import os

from conftest import FakeLLM, make_job
from jobpilot.config import Config
from jobpilot.models import ApplyOutcome
from jobpilot.pipeline import Pipeline
from jobpilot.store import Store


class ListSource:
  name = 'fake'

  def __init__(self, jobs):
    self.jobs = jobs

  def fetch(self, prefs):
    return iter(self.jobs)


class FakeApplier:

  def __init__(self, status='submitted'):
    self.status = status
    self.applied = []

  def apply(self, job, packet, profile, packet_dir, answer_fn):
    self.applied.append(job.uid)
    return ApplyOutcome(status=self.status)


def make_pipeline(tmp_path,
                  profile,
                  prefs,
                  jobs,
                  mode='dry_run',
                  llm=None,
                  applier=None,
                  **apply_kw):
  cfg = Config(preferences=prefs, output_dir=str(tmp_path / 'out'))
  cfg.apply.mode = mode
  cfg.apply.delay_seconds = 0
  for k, v in apply_kw.items():
    setattr(cfg.apply, k, v)
  store = Store(str(tmp_path / 'db.sqlite'))
  return Pipeline(cfg,
                  profile,
                  llm or FakeLLM(),
                  store,
                  sources=[ListSource(jobs)],
                  applier_factory=lambda _: applier), store


def test_end_to_end_dry_run(tmp_path, profile, prefs):
  jobs = [
      make_job(),
      make_job(source='remotive', external_id='x'),  # cross-post dup
      make_job(external_id='acme/2', title='Sales Manager')
  ]
  pipe, store = make_pipeline(tmp_path, profile, prefs, jobs)
  res = pipe.search()
  assert {
      k: res[k] for k in ('fetched', 'new', 'passed')
  } == {
      'fetched': 3,
      'new': 2,
      'passed': 1
  }
  assert res['by_source'] == {'fake': {'fetched': 3, 'new': 2, 'passed': 1}}
  assert pipe.match() == {'scored': 1, 'errors': 0}
  assert pipe.apply() == {'packet_ready': 1}
  [row] = store.report()
  assert row['status'] == 'packet_ready' and row['score'] == 90
  assert os.path.exists(os.path.join(row['packet_dir'], 'APPLICATION.md'))
  # Re-running finds nothing new.
  assert pipe.search()['new'] == 0 and pipe.match()['scored'] == 0
  assert pipe.apply() == {}


def test_low_scores_not_applied(tmp_path, profile, prefs):
  pipe, store = make_pipeline(tmp_path,
                              profile,
                              prefs, [make_job()],
                              llm=FakeLLM(score=40))
  pipe.search()
  pipe.match()
  assert pipe.apply() == {} and store.report() == []


def test_submit_limits(tmp_path, profile, prefs):
  jobs = [make_job(external_id=f'c{i}/1', company=f'C{i}') for i in range(3)]
  jobs.append(
      make_job(external_id='c0/2',
               company='C0',
               title='Staff Machine Learning Engineer'))
  applier = FakeApplier()
  pipe, store = make_pipeline(tmp_path,
                              profile,
                              prefs,
                              jobs,
                              mode='auto',
                              applier=applier,
                              max_per_day=2)
  pipe.search()
  pipe.match()
  assert pipe.apply() == {'submitted': 2}
  assert pipe.apply() == {}  # daily cap reached
  assert store.submitted_since(store_day_start()) == 2


def store_day_start():
  import datetime as dt
  return dt.datetime.now(dt.timezone.utc).replace(hour=0,
                                                  minute=0,
                                                  second=0,
                                                  microsecond=0)


def test_company_cooldown(tmp_path, profile, prefs):
  jobs = [
      make_job(),
      make_job(external_id='acme/2', title='Staff Machine Learning Engineer')
  ]
  applier = FakeApplier()
  pipe, _ = make_pipeline(tmp_path,
                          profile,
                          prefs,
                          jobs,
                          mode='auto',
                          applier=applier)
  pipe.search()
  pipe.match()
  assert pipe.apply() == {'submitted': 1}
  assert len(applier.applied) == 1


