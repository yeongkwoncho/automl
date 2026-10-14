from tradepilot.learn import propose, rule_stats
from tradepilot.models import (Decision, Outcome, Rule, RuleAdjustment,
                               RuleStat, Strategy, StrategyParams)
import datetime as dt


def strategy(**params):
  return Strategy(params=StrategyParams(**params),
                  rules=[
                      Rule(id='a', description='a'),
                      Rule(id='b', description='b'),
                      Rule(id='f',
                           description='filter',
                           kind='filter',
                           params={'max_gap_pct': 10.0})
                  ])


def test_rule_stats():
  ds = [
      Decision(id=i,
               date=dt.date(2026, 10, 1),
               symbol='X',
               action='buy',
               reasons=['a'] + (['b'] if i % 2 else [])) for i in range(4)
  ]
  outs = {
      0:
          Outcome(decision_id=0,
                  horizon=5,
                  ret_pct=3,
                  max_up_pct=3,
                  max_down_pct=0,
                  label='good'),
      1:
          Outcome(decision_id=1,
                  horizon=5,
                  ret_pct=-2,
                  max_up_pct=0,
                  max_down_pct=-2,
                  label='bad')
  }
  st = rule_stats(ds, outs)
  assert (st['a'].n, st['a'].good, st['a'].bad) == (2, 1, 1)
  assert st['a'].avg_ret_pct == 0.5 and st['b'].n == 1
  assert st['a'].hit_rate == 0.5


def test_no_change_below_min_samples():
  s = strategy(min_samples=20)
  stats = {'a': RuleStat(rule_id='a', n=10, good=9, bad=1)}
  assert propose(s, stats) == (None, [])


def test_weight_moves_are_bounded():
  s = strategy(min_samples=5, max_weight_change=0.25)
  stats = {
      'a': RuleStat(rule_id='a', n=30, good=28, bad=2),
      'b': RuleStat(rule_id='b', n=30, good=10, bad=10)
  }
  new, changes = propose(s, stats)
  assert new.version == 2
  assert new.rule('a').weight == 1.25  # capped at +25%
  assert new.rule('b').weight == 1.0  # 50% hit rate: unchanged
  assert [c.rule_id for c in changes] == ['a']
  assert s.rule('a').weight == 1.0  # original untouched


def test_disable_needs_twice_min_samples():
  s = strategy(min_samples=10, disable_below=0.35)
  few = {'a': RuleStat(rule_id='a', n=15, good=3, bad=12)}
  new, _ = propose(s, few)
  assert new.rule('a').enabled and new.rule('a').weight < 1.0
  many = {'a': RuleStat(rule_id='a', n=25, good=4, bad=21)}
  new, changes = propose(s, many)
  assert not new.rule('a').enabled and changes[0].field == 'enabled'


def test_llm_adjustments_bounded_and_existing_only():
  s = strategy()
  adj = [
      RuleAdjustment(rule_id='f',
                     param='max_gap_pct',
                     new_value=30,
                     rationale='x'),
      RuleAdjustment(rule_id='f',
                     param='new_param',
                     new_value=1,
                     rationale='ignored'),
      RuleAdjustment(rule_id='zzz',
                     param='weight',
                     new_value=2,
                     rationale='ignored'),
      RuleAdjustment(rule_id='b', param='weight', new_value=0.01, rationale='y')
  ]
  new, changes = propose(s, {}, adj)
  assert new.rule('f').params == {'max_gap_pct': 12.5}
  assert new.rule('b').weight == 0.75
  assert {c.source for c in changes} == {'llm'} and len(changes) == 2
