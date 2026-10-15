"""search -> prefilter -> LLM match -> tailor -> apply, with safety limits."""
import concurrent.futures
import datetime as dt
import logging
import os
import time
from typing import Callable, Dict, List, Optional

import httpx

from jobpilot.agent import Matcher, Tailor
from jobpilot.appliers import load_packet, write_packet
from jobpilot.config import Config
from jobpilot.filters import prefilter
from jobpilot.llm import LLMError, StructuredLLM
from jobpilot.models import ApplyOutcome, CandidateProfile, Job
from jobpilot.sources import JobSource, build_sources
from jobpilot.store import Store

logger = logging.getLogger(__name__)


class Pipeline:

  def __init__(self,
               cfg: Config,
               profile: CandidateProfile,
               llm: StructuredLLM,
               store: Store,
               sources: Optional[List[JobSource]] = None,
               applier_factory: Optional[Callable] = None,
               sleep: Callable[[float], None] = time.sleep):
    self.cfg = cfg
    self.profile = profile
    self.store = store
    self.prefs = cfg.preferences
    self.matcher = Matcher(llm, profile, self.prefs, cfg.llm.match_effort)
    self.tailor = Tailor(llm, profile, self.prefs, cfg.llm.tailor_effort)
    self._sources = sources
    self._applier_factory = applier_factory
    self._sleep = sleep

  # 1. Search.
  def search(self) -> Dict[str, int]:
    sources = self._sources
    http = None
    if sources is None:
      http = httpx.Client(timeout=30, follow_redirects=True)
      sources = build_sources(self.cfg.sources, http)
    counts = {'fetched': 0, 'new': 0, 'passed': 0}
    try:
      for src in sources:
        try:
          for job in src.fetch(self.prefs):
            counts['fetched'] += 1
            result = prefilter(job, self.prefs)
            if self.store.add_job(job,
                                  None if result.passed else result.reason):
              counts['new'] += 1
              counts['passed'] += result.passed
        except httpx.HTTPError as e:
          logger.warning('source %s failed: %s', src.name, e)
    finally:
      if http:
        http.close()
    return counts

  # 2. Match.
  def match(self, limit: Optional[int] = None) -> Dict[str, int]:
    jobs = self.store.unscored_jobs()[:limit]
    counts = {'scored': 0, 'errors': 0}
    with concurrent.futures.ThreadPoolExecutor(self.cfg.llm.max_workers) as ex:
      futures = {ex.submit(self.matcher.assess, j): j for j in jobs}
      for fut in concurrent.futures.as_completed(futures):
        job = futures[fut]
        try:
          self.store.save_match(job.uid, fut.result())
          counts['scored'] += 1
        except LLMError as e:
          logger.warning('scoring %s failed: %s', job.uid, e)
          counts['errors'] += 1
    return counts

  # 3+4. Tailor and apply.
  def apply(self, limit: Optional[int] = None) -> Dict[str, int]:
    ac = self.cfg.apply
    now = dt.datetime.now(dt.timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    budget = ac.max_per_day - self.store.submitted_since(day_start)
    cooldown = now - dt.timedelta(days=ac.company_cooldown_days)
    counts: Dict[str, int] = {}
    output_dir = self.cfg.path(self.cfg.output_dir)
    applier = self._make_applier() if ac.mode != 'dry_run' else None
    processed = 0

    candidates = list(
        self.store.candidates(ac.min_score,
                              include_packets=ac.mode != 'dry_run'))
    for job, match, existing_dir in candidates:
      if limit is not None and processed >= limit:
        break
      if ac.mode != 'dry_run' and budget <= 0:
        logger.info('daily submission limit (%d) reached', ac.max_per_day)
        break
      if (ac.company_cooldown_days and
          self.store.applied_to_company_since(job.company, cooldown)):
        continue
      processed += 1
      packet = load_packet(existing_dir) if existing_dir else None
      packet_dir = existing_dir
      if packet is None:
        try:
          packet = self.tailor.tailor(job, match)
        except LLMError as e:
          logger.warning('tailoring %s failed: %s', job.uid, e)
          counts['failed'] = counts.get('failed', 0) + 1
          continue
        packet_dir = write_packet(output_dir, job, match, packet)

      if applier is None:
        outcome = ApplyOutcome(status='packet_ready', detail='dry run')
      else:
        outcome = self._apply_one(applier, job, packet, packet_dir)
      self.store.record_application(job, outcome, packet_dir)
      counts[outcome.status] = counts.get(outcome.status, 0) + 1
      logger.info('%s: %s @ %s (%s) %s', outcome.status, job.title, job.company,
                  match.score, outcome.detail)
      if outcome.status == 'submitted':
        budget -= 1
        if ac.delay_seconds:
          self._sleep(ac.delay_seconds)
    return counts

  def run(self) -> Dict[str, Dict[str, int]]:
    return {
        'search': self.search(),
        'match': self.match(),
        'apply': self.apply()
    }

  def _make_applier(self):
    if self._applier_factory:
      return self._applier_factory(self.cfg.apply)
    from jobpilot.appliers.browser import BrowserApplier  # pylint: disable=g-import-not-at-top
    return BrowserApplier(mode=self.cfg.apply.mode,
                          headless=self.cfg.apply.headless,
                          executable_path=self.cfg.apply.chromium_path)

  def _apply_one(self, applier, job: Job, packet, packet_dir) -> ApplyOutcome:
    answer_fn = lambda qs: self.tailor.answer(job, qs)
    if not self.profile.resume_path or not os.path.exists(
        self.profile.resume_path):
      return ApplyOutcome(status='needs_manual',
                          detail='resume_path missing; cannot upload resume')
    try:
      return applier.apply(job, packet, self.profile, packet_dir, answer_fn)
    except LLMError as e:
      return ApplyOutcome(status='failed', detail=str(e))
