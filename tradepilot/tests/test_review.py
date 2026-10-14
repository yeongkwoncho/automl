import os

import yaml

from conftest import D0, trading_days, write_prices
from tradepilot.models import (DailyReview, Decision, DecisionReview,
                               RuleAdjustment, Strategy, StrategyParams, Rule)
from tradepilot.prices import CSVPrices
from tradepilot.review import Reviewer
from tradepilot.store import Store


class FakeLLM:

  def __init__(self, adjustments=()):
    self.calls = []
    self.adjustments = list(adjustments)

  def structured(self, *, system, content, schema, effort='high'):
    assert schema is DailyReview
    self.calls.append(content)
    import json, re
    data = json.loads(
        re.search(r'<review_data>\n(.*)\n</review_data>', content,
                  re.S).group(1))
    ids = [
        d['id'] for d in data['todays_decisions'] + data['matured_decisions']
    ]
    return DailyReview(summary='요약',
                       lessons=['거래량 신호를 기록할 것'],
                       decision_reviews=[
                           DecisionReview(decision_id=i,
                                          reasoning_quality='sound',
                                          outcome_attribution='skill',
                                          comment='규칙대로') for i in ids
                       ],
                       rule_adjustments=self.adjustments,
                       new_rule_ideas=['갭 필터'])


def setup(tmp_path, min_samples=2, auto='stats', adjustments=()):
  prices = tmp_path / 'prices'
  # UP rises 1%/day; DN falls 1%/day.
  write_prices(prices, 'UP', [100 * 1.01**i for i in range(40)])
  write_prices(prices, 'DN', [100 * 0.99**i for i in range(40)])
  store = Store(str(tmp_path / 'db.sqlite'))
  s = Strategy(params=StrategyParams(min_samples=min_samples, lookback_days=0),
               rules=[
                   Rule(id='breakout', description='b'),
                   Rule(id='weak_market',
                        description='f',
                        kind='filter',
                        params={'x': 1.0})
               ])
  store.save_strategy(s, 'active')
  llm = FakeLLM(adjustments)
  r = Reviewer(store,
               CSVPrices(str(prices)),
               llm,
               str(tmp_path / 'strategies' / 'active.yaml'),
               str(tmp_path / 'journal'),
               auto_approve=auto)
  return store, r, llm


def test_daily_review_learns_and_exports(tmp_path):
  store, r, llm = setup(tmp_path)
  days = trading_days(D0, 30)
  # Past decisions: breakout buys on UP were right, on DN wrong; passes on
  # DN (weak_market) were right.
  for day in days[:6]:
    store.add_decision(
        Decision(date=day, symbol='UP', action='buy', reasons=['breakout']))
    store.add_decision(
        Decision(date=day, symbol='DN', action='pass', reasons=['weak_market']))
  today = days[11]
  store.add_decision(
      Decision(date=today,
               symbol='UP',
               action='pass',
               reasons=['weak_market'],
               note='시장 약세'))
  res = r.run(today)

  assert res['scored'] == {0: 13, 1: 12, 5: 12}  # today has only h0
  assert res['decisions_today'] == 1 and res['matured'] == 12
  st = res['stats']
  assert st['breakout']['good'] == 6 and st['weak_market']['good'] == 6
  # Both rules were consistently right -> weights raised (capped +25%).
  applied = {c['rule_id']: c for c in res['changes']['applied']}
  assert applied['breakout']['new'] == '1.25'
  active = store.active_strategy()
  assert active.version == 2 and active.rule('breakout').weight == 1.25
  exported = yaml.safe_load(open(tmp_path / 'strategies' / 'active.yaml'))
  assert exported['version'] == 2

  # Today's pass on a rising stock: day-0 outcome is already known.
  [d] = store.decisions_on(today)
  assert {o.horizon for o in store.outcomes_for(d.id)} == {0}
  assert len(llm.calls) == 1 and '"todays_decisions"' in llm.calls[0]

  report = open(res['report']).read()
  assert '오늘 판단' in report and '규칙별 누적 성과' in report
  assert '다음 거래일 전략에 적용된 변경' in report and '갭 필터' in report
  assert store.review(today)['llm']['lessons'] == ['거래량 신호를 기록할 것']

  # Re-running the same day does not double count or re-apply.
  res2 = r.run(today)
  assert res2['scored'] == {0: 0, 1: 0, 5: 0}


def test_llm_changes_wait_for_approval(tmp_path):
  adj = [
      RuleAdjustment(rule_id='weak_market',
                     param='x',
                     new_value=1.2,
                     rationale='완화')
  ]
  store, r, _ = setup(tmp_path, min_samples=100, adjustments=adj)
  store.add_decision(
      Decision(date=D0, symbol='UP', action='buy', reasons=['breakout']))
  res = r.run(D0)
  ch = res['changes']
  assert ch['applied'] == [] and ch['proposed_version'] == 2
  assert ch['proposed'][0]['field'] == 'params.x'
  assert store.active_strategy().version == 1
  assert store.strategy_status(2) == 'proposed'
  assert '승인 대기' in open(res['report']).read()


def test_auto_approve_none_proposes_stats_changes(tmp_path):
  store, r, _ = setup(tmp_path, auto='none')
  for day in trading_days(D0, 4):
    store.add_decision(
        Decision(date=day, symbol='UP', action='buy', reasons=['breakout']))
  res = r.run(trading_days(D0, 12)[-1])
  assert res['changes']['applied'] == []
  assert res['changes']['proposed_version'] == 2
  assert store.active_strategy().version == 1


def test_no_llm_and_no_decisions(tmp_path):
  store, r, llm = setup(tmp_path)
  r.llm = None
  res = r.run(D0)
  assert res['llm'] is None and os.path.exists(res['report'])