def test_dry_run_packets_reused_later(tmp_path, profile, prefs):
  llm = FakeLLM()
  pipe, store = make_pipeline(tmp_path, profile, prefs, [make_job()], llm=llm)
  pipe.search()
  pipe.match()
  pipe.apply()
  pipe.cfg.apply.mode = 'review'
  pipe._applier_factory = lambda _: FakeApplier()
  tailor_calls = sum(1 for c in llm.calls if c[0] == 'TailoredApplication')
  assert pipe.apply() == {'submitted': 1}
  assert sum(
      1 for c in llm.calls if c[0] == 'TailoredApplication') == tailor_calls
  assert store.report()[0]['status'] == 'submitted'


def test_same_title_openings_in_one_source_are_kept(tmp_path, profile, prefs):
  jobs = [make_job(external_id='acme/1'), make_job(external_id='acme/2')]
  pipe, store = make_pipeline(tmp_path, profile, prefs, jobs)
  assert pipe.search()['new'] == 2
  # ...but the same posting from another board is still a duplicate.
  pipe._sources = [ListSource([make_job(source='remotive', external_id='9')])]
  assert pipe.search()['new'] == 0


class AuthFailLLM(FakeLLM):

  def structured(self, **kw):
    from jobpilot.llm import LLMAuthError
    self.calls.append(kw)
    raise LLMAuthError('no key')


def test_missing_api_key_aborts_match(tmp_path, profile, prefs):
  import pytest
  from jobpilot.llm import LLMAuthError
  jobs = [make_job(external_id=f'acme/{i}') for i in range(5)]
  llm = AuthFailLLM()
  pipe, _ = make_pipeline(tmp_path, profile, prefs, jobs, llm=llm)
  pipe.cfg.llm.max_workers = 1
  pipe.search()
  with pytest.raises(LLMAuthError):
    pipe.match()
  assert len(llm.calls) < 5


def test_apply_explains_empty_result(tmp_path, profile, prefs, caplog):
  import logging
  pipe, _ = make_pipeline(tmp_path, profile, prefs, [make_job()])
  pipe.search()
  with caplog.at_level(logging.INFO):
    assert pipe.apply() == {}
  assert '1 jobs passed the prefilter, 0 scored' in caplog.text
  assert 'jobpilot match' in caplog.text


def test_browser_setup_error_propagates(tmp_path, profile, prefs):
  import pytest
  from jobpilot.appliers.browser import BrowserSetupError

  class Broken:

    def apply(self, *a):
      raise BrowserSetupError('Chromium is not installed')

  pipe, _ = make_pipeline(tmp_path,
                          profile,
                          prefs, [make_job()],
                          mode='review',
                          applier=Broken())
  pipe.search()
  pipe.match()
  with pytest.raises(BrowserSetupError):
    pipe.apply()


def test_jobs_listing(tmp_path, profile, prefs):
  jobs = [make_job(), make_job(external_id='acme/2', title='Sales Manager')]
  pipe, store = make_pipeline(tmp_path, profile, prefs, jobs)
  pipe.search()
  rows = store.list_jobs()
  assert {r['title'] for r in rows
         } == {'Senior Machine Learning Engineer', 'Sales Manager'}
  [rej] = store.list_jobs('rejected')
  assert rej['filter_reason'] == 'title does not match'
  assert [r['uid'] for r in store.list_jobs('passed')] == ['greenhouse:acme/1']
  assert store.source_counts() == [('greenhouse', 2, 1)]


def test_search_table_shows_board_failures(tmp_path, profile, prefs):

  class FailingBoards(ListSource):
    name = 'greenhouse'
    boards = ['a', 'b']

    def fetch(self, prefs):
      self.errors = [('a', 'HTTP 403 from x'), ('b', 'HTTP 403 from x')]
      return iter([])

  pipe, _ = make_pipeline(tmp_path, profile, prefs, [])
  pipe._sources = [FailingBoards([])]
  res = pipe.search()
  assert res['by_source']['greenhouse']['error'] == (
      '2/2 boards failed (HTTP 403 from x)')
