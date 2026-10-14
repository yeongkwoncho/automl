"""The end-of-day review loop."""
import datetime as dt
import json
import logging
import os
from typing import Dict, List, Optional

import yaml

from tradepilot.learn import propose, rule_stats, stats_window_start
from tradepilot.llm import LLMError, StructuredLLM
from tradepilot.models import (DailyReview, Decision, Outcome, RuleStat,
                               Strategy)
from tradepilot.prices import PriceSource
from tradepilot.scoring import score_pending
from tradepilot.store import Store

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a trading coach reviewing one trader's decisions at the end of the \
day. Your job is to judge the quality of each decision's REASONING, \
separately from its outcome, and to improve the written strategy over time.

Principles:
- Judge process, not just P&L. A decision is 'sound' if it followed the \
strategy rules correctly given the signals available at the time, even if \
it lost money ('bad_luck'). A winning trade that broke the rules is \
'luck', not skill.
- Decisions not to buy ('pass') count as much as trades: check whether \
the cited filter rules were applied correctly and whether the pass cost \
an opportunity.
- Use the recorded signals and the rule descriptions/params to check the \
reasoning. If a decision cites no rules or lacks signals, say \
'insufficient_data' and note what should be logged next time.
- One day is noise. Base rule adjustments on the accumulated rule \
statistics, prefer small changes, and propose nothing when samples are \
small. Only adjust existing params or weights; put new rules in \
new_rule_ideas.
- Be concrete and brief. Write in the language of the trader's notes \
(Korean if the notes are Korean)."""


def _fmt_decision(d: Decision, outcomes: List[Outcome]) -> dict:
  return {
      'id': d.id,
      'date': d.date.isoformat(),
      'time': d.time,
      'symbol': d.symbol,
      'action': d.action,
      'price': d.price,
      'reasons': d.reasons,
      'against': d.against,
      'signals': d.signals,
      'confidence': d.confidence,
      'note': d.note,
      'outcomes': {
          f'h{o.horizon}': {
              'ret_pct': o.ret_pct,
              'label': o.label,
              'max_up_pct': o.max_up_pct,
              'max_down_pct': o.max_down_pct
          } for o in outcomes
      },
  }


