import datetime as dt

from conftest import D0, trading_days
from tradepilot.models import Bar, Decision, StrategyParams
from tradepilot.scoring import label_for, score

P = StrategyParams()


def bars(closes, start=D0):
  return [
      Bar(date=d, open=c, high=c * 1.02, low=c * 0.97, close=c)
      for d, c in zip(trading_days(start, len(closes)), closes)
  ]


def test_labels():
  assert label_for('buy', 2.0, P) == 'good'
  assert label_for('buy', -2.0, P) == 'bad'
  assert label_for('buy', 0.3, P) == 'neutral'
  assert label_for('pass', 2.0, P) == 'bad'  # missed opportunity
  assert label_for('pass', -2.0, P) == 'good'  # avoided a loss
  assert label_for('sell', 3.0, P) == 'bad'
  assert label_for('hold', 1.0, P) == 'good'


def test_score_horizons():
  d = Decision(id=1, date=D0, symbol='X', action='buy', price=100)
  b = bars([101, 103, 104, 99, 98, 106])
  o0 = score(d, b, 0, P)
  assert o0.ret_pct == 1.0 and o0.label == 'good'
  o5 = score(d, b, 5, P)
  assert o5.ret_pct == 6.0 and o5.max_down_pct == round(
      (98 * .97 / 100 - 1) * 100, 3)
  assert score(d, b, 6, P) is None  # not enough days yet


def test_entry_defaults_and_holiday():
  d = Decision(id=1, date=D0, symbol='X', action='pass')
  assert score(d, bars([100, 95]), 1, P).ret_pct == -5.0  # entry = close
  # Decision on a Saturday: enter at the next session's open.
  sat = D0 + dt.timedelta(days=2)
  d2 = Decision(id=2, date=sat, symbol='X', action='buy')
  b = [Bar(date=sat + dt.timedelta(days=2), open=50, high=52, low=49, close=51)]
  assert score(d2, b, 0, P).ret_pct == 2.0
