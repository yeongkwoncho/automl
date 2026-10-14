"""Turns scored decisions into per-rule statistics and strategy updates.

Guardrails against overfitting a noisy market:
- A rule is only adjusted once it has `min_samples` decisive outcomes.
- Weights move at most `max_weight_change` per review and stay within
  [min_weight, max_weight].
- Rules are disabled, never deleted, so a bad call is easy to revert.
- LLM suggestions only touch existing numeric params/weights, within the
  same bounds; new rule ideas are reported, never auto-added.
"""
import datetime as dt
from typing import Dict, Iterable, List, Optional, Tuple

from tradepilot.models import (Decision, Outcome, RuleAdjustment, RuleChange,
                               RuleStat, Strategy)


def rule_stats(decisions: Iterable[Decision],
               outcomes: Dict[int, Outcome]) -> Dict[str, RuleStat]:
  """Aggregates primary-horizon outcomes per rule cited in `reasons`."""
  stats: Dict[str, RuleStat] = {}
  totals: Dict[str, float] = {}
  for d in decisions:
    o = outcomes.get(d.id)
    if o is None:
      continue
    for rid in d.reasons:
      s = stats.setdefault(rid, RuleStat(rule_id=rid))
      s.n += 1
      setattr(s, o.label, getattr(s, o.label) + 1)
      s.by_action[d.action] = s.by_action.get(d.action, 0) + 1
      totals[rid] = totals.get(rid, 0.0) + o.ret_pct
  for rid, s in stats.items():
    s.avg_ret_pct = round(totals[rid] / s.n, 3)
  return stats


def _clip_step(old: float, target: float, max_change: float) -> float:
  lo, hi = old * (1 - max_change), old * (1 + max_change)
  return min(max(target, lo), hi)


def propose(
    strategy: Strategy,
    stats: Dict[str, RuleStat],
    llm_adjustments: Iterable[RuleAdjustment] = ()
) -> Tuple[Optional[Strategy], List[RuleChange]]:
  """Returns (next strategy version, changes) or (None, []) if no change."""
  p = strategy.params
  new = strategy.model_copy(deep=True)
  new.version = strategy.version + 1
  changes: List[RuleChange] = []

  for rule in new.rules:
    s = stats.get(rule.id)
    decisive = (s.good + s.bad) if s else 0
    if not rule.enabled or decisive < p.min_samples:
      continue
    hit = s.hit_rate
    if hit < p.disable_below and decisive >= 2 * p.min_samples:
      rule.enabled = False
      changes.append(
          RuleChange(
              rule_id=rule.id,
              field='enabled',
              old='True',
              new='False',
              reason=f'hit rate {hit:.0%} over {decisive} decisive outcomes',
              source='stats'))
      continue
    # Scale weight toward performance: 50% hit rate keeps it unchanged.
    target = rule.weight * (0.5 + hit)
    w = round(
        min(
            max(_clip_step(rule.weight, target, p.max_weight_change),
                p.min_weight), p.max_weight), 3)
    if abs(w - rule.weight) >= 0.01:
      changes.append(
          RuleChange(
              rule_id=rule.id,
              field='weight',
              old=str(rule.weight),
              new=str(w),
              reason=
              f'hit rate {hit:.0%} (n={decisive}, avg {s.avg_ret_pct:+.2f}%)',
              source='stats'))
      rule.weight = w

  for adj in llm_adjustments:
    rule = new.rule(adj.rule_id)
    if rule is None or not rule.enabled:
      continue
    if adj.param == 'weight':
      old = rule.weight
      v = round(
          min(
              max(_clip_step(old, adj.new_value, p.max_weight_change),
                  p.min_weight), p.max_weight), 3)
      if abs(v - old) >= 0.01:
        rule.weight = v
        changes.append(
            RuleChange(rule_id=rule.id,
                       field='weight',
                       old=str(old),
                       new=str(v),
                       reason=adj.rationale,
                       source='llm'))
    elif adj.param in rule.params:
      old = rule.params[adj.param]
      v = _clip_step(old, adj.new_value,
                     p.max_weight_change) if old else (adj.new_value)
      v = round(v, 4)
      if v != old:
        rule.params[adj.param] = v
        changes.append(
            RuleChange(rule_id=rule.id,
                       field=f'params.{adj.param}',
                       old=str(old),
                       new=str(v),
                       reason=adj.rationale,
                       source='llm'))

  return (new, changes) if changes else (None, [])


def stats_window_start(strategy: Strategy, today: dt.date) -> Optional[dt.date]:
  days = strategy.params.lookback_days
  return today - dt.timedelta(days=days) if days else None