class Reviewer:

  def __init__(self,
               store: Store,
               prices: PriceSource,
               llm: Optional[StructuredLLM],
               strategy_out: str,
               journal_dir: str,
               auto_approve: str = 'stats',
               effort: str = 'high'):
    assert auto_approve in ('none', 'stats', 'all'), auto_approve
    self.store = store
    self.prices = prices
    self.llm = llm
    self.strategy_out = strategy_out
    self.journal_dir = journal_dir
    self.auto_approve = auto_approve
    self.effort = effort

  def run(self, today: dt.date) -> dict:
    strategy = self.store.active_strategy()
    if strategy is None:
      raise RuntimeError('no active strategy; run `tradepilot init` first')
    p = strategy.params

    scored, newly = score_pending(self.store, self.prices, p, today)
    todays = self.store.decisions_on(today)
    # Past decisions whose later-horizon outcome became known in this run.
    matured_ids = newly - {d.id for d in todays}
    matured = [
        d for d in self.store.decisions_since(None) if d.id in matured_ids
    ]

    window = self.store.decisions_since(stats_window_start(strategy, today))
    primary = {o.decision_id: o for o in self.store.outcomes(p.primary_horizon)}
    stats = rule_stats(window, primary)

    llm_review: Optional[DailyReview] = None
    if self.llm and (todays or matured):
      llm_review = self._llm_review(strategy, todays, matured, stats)

    changes = self._update_strategy(strategy, stats, llm_review)
    result = {
        'date': today.isoformat(),
        'scored': scored,
        'decisions_today': len(todays),
        'matured': len(matured),
        'strategy_version': strategy.version,
        'stats': {
            k: v.model_dump() | {
                'hit_rate': round(v.hit_rate, 3)
            } for k, v in stats.items()
        },
        'llm': llm_review.model_dump() if llm_review else None,
        'changes': changes,
    }
    self.store.save_review(today, result)
    result['report'] = self._write_report(today, todays, matured, stats,
                                          llm_review, changes)
    return result

  # Steps.
  def _llm_review(self, strategy: Strategy, todays: List[Decision],
                  matured: List[Decision],
                  stats: Dict[str, RuleStat]) -> Optional[DailyReview]:
    payload = {
        'strategy': json.loads(strategy.model_dump_json()),
        'todays_decisions': [
            _fmt_decision(d, self.store.outcomes_for(d.id)) for d in todays
        ],
        'matured_decisions': [
            _fmt_decision(d, self.store.outcomes_for(d.id)) for d in matured
        ],
        'rule_stats': {
            k: v.model_dump() | {
                'hit_rate': round(v.hit_rate, 3)
            } for k, v in stats.items()
        },
        'recent_lessons': self.store.recent_lessons(),
    }
    content = ('<review_data>\n' +
               json.dumps(payload, ensure_ascii=False, indent=1, default=str) +
               '\n</review_data>\n\nReview today. Give a decision_review for '
               'every decision in todays_decisions and matured_decisions.')
    try:
      return self.llm.structured(system=SYSTEM_PROMPT,
                                 content=content,
                                 schema=DailyReview,
                                 effort=self.effort)
    except LLMError as e:
      logger.warning('LLM review failed: %s', e)
      return None

  def _update_strategy(self, strategy: Strategy, stats: Dict[str, RuleStat],
                       review: Optional[DailyReview]) -> dict:
    """Statistics-driven changes are bounded and reproducible, so with
    auto_approve='stats' they go live for the next session. LLM suggestions
    wait for `strategy approve` unless auto_approve='all'."""
    out = {'applied': [], 'proposed': [], 'proposed_version': None}
    adjustments = review.rule_adjustments if review else []
    base, pending_stats = strategy, stats
    if self.auto_approve in ('stats', 'all'):
      new, changes = propose(strategy, stats)
      if new:
        new.version = self.store.latest_version() + 1
        self.store.save_strategy(new, 'proposed', changes)
        self.store.activate(new.version)
        out['applied'] += [c.model_dump() for c in changes]
        base = new
      pending_stats = {}
    nxt, changes = propose(base, pending_stats, adjustments)
    if nxt:
      nxt.version = self.store.latest_version() + 1
      self.store.save_strategy(nxt, 'proposed', changes)
      if self.auto_approve == 'all':
        self.store.activate(nxt.version)
        out['applied'] += [c.model_dump() for c in changes]
      else:
        out['proposed'] = [c.model_dump() for c in changes]
        out['proposed_version'] = nxt.version
    self.export_active()
    return out

  def export_active(self):
    """Writes the active strategy where your trading tools read it."""
    s = self.store.active_strategy()
    if not s or not self.strategy_out:
      return
    os.makedirs(os.path.dirname(self.strategy_out) or '.', exist_ok=True)
    with open(self.strategy_out, 'w') as f:
      yaml.safe_dump(json.loads(s.model_dump_json()),
                     f,
                     allow_unicode=True,
                     sort_keys=False)

  def _write_report(self, today, todays, matured, stats, review,
                    changes) -> str:
    os.makedirs(self.journal_dir, exist_ok=True)
    path = os.path.join(self.journal_dir, f'{today.isoformat()}.md')
    by_id = {
        r.decision_id: r for r in (review.decision_reviews if review else [])
    }
    L = [f'# 거래 리뷰 {today.isoformat()}', '']
    if review:
      L += ['## 요약', '', review.summary, '']

    def table(title, ds):
      if not ds:
        return []
      rows = [
          f'## {title}', '',
          '| # | 종목 | 판단 | 근거 규칙 | 당일 | 1일 | 5일 | 근거 평가 | 원인 |',
          '|---|---|---|---|---|---|---|---|---|'
      ]
      for d in ds:
        oc = {o.horizon: o for o in self.store.outcomes_for(d.id)}
        cell = lambda h: (f'{oc[h].ret_pct:+.2f}% ({oc[h].label})'
                          if h in oc else '—')
        r = by_id.get(d.id)
        rows.append(
            f'| {d.id} | {d.symbol} | {d.action} | {", ".join(d.reasons) or "—"} '
            f'| {cell(0)} | {cell(1)} | {cell(5)} '
            f'| {r.reasoning_quality if r else "—"} '
            f'| {r.outcome_attribution if r else "—"} |')
      notes = [f'- #{d.id}: {by_id[d.id].comment}' for d in ds if d.id in by_id]
      return rows + [''] + notes + ['']

    L += table('오늘 판단', todays)
    L += table('결과가 확정된 과거 판단', matured)
    if stats:
      L += [
          '## 규칙별 누적 성과', '',
          '| 규칙 | n | good | bad | neutral | 적중률 | 평균 수익률 |',
          '|---|---|---|---|---|---|---|'
      ]
      for s in sorted(stats.values(), key=lambda s: -s.n):
        L.append(f'| {s.rule_id} | {s.n} | {s.good} | {s.bad} | {s.neutral} '
                 f'| {s.hit_rate:.0%} | {s.avg_ret_pct:+.2f}% |')
      L.append('')
    if review and review.lessons:
      L += ['## 교훈', ''] + [f'- {x}' for x in review.lessons] + ['']
    fmt = lambda c: (f'- `{c["rule_id"]}.{c["field"]}`: {c["old"]} → '
                     f'{c["new"]} ({c["source"]}) — {c["reason"]}')
    if changes['applied']:
      L += ['## 다음 거래일 전략에 적용된 변경', '']
      L += [fmt(c) for c in changes['applied']] + ['']
    if changes['proposed']:
      L += [
          f'## 승인 대기 중인 변경 (v{changes["proposed_version"]})', '',
          f'`tradepilot strategy approve {changes["proposed_version"]}`'
          ' 로 적용', ''
      ]
      L += [fmt(c) for c in changes['proposed']] + ['']
    if review and review.new_rule_ideas:
      L += ['## 새 규칙 아이디어 (자동 적용 안 됨)', '']
      L += [f'- {x}' for x in review.new_rule_ideas] + ['']
    with open(path, 'w') as f:
      f.write('\n'.join(L))
    return path
