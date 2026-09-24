"""Job sources and a factory that builds them from config."""
import logging
from typing import List

import httpx

from jobpilot.sources.ats import AshbySource, GreenhouseSource, LeverSource
from jobpilot.sources.base import JobSource
from jobpilot.sources.boards import (AdzunaSource, ArbeitnowSource,
                                     RemoteOKSource, RemotiveSource,
                                     SaraminSource, env_or_value)

logger = logging.getLogger(__name__)

__all__ = ['JobSource', 'build_sources']


def build_sources(cfg: dict, http: httpx.Client) -> List[JobSource]:
  """Instantiates every source enabled in the `sources:` config section."""
  sources: List[JobSource] = []
  for name, cls in (('greenhouse', GreenhouseSource), ('lever', LeverSource),
                    ('ashby', AshbySource)):
    boards = (cfg.get(name) or {}).get('boards') or []
    if boards:
      sources.append(cls(http, boards))

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
