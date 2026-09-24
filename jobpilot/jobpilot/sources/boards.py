"""Aggregator job boards with public APIs."""
import logging
import os
from typing import Iterator, List, Optional

import httpx

from jobpilot.models import Job, Preferences
from jobpilot.sources.base import JobSource, search_terms
from jobpilot.textutil import html_to_text, parse_datetime

logger = logging.getLogger(__name__)


def _detect_ats(url: str) -> Optional[str]:
  for host, ats in (('greenhouse.io', 'greenhouse'), ('lever.co', 'lever'),
                    ('ashbyhq.com', 'ashby')):
    if host in (url or ''):
      return ats
  return None


class RemotiveSource(JobSource):
  """https://remotive.com/api/remote-jobs (remote jobs, global)."""
  name = 'remotive'
  API = 'https://remotive.com/api/remote-jobs'

  def __init__(self, http, limit: int = 100):
    super().__init__(http)
    self.limit = limit

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    for term in search_terms(prefs):
      data = self._get_json(self.API, search=term, limit=self.limit)
      for j in data.get('jobs', []):
        yield Job(
            source=self.name,
            external_id=str(j['id']),
            title=j['title'],
            company=j['company_name'],
            url=j['url'],
            location=j.get('candidate_required_location', '') or 'Remote',
            remote=True,
            description=html_to_text(j.get('description', '')),
            posted_at=parse_datetime(j.get('publication_date')),
            employment_type=j.get('job_type'),
            tags=[j['category']] if j.get('category') else [],
        )


class RemoteOKSource(JobSource):
  """https://remoteok.com/api -- terms require linking back to RemoteOK."""
  name = 'remoteok'
  API = 'https://remoteok.com/api'

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    data = self._get_json(self.API)
    for j in data:
      if 'id' not in j or 'position' not in j:
        continue  # first element is the legal notice
      apply_url = j.get('apply_url') or j['url']
      yield Job(
          source=self.name,
          external_id=str(j['id']),
          title=j['position'],
          company=j.get('company', ''),
          url=j['url'],
          apply_url=apply_url,
          location=j.get('location', '') or 'Remote',
          remote=True,
          description=html_to_text(j.get('description', '')),
          posted_at=parse_datetime(j.get('epoch') or j.get('date')),
          salary_min=j.get('salary_min') or None,
          salary_max=j.get('salary_max') or None,
          salary_currency='USD' if j.get('salary_max') else None,
          tags=j.get('tags') or [],
          ats=_detect_ats(apply_url),
      )


class ArbeitnowSource(JobSource):
  """https://www.arbeitnow.com/api/job-board-api (Europe, many in Germany)."""
  name = 'arbeitnow'
  API = 'https://www.arbeitnow.com/api/job-board-api'

  def __init__(self, http, pages: int = 3):
    super().__init__(http)
    self.pages = pages

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    for page in range(1, self.pages + 1):
      data = self._get_json(self.API, page=page)
      for j in data.get('data', []):
        yield Job(
            source=self.name,
            external_id=j['slug'],
            title=j['title'],
            company=j['company_name'],
            url=j['url'],
            location=j.get('location', ''),
            remote=bool(j.get('remote')),
            description=html_to_text(j.get('description', '')),
            posted_at=parse_datetime(j.get('created_at')),
            employment_type=', '.join(j.get('job_types') or []) or None,
            tags=j.get('tags') or [],
        )
      if not (data.get('links') or {}).get('next'):
        break


class AdzunaSource(JobSource):
  """https://developer.adzuna.com -- needs a free app_id/app_key.

  Covers ~20 countries (us, gb, de, fr, ca, au, in, sg, ...).
  """
  name = 'adzuna'
  API = 'https://api.adzuna.com/v1/api/jobs/{country}/search/{page}'

  def __init__(self,
               http,
               countries: List[str],
               app_id: str,
               app_key: str,
               pages: int = 1,
               per_page: int = 50):
    super().__init__(http)
    self.countries = countries
    self.app_id, self.app_key = app_id, app_key
    self.pages, self.per_page = pages, per_page

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    wheres = [l for l in prefs.locations if l.lower() != 'remote'] or ['']
    for country in self.countries:
      for term in search_terms(prefs):
        for where in wheres:
          for page in range(1, self.pages + 1):
            params = dict(app_id=self.app_id,
                          app_key=self.app_key,
                          what=term,
                          results_per_page=self.per_page,
                          max_days_old=prefs.max_job_age_days)
            if where:
              params['where'] = where
            try:
              data = self._get_json(self.API.format(country=country, page=page),
                                    **params)
            except httpx.HTTPStatusError as e:
              logger.warning('adzuna %s %r: %s', country, term, e)
              break
            results = data.get('results', [])
            for j in results:
              yield Job(
                  source=self.name,
                  external_id=f'{country}/{j["id"]}',
                  title=html_to_text(j['title']),
                  company=(j.get('company') or {}).get('display_name', ''),
                  url=j['redirect_url'],
                  location=(j.get('location') or {}).get('display_name', ''),
                  description=html_to_text(j.get('description', '')),
                  posted_at=parse_datetime(j.get('created')),
                  employment_type=j.get('contract_time'),
                  salary_min=j.get('salary_min'),
                  salary_max=j.get('salary_max'),
              )
            if len(results) < self.per_page:
              break


class SaraminSource(JobSource):
  """https://oapi.saramin.co.kr (Korea) -- needs an access key."""
  name = 'saramin'
  API = 'https://oapi.saramin.co.kr/job-search'

  def __init__(self, http, access_key: str, count: int = 110):
    super().__init__(http)
    self.access_key = access_key
    self.count = count

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    for term in search_terms(prefs):
      data = self._get_json(
          self.API, **{
              'access-key': self.access_key,
              'keywords': term,
              'count': self.count,
              'sort': 'pd'
          })
      for j in (data.get('jobs') or {}).get('job', []):
        pos = j.get('position') or {}
        name = lambda d: (d or {}).get('name', '')
        yield Job(
            source=self.name,
            external_id=str(j['id']),
            title=pos.get('title', ''),
            company=name((j.get('company') or {}).get('detail')),
            url=j['url'],
            location=html_to_text(name(pos.get('location'))),
            description='\n'.join(
                filter(None, [
                    name(pos.get('industry')),
                    name(pos.get('job-mid-code')),
                    name(pos.get('experience-level')),
                    name(pos.get('required-education-level')),
                    j.get('keyword', ''),
                    name(j.get('salary'))
                ])),
            posted_at=parse_datetime(j.get('posting-timestamp')),
            employment_type=name(pos.get('job-type')) or None,
            tags=[t for t in (j.get('keyword') or '').split(',') if t],
        )


def env_or_value(cfg: dict, key: str) -> str:
  """Reads `key` from cfg, or from the env var named by `key_env`."""
  if cfg.get(key):
    return str(cfg[key])
  env = cfg.get(f'{key}_env')
  return os.environ.get(env, '') if env else ''
