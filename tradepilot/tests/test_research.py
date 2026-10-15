import csv

from conftest import D0, trading_days
from tradepilot.cli import main
from tradepilot.models import Decision, Outcome, Rule, Strategy
from tradepilot.research import build_dataset, research_report
from tradepilot.store import Store


def journal(tmp_path, n=30):
  """Synthetic journal: buys with high vol_ratio won, low vol_ratio lost."""
  store = Store(str(tmp_path / 'db.sqlite'))
  s = Strategy(rules=[
      Rule(id='breakout', description='b'),
      Rule(id='weak_market', description='f', kind='filter')
  ])
  store.save_strategy(s, 'active')
  days = trading_days(D0, n)
  for i, day in enumerate(days):
    vol = 0.5 + i * 0.1
    win = vol >= 2.0
    did = store.add_decision(
        Decision(date=day,
                 symbol='X',
                 action='buy',
                 reasons=['breakout'],
                 signals={'vol_ratio': vol},
                 confidence=0.4 + (i % 3) * 0.25))
    ret = 3.0 if win else -2.0
    store.save_outcome(
        Outcome(decision_id=did,
                horizon=5,
                ret_pct=ret,
                max_up_pct=4,
                max_down_pct=-3,
                label='good' if win else 'bad'))
    pid = store.add_decision(
        Decision(date=day,
                 symbol='Y',
                 action='pass',
                 reasons=['weak_market'],
                 note=f'p{i}'))
    store.save_outcome(
        Outcome(decision_id=pid,
                horizon=5,
                ret_pct=2.5,
                max_up_pct=3,
                max_down_pct=0,
                label='bad'))
  store.save_review(
      days[0], {
          'llm': {
              'decision_reviews': [{
                  'decision_id': 1,
                  'reasoning_quality': 'sound',
                  'outcome_attribution': 'bad_luck',
                  'comment': ''
              }]
          }
      })
  return store, s


def test_dataset_columns(tmp_path):
  store, s = journal(tmp_path)
  rows = build_dataset(store, s)
  assert len(rows) == 60
  r = rows[0]
  assert r['signal_vol_ratio'] == 0.5 and r['ret_h5'] == -2.0
  assert r['label_h5'] == 'bad' and r['ret_h0'] is None
  assert r['reasoning_quality'] == 'sound' and r['reasons'] == 'breakout'
  assert rows[1]['action'] == 'pass' and 'signal_vol_ratio' not in rows[1]


def test_report_finds_signal_edge_and_costly_passes(tmp_path):
  store, s = journal(tmp_path)
  report = research_report(build_dataset(store, s), s)
  assert '## 1. 판단 유형별 성과' in report
  assert '### vol_ratio' in report
  # Top vol_ratio bucket should be all wins, bottom all losses.
  lines = [l for l in report.splitlines() if l.startswith('| ≥')]
  assert lines and '+3.00%' in lines[-1]
  assert '| < ' in report and '-2.00%' in report
  assert '## 4. 가장 비쌌던 pass' in report
  assert '## 5. 근거 품질 vs 결과' in report
  assert '## 6. 확신도 보정' in report


def test_report_waits_for_data(tmp_path):
  store = Store(str(tmp_path / 'db.sqlite'))
  s = Strategy()
  store.save_strategy(s, 'active')
  assert '분석을 보류' in research_report(build_dataset(store, s), s)


def test_template_is_header_only_and_importable(tmp_path, monkeypatch, capsys):
  monkeypatch.chdir(tmp_path)
  main(['init'])
  main(['template', '--date', '2026-10-15'])
  out = capsys.readouterr().out
  path = 'decisions-2026-10-15.csv'
  rows = list(csv.reader(open(path, encoding='utf-8-sig')))
  assert len(rows) == 1 and rows[0][:4] == ['date', 'time', 'symbol', 'action']
  assert 'example rows' in out
  main(['import', path])  # empty template imports nothing
  assert 'imported 0' in capsys.readouterr().out
  with open(path, 'a', encoding='utf-8') as f:
    f.write(
        '2026-10-15,09:10,005930,buy,71200,10,breakout_20d,,0.6,돌파,2.4,4.1,64,1.2\n'
    )
  main(['import', path])
  assert 'imported 1' in capsys.readouterr().out
  main(['research'])
  out = capsys.readouterr().out
  assert 'dataset:' in out and 'report:' in out
  rows = list(csv.DictReader(open('research/dataset.csv',
                                  encoding='utf-8-sig')))
  assert rows[0]['signal_vol_ratio'] == '2.4'
