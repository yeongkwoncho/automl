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


def test_refilter_after_preferences_change(tmp_path, profile, prefs):
  jobs = [
      make_job(),
      make_job(external_id='acme/2', title='Metrology Engineer')
  ]
  pipe, store = make_pipeline(tmp_path, profile, prefs, jobs)
  pipe.search()
  pipe.match()
  assert [r['title'] for r in store.list_jobs('passed')
         ] == ['Senior Machine Learning Engineer']
  assert store.filter_reasons() == [('title does not match', 1)]

  # User switches to metrology roles and re-runs (no network needed).
  prefs.titles, prefs.keywords = ['Metrology Engineer'], []
  assert pipe.refilter() == {'now_passed': 1, 'now_rejected': 1}
  assert [r['title'] for r in store.list_jobs('passed')
         ] == ['Metrology Engineer']
  # The ML job was scored 90 earlier but no longer fits: not applied to.
  assert list(store.candidates(75)) == []
  assert pipe.refilter() == {'now_passed': 0, 'now_rejected': 0}

  # search also re-checks stored jobs.
  prefs.titles = ['Machine Learning Engineer']
  res = pipe.search()
  assert res['new'] == 0 and res['refiltered']['now_passed'] == 1


def test_cli_registers_every_step_command():
  import argparse
  from jobpilot import cli
  seen = []
  orig = argparse._SubParsersAction.add_parser

  def spy(self, name, **kw):
    seen.append(name)
    return orig(self, name, **kw)

  argparse._SubParsersAction.add_parser = spy
  try:
    try:
      cli.main(['--help'])
    except SystemExit:
      pass
  finally:
    argparse._SubParsersAction.add_parser = orig
  for cmd in ('search', 'refilter', 'match', 'apply', 'run', 'jobs', 'status',
              'check-boards', 'mark', 'init', 'import-resume', 'serve', 'companies'):
    assert cmd in seen, cmd


def test_selected_jobs_bypass_prefilter_and_min_score(tmp_path, profile, prefs):
  jobs = [
      make_job(),
      make_job(external_id='acme/2', title='Sales Manager'),
      make_job(external_id='acme/3',
               title='Staff Machine Learning '
               'Engineer',
               company='Other')
  ]
  pipe, store = make_pipeline(tmp_path,
                              profile,
                              prefs,
                              jobs,
                              llm=FakeLLM(score=40))
  pipe.search()
  # The user hand-picks the rejected Sales job in the web page.
  store.set_selected(['greenhouse:acme/2'], True)
  only = store.selected_uids()
  assert pipe.match(only=only) == {'scored': 1, 'errors': 0}
  # Normal apply skips it (filtered out, score 40 < 75)...
  assert pipe.apply() == {}
  # ...but --selected respects the user's choice.
  assert pipe.apply(only=only) == {'packet_ready': 1}
  assert [r['title'] for r in store.report()] == ['Sales Manager']
  store.set_selected(only, False)
  assert store.selected_uids() == []


def test_companies_command_flags_configured_but_empty(tmp_path, monkeypatch,
                                                      capsys):
  import yaml
  from jobpilot.cli import main
  monkeypatch.chdir(tmp_path)
  main(['init'])
  cfg = yaml.safe_load(open('config.yaml'))
  cfg['sources'] = {
      'greenhouse': {
          'boards': ['stripe', 'my_co']
      },
      'apple': {
          'enabled': True
      }
  }
  yaml.safe_dump(cfg, open('config.yaml', 'w'))
  store = Store('jobpilot.db')
  store.add_job(make_job(external_id='stripe/1', company='Stripe'), None)
  store.add_job(
      make_job(external_id='stripe/2',
               company='Stripe',
               title='Account Executive'), 'title does not match')
  # A board slug with "_" must not match other boards via LIKE wildcards.
  store.add_job(make_job(external_id='myXco/1', company='Wrong'), None)
  store.close()
  capsys.readouterr()
  main(['companies'])
  out = capsys.readouterr().out
  line = lambda board: next(l for l in out.splitlines() if f' {board} ' in l)
  assert 'Stripe' in line('stripe') and line('stripe').split()[-2:] == [
      '2', '1'
  ]
  assert 'nothing collected' in line('my_co')
  assert 'nothing collected' in line('Apple')
  assert 'Nothing collected for: my_co, Apple' in out
