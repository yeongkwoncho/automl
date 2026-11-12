"""Company career boards hosted on Greenhouse, Lever and Ashby.

All three expose public, unauthenticated job-board APIs. You list the
companies you care about (their board token / slug) in the config.
"""
import logging
from typing import Iterator, List

import httpx

from jobpilot.models import Job, Preferences
from jobpilot.sources.base import USER_AGENT, JobSource, describe_error
from jobpilot.textutil import html_to_text, parse_datetime

logger = logging.getLogger(__name__)


class _BoardSource(JobSource):
  """Iterates a list of company boards, isolating per-board failures."""

  def __init__(self, http: httpx.Client, boards: List[str]):
    super().__init__(http)
    self.boards = boards
    # (board, error) for boards that failed in the last fetch(), so callers
    # can tell "no openings" apart from "could not reach the board".
    self.errors: List[tuple] = []

  def _failed(self, board, e: Exception):
    label = board if isinstance(board, str) else board.get('url', board)
    msg = describe_error(e)
    self.errors.append((label, msg))
    logger.warning('%s board %s failed: %s', self.name, label, msg)

  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    self.errors = []
    for board in self.boards:
      try:
        yield from self.fetch_board(board)
      except (httpx.HTTPError, ValueError, KeyError) as e:
        self._failed(board, e)

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


class PinpointSource(_BoardSource):
  """Pinpoint career sites (<board>.pinpointhq.com), common in the UK.

  Reads the public postings feed: /postings.json, then /jobs.rss if the
  JSON one is unavailable. Field names are not documented to us, so
  several layouts are accepted; `check-boards` reports which feed worked.
  """
  name = 'pinpoint'
  HOST = 'https://{board}.pinpointhq.com'

  def _feed(self, board: str):
    """Returns ('json', postings) or ('rss', postings) as dicts."""
    base = self.HOST.format(board=board)
    try:
      data = self._get_json(base + '/postings.json')
      items = data.get('data', data) if isinstance(data, dict) else data
      return 'json', [_pinpoint_json(p, base) for p in items or []]
    except (httpx.HTTPStatusError, ValueError) as e:
      if isinstance(
          e, httpx.HTTPStatusError) and e.response.status_code not in (400, 404,
                                                                       406):
        raise
    resp = self.http.get(base + '/jobs.rss', headers={'User-Agent': USER_AGENT})
    resp.raise_for_status()
    return 'rss', _pinpoint_rss(resp.text, base)

  def check(self, board: str) -> str:
    kind, postings = self._feed(board)
    return f'{len(postings)} jobs ({kind} feed)'

  def fetch_board(self, board: str) -> Iterator[Job]:
    _, postings = self._feed(board)
    for p in postings:
      if not p.get('title') or not p.get('url'):
        continue
      yield Job(
          source=self.name,
          external_id=f'{board}/{p["id"]}',
          title=p['title'],
          company=p.get('company') or board,
          url=p['url'],
          apply_url=p['url'],
          location=p.get('location', ''),
          remote=p.get('remote'),
          description=p.get('description', ''),
          posted_at=p.get('posted_at'),
          employment_type=p.get('employment_type'),
          tags=[t for t in [p.get('department')] if t],
          ats='pinpoint',
      )


def _first(d: dict, *keys):
  for k in keys:
    v = d.get(k)
    if v not in (None, '', []):
      return v
  return None


def _text(v) -> str:
  """A string from a value that may be a string or a {name|title: ...}."""
  if isinstance(v, dict):
    v = _first(v, 'name', 'title', 'label', 'text')
  return str(v).strip() if v not in (None, '') else ''


def _pinpoint_json(p: dict, base: str) -> dict:
  p = p.get('attributes', p) if isinstance(p.get('attributes'), dict) else p
  pid = _first(p, 'id', 'uuid') or _first(p, 'path', 'url') or ''
  url = _first(p, 'url', 'path', 'link') or ''
  if url.startswith('/'):
    url = base + url
  if not url and pid:
    url = f'{base}/postings/{pid}'
  loc = p.get('location')
  if isinstance(loc, dict):
    loc = ', '.join(x for x in (_text(loc.get('city')),
                                _text(loc.get('province') or loc.get('region')),
                                _text(loc.get('name'))) if x) or _text(loc)
  workplace = str(_first(p, 'workplace_type', 'workplaceType') or '').lower()
  desc = '\n\n'.join(
      html_to_text(str(p[k]))
      for k in ('description', 'key_responsibilities',
                'skills_knowledge_expertise', 'benefits', 'requirements')
      if p.get(k))
  return {
      'id':
          str(pid).rstrip('/').split('/')[-1],
      'title':
          _text(_first(p, 'title', 'name')),
      'url':
          url,
      'location':
          _text(loc),
      'remote': ('remote' in workplace or 'hybrid' in workplace) or None,
      'description':
          desc,
      'employment_type':
          _text(_first(p, 'employment_type', 'employmentType')),
      'department':
          _text(_first(p, 'department', 'job_department')),
      'company':
          _text(_first(p, 'company', 'company_name')),
      'posted_at':
          parse_datetime(_first(p, 'published_at', 'publishedAt',
                                'created_at')),
  }


def _pinpoint_rss(xml: str, base: str) -> list:
  import xml.etree.ElementTree as ET  # pylint: disable=g-import-not-at-top
  root = ET.fromstring(xml)
  out = []
  for item in root.iter('item'):
    get = lambda tag: (item.findtext(tag) or '').strip()
    link = get('link') or get('guid')
    out.append({
        'id': link.rstrip('/').split('/')[-1] or get('guid'),
        'title': get('title'),
        'url': link,
        'location': get('location') or get('{*}location'),
        'description': html_to_text(get('description')),
        'posted_at': _rss_date(get('pubDate')),
        'department': get('category'),
    })
  return out


def _rss_date(value: str):
  from email.utils import parsedate_to_datetime  # pylint: disable=g-import-not-at-top
  try:
    return parsedate_to_datetime(value) if value else None
  except (TypeError, ValueError):
    return None
