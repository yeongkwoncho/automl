"""Apple's own careers site (jobs.apple.com).

Apple does not use a third-party ATS and publishes no job API. This reads
the JSON the public search page calls: it fetches a CSRF token, then
POSTs searches to /api/role/search. Fields are read defensively because
the format is undocumented; `jobpilot check-boards` shows whether it
still works.
"""
import datetime as dt
import logging
import time
from typing import Callable, Iterator, Optional

import httpx

from jobpilot.models import Job, Preferences
from jobpilot.sources.base import USER_AGENT, JobSource, describe_error, search_terms
from jobpilot.textutil import html_to_text, parse_datetime

logger = logging.getLogger(__name__)

BASE = 'https://jobs.apple.com'
PAGE_SIZE = 20  # what the site returns per page


def _parse_date(value) -> Optional[dt.datetime]:
  """Apple shows dates like 'Nov 03, 2026'; ISO strings also accepted."""
  if not value:
    return None
  for fmt in ('%b %d, %Y', '%B %d, %Y'):
    try:
      return dt.datetime.strptime(str(value).strip(),
                                  fmt).replace(tzinfo=dt.timezone.utc)
    except ValueError:
      pass
  return parse_datetime(value)


class AppleSource(JobSource):
  name = 'apple'

  def __init__(self,
               http: httpx.Client,
               locale: str = 'en-us',
               max_pages: int = 5,
               delay: float = 0.5,
               sleep: Callable[[float], None] = time.sleep):
    super().__init__(http)
    self.locale = locale
    self.max_pages = max_pages
    self.delay = delay
    self._sleep = sleep
    self._token: Optional[str] = None
    self.boards = ['jobs.apple.com']
    self.errors = []

  def _csrf(self) -> str:
    if self._token is None:
      resp = self.http.get(f'{BASE}/api/csrfToken',
                           headers={'User-Agent': USER_AGENT})
      resp.raise_for_status()
      self._token = resp.headers.get('x-apple-csrf-token', '')
    return self._token

  def _search(self, term: str, page: int) -> dict:
    if self.delay:
      self._sleep(self.delay)
    resp = self.http.post(f'{BASE}/api/role/search',
                          json={
                              'query': term,
                              'filters': {},
                              'page': page,
                              'locale': self.locale,
                              'sort': 'newest'
                          },
                          headers={
                              'User-Agent': USER_AGENT,
                              'Accept': 'application/json',
                              'x-apple-csrf-token': self._csrf()
                          })
    resp.raise_for_status()
    data = resp.json()
    return data.get('res', data) if isinstance(data, dict) else {}

  def check(self, _board=None) -> str:
    data = self._search('', 1)
    return f'{data.get("totalRecords", 0)} jobs (Apple)'

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    self.errors = []
    seen = set()
    try:
      for term in search_terms(prefs):
        for page in range(1, self.max_pages + 1):
          data = self._search(term, page)
          results = data.get('searchResults') or []
          for r in results:
            job = self._job(r)
            if job and job.external_id not in seen:
              seen.add(job.external_id)
              yield job
          total = int(data.get('totalRecords') or 0)
          if len(results) < PAGE_SIZE or page * PAGE_SIZE >= total:
            break
    except (httpx.HTTPError, ValueError, KeyError) as e:
      self.errors.append(('jobs.apple.com', describe_error(e)))
      logger.warning('apple failed: %s', describe_error(e))

  def _job(self, r: dict) -> Optional[Job]:
    pid = r.get('positionId') or r.get('id')
    title = r.get('postingTitle') or r.get('title')
    if not pid or not title:
      return None
    slug = r.get('transformedPostingTitle') or ''
    url = f'{BASE}/{self.locale}/details/{pid}' + (f'/{slug}' if slug else '')
    locs = r.get('locations') or []
    location = '; '.join(
        filter(None, (l.get('name') or l.get('city') or l.get('countryName')
                      for l in locs
                      if isinstance(l, dict))))
    team = (r.get('team') or {}).get('teamName') if isinstance(
        r.get('team'), dict) else None
    return Job(
        source=self.name,
        external_id=str(pid),
        title=title,
        company='Apple',
        url=url,
        apply_url=url,
        location=location,
        remote=bool(r.get('homeOffice')) or None,
        description=html_to_text(r.get('jobSummary') or ''),
        posted_at=_parse_date(r.get('postingDate') or r.get('postDateInGMT')),
        tags=[team] if team else [],
    )
