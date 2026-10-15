"""Company career boards hosted on Greenhouse, Lever and Ashby.

All three expose public, unauthenticated job-board APIs. You list the
companies you care about (their board token / slug) in the config.
"""
import logging
from typing import Iterator, List

import httpx

from jobpilot.models import Job, Preferences
from jobpilot.sources.base import JobSource
from jobpilot.textutil import html_to_text, parse_datetime

logger = logging.getLogger(__name__)


class _BoardSource(JobSource):
  """Iterates a list of company boards, isolating per-board failures."""

  def __init__(self, http: httpx.Client, boards: List[str]):
    super().__init__(http)
    self.boards = boards

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    for board in self.boards:
      try:
        yield from self.fetch_board(board)
      except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning('%s board %r failed: %s', self.name, board, e)

  def fetch_board(self, board: str) -> Iterator[Job]:
    raise NotImplementedError

  def check(self, board: str) -> str:
    """One-line health check used by `jobpilot check-boards`."""
    return f'{sum(1 for _ in self.fetch_board(board))} jobs'


class GreenhouseSource(_BoardSource):
  name = 'greenhouse'
  API = 'https://boards-api.greenhouse.io/v1/boards/{board}/jobs'

  def fetch_board(self, board):
    data = self._get_json(self.API.format(board=board), content='true')
    company = board
    for j in data.get('jobs', []):
      company = j.get('company_name') or company
      location = (j.get('location') or {}).get('name', '')
      yield Job(
          source=self.name,
          external_id=f'{board}/{j["id"]}',
          title=j['title'],
          company=company,
          url=j['absolute_url'],
          apply_url=j['absolute_url'],
          location=location,
          remote='remote' in location.lower() or None,
          description=html_to_text(j.get('content', '')),
          posted_at=parse_datetime(
              j.get('first_published') or j.get('updated_at')),
          ats='greenhouse',
      )


class LeverSource(_BoardSource):
  name = 'lever'
  API = 'https://api.lever.co/v0/postings/{board}'

  def fetch_board(self, board):
    data = self._get_json(self.API.format(board=board), mode='json')
    for j in data:
      cats = j.get('categories') or {}
      parts = [j.get('descriptionPlain', '')]
      for lst in j.get('lists') or []:
        parts.append(
            f'{lst.get("text", "")}\n{html_to_text(lst.get("content", ""))}')
      parts.append(j.get('additionalPlain', ''))
      salary = j.get('salaryRange') or {}
      yield Job(
          source=self.name,
          external_id=f'{board}/{j["id"]}',
          title=j['text'],
          company=board,
          url=j['hostedUrl'],
          apply_url=j.get('applyUrl') or j['hostedUrl'].rstrip('/') + '/apply',
          location=cats.get('location', '') or '',
          remote=(j.get('workplaceType') == 'remote') or None,
          description='\n\n'.join(p for p in parts if p).strip(),
          posted_at=parse_datetime(j.get('createdAt')),
          employment_type=cats.get('commitment'),
          salary_min=salary.get('min'),
          salary_max=salary.get('max'),
          salary_currency=salary.get('currency'),
          tags=[t for t in [cats.get('team'),
                            cats.get('department')] if t],
          ats='lever',
      )


class AshbySource(_BoardSource):
  name = 'ashby'
  API = 'https://api.ashbyhq.com/posting-api/job-board/{board}'

  def fetch_board(self, board):
    data = self._get_json(self.API.format(board=board),
                          includeCompensation='true')
    for j in data.get('jobs', []):
      if j.get('isListed') is False:
        continue
      yield Job(
          source=self.name,
          external_id=f'{board}/{j["id"]}',
          title=j['title'],
          company=board,
          url=j['jobUrl'],
          apply_url=j.get('applyUrl') or j['jobUrl'],
          location=j.get('location', '') or '',
          remote=j.get('isRemote'),
          description=j.get('descriptionPlain') or
          html_to_text(j.get('descriptionHtml', '')),
          posted_at=parse_datetime(j.get('publishedAt')),
          employment_type=j.get('employmentType'),
          tags=[
              t
              for t in [j.get('department'), j.get('team')]
              if t
          ],
          ats='ashby',
      )


class RecruiteeSource(_BoardSource):
  """Recruitee career sites (<board>.recruitee.com), common in the EU."""
  name = 'recruitee'
  API = 'https://{board}.recruitee.com/api/offers/'

  def fetch_board(self, board):
    data = self._get_json(self.API.format(board=board))
    for j in data.get('offers', []):
      if j.get('status') not in (None, 'published'):
        continue
      location = j.get('location') or ', '.join(
          p for p in [j.get('city'), j.get('country')] if p)
      url = j.get('careers_url') or (
          f'https://{board}.recruitee.com/o/{j["slug"]}')
      description = '\n\n'.join(
          html_to_text(j.get(k, '')) for k in ('description', 'requirements'))
      yield Job(
          source=self.name,
          external_id=f'{board}/{j["id"]}',
          title=j['title'],
          company=j.get('company_name') or board,
          url=url,
          apply_url=j.get('careers_apply_url') or url.rstrip('/') + '/c/new',
          location=location,
          remote=j.get('remote') or None,
          description=description.strip(),
          posted_at=parse_datetime(
              j.get('published_at') or j.get('created_at')),
          employment_type=j.get('employment_type_code'),
          tags=[j['department']] if j.get('department') else [],
          ats='recruitee',
      )
