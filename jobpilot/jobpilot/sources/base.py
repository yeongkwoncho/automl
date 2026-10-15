"""Job source interface."""
import abc
import logging
from typing import Iterator, List

import httpx

from jobpilot.models import Job, Preferences

logger = logging.getLogger(__name__)

USER_AGENT = 'JobPilot/0.1 (+https://github.com/yeongkwoncho/automl)'


class JobSource(abc.ABC):
  """Fetches postings from one public job API.

  Sources only talk to official/public APIs. Sites whose terms forbid
  automated access (e.g. LinkedIn, Indeed) are intentionally not supported.
  """
  name: str = ''

  def __init__(self, http: httpx.Client):
    self.http = http

  @abc.abstractmethod
  def fetch(self, prefs: Preferences) -> Iterator[Job]:
    """Yields jobs. May use `prefs` to narrow server-side search."""

  def _get_json(self, url: str, **params):
    resp = self.http.get(url,
                         params=params or None,
                         headers={'User-Agent': USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def search_terms(prefs: Preferences) -> List[str]:
  """Queries to send to keyword-search APIs."""
  terms = list(dict.fromkeys(prefs.titles + prefs.keywords))
  return terms or ['']
