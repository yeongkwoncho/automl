"""Job sources and a factory that builds them from config."""
import logging
from typing import List, Tuple

import httpx

from jobpilot.sources.ats import (AshbySource, GreenhouseSource, LeverSource,
                                  RecruiteeSource)
from jobpilot.sources.base import JobSource
from jobpilot.sources.workday import WorkdaySource
from jobpilot.sources.boards import (AdzunaSource, ArbeitnowSource,
                                     RemoteOKSource, RemotiveSource,
                                     SaraminSource, env_or_value)

logger = logging.getLogger(__name__)

__all__ = ['JobSource', 'build_sources', 'check_boards']

_BOARD_SOURCES = (('greenhouse', GreenhouseSource), ('lever', LeverSource),
                  ('ashby', AshbySource), ('recruitee', RecruiteeSource))


def build_sources(cfg: dict, http: httpx.Client) -> List[JobSource]:
  """Instantiates every source enabled in the `sources:` config section."""
  sources: List[JobSource] = []
  for name, cls in _BOARD_SOURCES:
    boards = (cfg.get(name) or {}).get('boards') or []
    if boards:
      sources.append(cls(http, boards))

  wd = cfg.get('workday') or {}
  if wd.get('sites'):
    sources.append(WorkdaySource(http, wd['sites'], wd.get('max_pages', 5)))

  if (cfg.get('remotive') or {}).get('enabled'):
    sources.append(RemotiveSource(http, cfg['remotive'].get('limit', 100)))
  if (cfg.get('remoteok') or {}).get('enabled'):
    sources.append(RemoteOKSource(http))
  if (cfg.get('arbeitnow') or {}).get('enabled'):
    sources.append(ArbeitnowSource(http, cfg['arbeitnow'].get('pages', 3)))

  adz = cfg.get('adzuna') or {}
  if adz.get('countries'):
    app_id, app_key = env_or_value(adz, 'app_id'), env_or_value(adz, 'app_key')
    if app_id and app_key:
      sources.append(
          AdzunaSource(http, adz['countries'], app_id, app_key,
                       adz.get('pages', 1)))
    else:
      logger.warning('adzuna configured but app_id/app_key missing; skipped')

  sar = cfg.get('saramin') or {}
  if sar:
    key = env_or_value(sar, 'access_key')
    if key:
      sources.append(SaraminSource(http, key))
    else:
      logger.warning('saramin configured but access_key missing; skipped')
  return sources


def check_boards(cfg: dict, http: httpx.Client) -> List[Tuple[str, str, str]]:
  """Fetches every configured company board once.

  Returns (source, board, result) rows where result is a job count or the
  error, so wrong slugs show up before a real run.
  """
  rows = []
  checks = [(name, cls(http, []), (cfg.get(name) or {}).get('boards'))
            for name, cls in _BOARD_SOURCES]
  checks.append(('workday', WorkdaySource(http, []), (cfg.get('workday') or
                                                      {}).get('sites')))
  for name, src, boards in checks:
    for board in boards or []:
      label = board if isinstance(board, str) else board.get('url', '')
      try:
        rows.append((name, label, src.check(board)))
      except httpx.HTTPStatusError as e:
        hint = ' (wrong slug?)' if e.response.status_code == 404 else ''
        rows.append((name, label, f'HTTP {e.response.status_code}{hint}'))
      except (httpx.HTTPError, ValueError, KeyError) as e:
        rows.append((name, label, f'error: {e}'))
  return rows
