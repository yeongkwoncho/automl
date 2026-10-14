import os

import pytest

from conftest import D0, trading_days, write_prices
from tradepilot.cli import main
from tradepilot.store import Store


@pytest.fixture
def workdir(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  main(['init'])
  write_prices('prices', '005930', [70000 + 700 * i for i in range(10)])
  return tmp_path


def test_log_review_brief(workdir, capsys):
  day = trading_days(D0, 3)[0]
  main([
      'log', '005930', 'buy', '--price', '70000', '--reasons',
      'breakout_20d,volume_surge', '--signal', 'vol_ratio=2.4', '--date',
      str(day), '--note', '20일 고가 돌파'
  ])
  main([
      'log', '005930', 'pass', '--reasons', 'overextended', '--against',
      'breakout_20d', '--date',
      str(day)
  ])
  main(['log', '005930', 'buy', '--reasons', 'typo_rule', '--date', str(day)])
  err = capsys.readouterr().err
  assert 'not rules in strategy v1: typo_rule' in err

  main(['review', '--date', str(trading_days(D0, 3)[-1]), '--no-llm'])
  out = capsys.readouterr().out
  assert 'decisions today: 0' in out and 'report:' in out
  store = Store('tradepilot.db')
  assert len(store.outcomes(0)) == 3 and len(store.outcomes(1)) == 3
  store.close()

  main(['stats'])
  out = capsys.readouterr().out
  assert 'breakout_20d' in out
  main(['brief'])
  out = capsys.readouterr().out
  assert '다음 거래일 전략 v1' in out
  assert os.path.exists('strategies/active.yaml')


def test_import_csv(workdir, capsys):
  with open('d.csv', 'w') as f:
    f.write('date,time,symbol,action,price,reasons,signal_rsi,note\n'
            f'{D0},09:05,005930,pass,70000,weak_market;overextended,72,과열\n')
  main(['import', 'd.csv'])
  assert 'imported 1' in capsys.readouterr().out
  store = Store('tradepilot.db')
  [d] = store.decisions_on(D0)
  assert d.reasons == ['weak_market', 'overextended']
  assert d.signals == {'rsi': 72.0} and d.time == '09:05'
  store.close()


def test_strategy_approve_flow(workdir, capsys):
  from tradepilot.models import Strategy
  store = Store('tradepilot.db')
  main(['strategy', 'list'])
  s = store.active_strategy()
  s.version, s.rules[0].weight = 2, 1.2
  store.save_strategy(s, 'proposed')
  store.close()
  main(['strategy', 'approve', '2'])
  assert 'v2 is now active' in capsys.readouterr().out
  with pytest.raises(SystemExit):
    main(['strategy', 'approve', '2'])  # no longer proposed
  assert Strategy  # imported for clarity
