"""Cheap rule-based prefilter that runs before any LLM call."""
import datetime as dt
import re
from typing import NamedTuple, Optional

from jobpilot.models import Job, Preferences


class FilterResult(NamedTuple):
  passed: bool
  reason: str = ''


def _contains_phrase(text: str, phrase: str) -> bool:
  phrase = phrase.strip().lower()
  if not phrase:
    return False
  return re.search(r'(?<![a-z0-9])' + re.escape(phrase) + r'(?![a-z0-9])',
                   text.lower()) is not None


def prefilter(job: Job,
              prefs: Preferences,
              now: Optional[dt.datetime] = None) -> FilterResult:
  """Rejects obviously irrelevant jobs so the LLM only sees plausible ones."""
  now = now or dt.datetime.now(dt.timezone.utc)
  company = job.company.lower()
  if any(c.lower() == company for c in prefs.exclude_companies):
    return FilterResult(False, 'excluded company')

  haystack = f'{job.title}\n{job.description}'
  for kw in prefs.exclude_keywords:
    if _contains_phrase(haystack, kw):
      return FilterResult(False, f'excluded keyword: {kw}')

  wanted = prefs.titles + prefs.keywords
  if wanted and not any(_contains_phrase(job.title, w) for w in wanted):
    return FilterResult(False, 'title does not match')

  if job.posted_at and prefs.max_job_age_days:
    if now - job.posted_at > dt.timedelta(days=prefs.max_job_age_days):
      return FilterResult(False, 'too old')

  loc = job.location.lower()
  is_remote = bool(job.remote) or 'remote' in loc
  if is_remote:
    if not prefs.remote_ok:
      return FilterResult(False, 'remote not wanted')
  else:
    if not prefs.onsite_ok:
      return FilterResult(False, 'not remote')
    onsite_locs = [l for l in prefs.locations if l.lower() != 'remote']
    if loc and onsite_locs and not any(l.lower() in loc for l in onsite_locs):
      return FilterResult(False, f'location: {job.location}')

  if (prefs.min_salary and job.salary_max and
      (not prefs.salary_currency or not job.salary_currency or
       prefs.salary_currency.upper() == job.salary_currency.upper())):
    if job.salary_max < prefs.min_salary:
      return FilterResult(False, 'salary below minimum')

  return FilterResult(True)
