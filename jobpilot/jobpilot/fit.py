"""Free, local estimate of how well a job fits the master CV.

Claude's `match` score is the real assessment but costs an API call per
job. This estimate runs on every collected job with no network, so the
whole list can be ranked first and Claude spent on the top of it.
"""
import re
from typing import Iterable, List

from jobpilot.models import CandidateProfile, Job, Preferences

_WORD = r'(?<![a-z0-9+#]){}(?![a-z0-9+#])'


def _has(text: str, term: str) -> bool:
  term = term.strip().lower()
  return bool(term) and re.search(_WORD.format(re.escape(term)),
                                  text) is not None


def _terms(values: Iterable[str]) -> List[str]:
  seen, out = set(), []
  for v in values:
    v = (v or '').strip().lower()
    if len(v) >= 2 and v not in seen:
      seen.add(v)
      out.append(v)
  return out


def quick_fit(profile: CandidateProfile, prefs: Preferences, job: Job) -> int:
  """0-100: title relevance (40) + share of CV skills in the posting (60)."""
  title = job.title.lower()
  text = f'{title}\n{job.description.lower()}\n{" ".join(job.tags).lower()}'

  wanted = _terms(prefs.titles + prefs.keywords +
                  [e.title for e in profile.experience])
  title_pts = 40 if any(_has(title, w) for w in wanted) else (20 if any(
      _has(text, w) for w in wanted) else 0)

  skills = _terms(profile.skills)
  if not skills:
    return title_pts
  hits = sum(1 for s in skills if _has(text, s))
  # Postings rarely list more than a handful of a CV's skills: 6 matches
  # (or all of a short skill list) counts as full marks.
  need = max(1, min(6, len(skills)))
  return round(title_pts + 60 * min(1.0, hits / need))
