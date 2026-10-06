"""Workday career sites (<tenant>.wd<N>.myworkdayjobs.com/<site>).

Many large semiconductor and metrology companies (KLA, Micron, Lam
Research, Thermo Fisher, ...) hire through Workday. Workday has no
documented public job API; this uses the JSON endpoints the public career
site itself calls. Requests are throttled, and the source only runs for
the sites you list.
"""
import datetime as dt
import logging
import re
import time
from typing import Callable, Iterator, List, NamedTuple, Optional, Union
from urllib.parse import urlparse

import httpx

from jobpilot.models import Job, Preferences
from jobpilot.sources.ats import _BoardSource
from jobpilot.sources.base import USER_AGENT, search_terms
from jobpilot.textutil import html_to_text, parse_datetime

logger = logging.getLogger(__name__)

PAGE_SIZE = 20  # Workday rejects larger pages.
_LOCALE = re.compile(r'^[a-z]{2}-[A-Z]{2}$')


class WorkdaySite(NamedTuple):
  base: str  # https://kla.wd1.myworkdayjobs.com
  tenant: str  # kla
  site: str  # Search
  company: str

  @property
  def api(self) -> str:
    return f'{self.base}/wday/cxs/{self.tenant}/{self.site}'


def parse_site(entry: Union[str, dict]) -> WorkdaySite:
  """Accepts a career-site URL, or {url: ..., company: ...}."""
  url = entry if isinstance(entry, str) else entry['url']
  company = '' if isinstance(entry, str) else entry.get('company', '')
  u = urlparse(url)
  if not u.hostname or 'myworkdayjobs.com' not in u.hostname:
    raise ValueError(f'not a Workday career site URL: {url}')
  parts = [p for p in u.path.split('/') if p and not _LOCALE.match(p)]
  if not parts:
    raise ValueError(f'Workday URL has no site name: {url}')
  tenant = u.hostname.split('.')[0]
  return WorkdaySite(f'https://{u.hostname}', tenant, parts[0], company or
                     tenant)


def _posted_relative(text: str, now: dt.datetime) -> Optional[dt.datetime]:
  """Parses 'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago'."""
  text = (text or '').lower()
  if 'today' in text:
    return now
  if 'yesterday' in text:
    return now - dt.timedelta(days=1)
  m = re.search(r'(\d+)\+?\s*days?', text)
  return now - dt.timedelta(days=int(m.group(1))) if m else None


class WorkdaySource(_BoardSource):
  name = 'workday'

  def __init__(self,
               http: httpx.Client,
               sites: List[Union[str, dict]],
               max_pages: int = 5,
               delay: float = 0.5,
               sleep: Callable[[float], None] = time.sleep):
    super().__init__(http, sites)
    self.max_pages = max_pages
    self.delay = delay
    self._sleep = sleep

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    wanted = [w.lower() for w in prefs.titles + prefs.keywords]
    for entry in self.boards:
      try:
        yield from self._fetch_site(parse_site(entry), prefs, wanted)
      except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning('workday site %r failed: %s', entry, e)

  def fetch_board(self, board):
    return self._fetch_site(parse_site(board), Preferences(), [])

  def check(self, board) -> str:
    site = parse_site(board)
    data = self._search(site, '', 0)
    return f'{data.get("total", 0)} jobs ({site.company})'

  def _request(self, method: str, url: str, **kw):
    if self.delay:
      self._sleep(self.delay)
    resp = self.http.request(method,
                             url,
                             headers={
                                 'User-Agent': USER_AGENT,
                                 'Accept': 'application/json'
                             },
                             **kw)
    resp.raise_for_status()
    return resp.json()

  def _search(self, site: WorkdaySite, term: str, offset: int) -> dict:
    return self._request('POST',
                         f'{site.api}/jobs',
                         json={
                             'appliedFacets': {},
                             'limit': PAGE_SIZE,
                             'offset': offset,
                             'searchText': term
                         })

  def _fetch_site(self, site: WorkdaySite, prefs: Preferences,
                  wanted: List[str]) -> Iterator[Job]:
    seen = set()
    for term in search_terms(prefs):
      total = None
      for page in range(self.max_pages):
        data = self._search(site, term, page * PAGE_SIZE)
        # Workday only reports the total on the first page.
        total = data.get('total') or total or 0
        postings = data.get('jobPostings') or []
        for p in postings:
          path = p.get('externalPath')
          if not path or path in seen:
            continue
          seen.add(path)
          yield self._job(site, p, wanted)
        if not postings or (page + 1) * PAGE_SIZE >= total:
          break

  def _job(self, site: WorkdaySite, p: dict, wanted: List[str]) -> Job:
    path = p['externalPath']
    title = p.get('title', '')
    info = {}
    # Descriptions need one request per job; skip ones the prefilter will
    # drop on title anyway.
    if not wanted or any(w in title.lower() for w in wanted):
      info = self._request('GET', site.api + path).get('jobPostingInfo') or {}
    now = dt.datetime.now(dt.timezone.utc)
    url = info.get('externalUrl') or f'{site.base}/{site.site}{path}'
    location = info.get('location') or p.get('locationsText', '')
    remote_type = (info.get('remoteType') or '').lower()
    req_id = info.get('jobReqId') or (p.get('bulletFields') or [path])[0]
    return Job(
        source=self.name,
        external_id=f'{site.tenant}/{req_id}',
        title=info.get('title') or title,
        company=site.company,
        url=url,
        apply_url=url.rstrip('/') + '/apply',
        location=location,
        remote=('remote' in remote_type or 'remote' in location.lower()) or
        None,
        description=html_to_text(info.get('jobDescription', '')),
        posted_at=parse_datetime(info.get('startDate')) or
        _posted_relative(p.get('postedOn', ''), now),
        employment_type=info.get('timeType'),
        ats='workday',
    )
