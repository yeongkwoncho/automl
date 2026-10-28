"""Eightfold AI career sites (e.g. careers.qualcomm.com).

Eightfold has no documented public job API. This reads the JSON the
public career site itself calls. Two generations exist:
  - /api/apply/v2/jobs           (older "PCS" sites)
  - /api/pcsx/search             (newer sites; job URLs carry ?hl=...)
Each site is probed once per run and the working one is used. Fields are
read defensively because the response shape is not documented.
"""
import logging
import time
from typing import Callable, Iterator, List, NamedTuple, Union
from urllib.parse import urlparse

import httpx

from jobpilot.models import Job, Preferences
from jobpilot.sources.ats import _BoardSource
from jobpilot.sources.base import USER_AGENT, search_terms
from jobpilot.textutil import html_to_text, parse_datetime

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


class EightfoldSite(NamedTuple):
  base: str  # https://careers.qualcomm.com
  domain: str  # qualcomm.com
  company: str


def parse_site(entry: Union[str, dict]) -> EightfoldSite:
  """Accepts a career-site URL, or {url:, domain:, company:}."""
  url = entry if isinstance(entry, str) else entry['url']
  u = urlparse(url if '://' in url else f'https://{url}')
  if not u.hostname:
    raise ValueError(f'not a URL: {url}')
  opts = {} if isinstance(entry, str) else entry
  # careers.qualcomm.com -> qualcomm.com
  domain = opts.get('domain') or '.'.join(u.hostname.split('.')[-2:])
  company = opts.get('company') or domain.split('.')[0].title()
  return EightfoldSite(f'https://{u.hostname}', domain, company)


def _first(d: dict, *keys, default=None):
  for k in keys:
    v = d.get(k)
    if v not in (None, '', []):
      return v
  return default


def _location(p: dict) -> str:
  locs = _first(p, 'locations', 'standardizedLocations', default=[])
  loc = _first(p, 'location', default='')
  if not loc and locs:
    loc = '; '.join(
        l if isinstance(l, str) else str(l.get('name', '')) for l in locs[:3])
  return loc


class EightfoldSource(_BoardSource):
  name = 'eightfold'

  def __init__(self,
               http: httpx.Client,
               sites: List[Union[str, dict]],
               max_pages: int = 10,
               delay: float = 0.5,
               sleep: Callable[[float], None] = time.sleep):
    super().__init__(http, sites)
    self.max_pages = max_pages
    self.delay = delay
    self._sleep = sleep

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    wanted = [w.lower() for w in prefs.titles + prefs.keywords]
    self.errors = []
    for entry in self.boards:
      try:
        yield from self._fetch_site(parse_site(entry), prefs, wanted)
      except (httpx.HTTPError, ValueError, KeyError) as e:
        self._failed(entry, e)

  def fetch_board(self, board):
    return self._fetch_site(parse_site(board), Preferences(), [])

  def check(self, board) -> str:
    site = parse_site(board)
    api, data = self._probe(site)
    return f'{self._count(data)} jobs ({site.company}, {api} API)'

  # HTTP helpers.
  def _get(self, url: str, **params) -> dict:
    if self.delay:
      self._sleep(self.delay)
    resp = self.http.get(url,
                         params=params,
                         headers={
                             'User-Agent': USER_AGENT,
                             'Accept': 'application/json'
                         })
    resp.raise_for_status()
    return resp.json()

  def _search(self, site, api: str, term: str, start: int) -> dict:
    if api == 'v2':
      return self._get(f'{site.base}/api/apply/v2/jobs',
                       domain=site.domain,
                       start=start,
                       num=PAGE_SIZE,
                       query=term,
                       sort_by='relevance')
    return self._get(f'{site.base}/api/pcsx/search',
                     domain=site.domain,
                     start=start,
                     num=PAGE_SIZE,
                     query=term,
                     location='',
                     sort_by='relevance')

  def _probe(self, site):
    """Finds which API generation the site serves."""
    last = None
    for api in ('v2', 'pcsx'):
      try:
        return api, self._search(site, api, '', 0)
      except httpx.HTTPStatusError as e:
        last = e
        if e.response.status_code not in (400, 403, 404, 405):
          raise
    raise last

  @staticmethod
  def _positions(data: dict) -> List[dict]:
    inner = data.get('data') if isinstance(data.get('data'), dict) else data
    return inner.get('positions') or []

  @staticmethod
  def _count(data: dict) -> int:
    inner = data.get('data') if isinstance(data.get('data'), dict) else data
    return int(inner.get('count') or len(inner.get('positions') or []))

  def _detail(self, site, api: str, pid) -> dict:
    try:
      if api == 'v2':
        return self._get(f'{site.base}/api/apply/v2/jobs/{pid}',
                         domain=site.domain)
      data = self._get(f'{site.base}/api/pcsx/position_details',
                       position_id=pid,
                       domain=site.domain,
                       hl='en')
      return data.get('data') if isinstance(data.get('data'), dict) else data
    except httpx.HTTPStatusError as e:
      logger.debug('eightfold detail %s failed: %s', pid, e)
      return {}

  def _fetch_site(self, site, prefs: Preferences,
                  wanted: List[str]) -> Iterator[Job]:
    api, _ = self._probe(site)
    seen = set()
    for term in search_terms(prefs):
      for page in range(self.max_pages):
        data = self._search(site, api, term, page * PAGE_SIZE)
        positions = self._positions(data)
        for p in positions:
          pid = p.get('id')
          if pid is None or pid in seen:
            continue
          seen.add(pid)
          yield self._job(site, api, p, wanted)
        if (len(positions) < PAGE_SIZE or
            (page + 1) * PAGE_SIZE >= self._count(data)):
          break

  def _job(self, site, api: str, p: dict, wanted: List[str]) -> Job:
    pid = p['id']
    title = _first(p, 'name', 'title', default='')
    description = _first(p, 'job_description', 'jobDescription', default='')
    # One extra request per job for the description; skip titles the
    # prefilter will drop anyway.
    if not description and (not wanted or
                            any(w in title.lower() for w in wanted)):
      d = self._detail(site, api, pid)
      description = _first(d, 'job_description', 'jobDescription', default='')
    url = _first(p,
                 'canonicalPositionUrl',
                 'positionUrl',
                 default=f'/careers/job/{pid}')
    if url.startswith('/'):
      url = site.base + url
    location = _location(p)
    work = str(
        _first(p, 'work_location_option', 'workLocationOption',
               default='')).lower()
    posted = _first(p, 't_create', 'postedTs', 'creationTs')
    return Job(
        source=self.name,
        external_id=f'{site.domain}/{pid}',
        title=title,
        company=site.company,
        url=url,
        apply_url=url,
        location=location,
        remote=('remote' in work or 'remote' in location.lower()) or None,
        description=html_to_text(description),
        posted_at=parse_datetime(posted),
        employment_type=_first(p, 'type', 'employmentType'),
        tags=[t for t in [_first(p, 'department')] if t],
        ats='eightfold',
    )
