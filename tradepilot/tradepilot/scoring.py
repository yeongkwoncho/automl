"""Scores decisions against what the market did afterwards."""
import datetime as dt
import logging
from typing import Dict, List, Optional, Set, Tuple

from tradepilot.models import Bar, Decision, Label, Outcome, StrategyParams
from tradepilot.prices import PriceSource

logger = logging.getLogger(__name__)


def label_for(action: str, ret_pct: float, p: StrategyParams) -> Label:
  """Was the decision right in hindsight?

  buy/hold: right if price rose by at least win_threshold, wrong if it
    fell by at least loss_threshold.
  pass: wrong (missed opportunity) if price rose by win_threshold, right
    (avoided loss) if it fell by loss_threshold.
  sell: right if price fell afterwards, wrong if it kept rising.
  Smaller moves are neutral.
  """
  up = ret_pct >= p.win_threshold_pct
  down = ret_pct <= -p.loss_threshold_pct
  if action in ('buy', 'hold'):
    return 'good' if up else 'bad' if down else 'neutral'
  # pass and sell are both bets that the price will not go up.
  return 'bad' if up else 'good' if down else 'neutral'


def score(d: Decision, bars: List[Bar], horizon: int,
          p: StrategyParams) -> Optional[Outcome]:
  """Outcome after `horizon` trading days, or None if not yet known.

  Bars must start on or before the decision date. The entry price is the
  decision price, or the decision-day close when none was recorded.
  """
  after = [b for b in bars if b.date >= d.date]
  if not after:
    return None
  if after[0].date == d.date:
    entry = d.price or after[0].close
  else:
    # No bar on the decision day (holiday/after-hours decision): the
    # window starts at the next session, entering at its open.
    entry = d.price or after[0].open
  window = after[:horizon + 1]
  if len(window) < horizon + 1 or not entry:
    return None
  pct = lambda x: (x / entry - 1) * 100
  ret = pct(window[-1].close)
  # For horizon 0 the window is the decision day itself.
  return Outcome(decision_id=d.id,
                 horizon=horizon,
                 ret_pct=round(ret, 3),
                 max_up_pct=round(pct(max(b.high for b in window)), 3),
                 max_down_pct=round(pct(min(b.low for b in window)), 3),
                 label=label_for(d.action, ret, p))


def score_pending(store, prices: PriceSource, p: StrategyParams,
                  today: dt.date) -> Tuple[Dict[int, int], Set[int]]:
  """Scores every decision whose horizon has elapsed.

  Returns (outcomes added per horizon, ids of decisions that got any new
  outcome).
  """
  counts = {h: 0 for h in p.horizons}
  newly: Set[int] = set()
  cache: Dict[str, List[Bar]] = {}
  for h in p.horizons:
    for d in store.unscored(h):
      if d.date > today:
        continue
      key = f'{d.symbol}:{d.date}'
      if key not in cache:
        try:
          cache[key] = prices.bars(d.symbol, d.date, today)
        except Exception as e:  # pylint: disable=broad-except
          logger.warning('prices for %s failed: %s', d.symbol, e)
          cache[key] = []
      o = score(d, cache[key], h, p)
      if o:
        store.save_outcome(o)
        counts[h] += 1
        newly.add(d.id)
  return counts, newly
