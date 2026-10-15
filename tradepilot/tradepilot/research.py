"""Builds a research dataset from the journal and mines it for strategy ideas.

The dataset is one row per decision with every signal, outcome and review
grade flattened into columns, so it can be loaded straight into pandas,
Excel or a backtester. `research_report` answers the questions that drive
the next strategy revision:
  - Which signal ranges preceded good vs bad outcomes?
  - Which rule combinations work, and which passes cost the most?
  - Does following the rules (sound reasoning) actually pay?
"""
import csv
import datetime as dt
import statistics
from typing import Dict, List, Optional

from tradepilot.models import Strategy
from tradepilot.store import Store


def build_dataset(store: Store, strategy: Strategy) -> List[dict]:
  horizons = strategy.params.horizons
  primary = strategy.params.primary_horizon
  reviews = store.decision_reviews()
  rows = []
  for d in store.decisions_since(None):
    oc = {o.horizon: o for o in store.outcomes_for(d.id)}
    r = reviews.get(d.id, {})
    row = {
        'id': d.id,
        'date': d.date.isoformat(),
        'weekday': d.date.weekday(),
        'time': d.time,
        'symbol': d.symbol,
        'action': d.action,
        'price': d.price,
        'quantity': d.quantity,
        'confidence': d.confidence,
        'strategy_version': d.strategy_version,
        'reasons': ';'.join(d.reasons),
        'against': ';'.join(d.against),
        'n_reasons': len(d.reasons),
        'n_against': len(d.against),
        'note': d.note,
        'reasoning_quality': r.get('reasoning_quality', ''),
        'outcome_attribution': r.get('outcome_attribution', ''),
    }
    for k, v in d.signals.items():
      row[f'signal_{k}'] = v
    for h in horizons:
      o = oc.get(h)
      row[f'ret_h{h}'] = o.ret_pct if o else None
      row[f'label_h{h}'] = o.label if o else ''
    o = oc.get(primary)
    row['max_up_primary'] = o.max_up_pct if o else None
    row['max_down_primary'] = o.max_down_pct if o else None
    rows.append(row)
  return rows


def write_csv(rows: List[dict], path: str) -> int:
  cols: List[str] = []
  for r in rows:
    cols += [k for k in r if k not in cols]
  with open(path, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
  return len(rows)


def _summary(rets: List[float], labels: List[str]) -> str:
  good, bad = labels.count('good'), labels.count('bad')
  hit = (good + 1) / (good + bad + 2)
  avg = statistics.fmean(rets) if rets else 0.0
  return f'{len(rets)} | {good} | {bad} | {hit:.0%} | {avg:+.2f}%'


def _quantile_edges(values: List[float], k: int) -> List[float]:
  s = sorted(values)
  return [s[min(len(s) - 1, int(len(s) * i / k))] for i in range(1, k)]


def research_report(rows: List[dict],
                    strategy: Strategy,
                    min_bucket: int = 5) -> str:
  h = strategy.params.primary_horizon
  ret_k, lab_k = f'ret_h{h}', f'label_h{h}'
  scored = [r for r in rows if r.get(ret_k) is not None]
  head = '| 구간 | n | good | bad | 적중률 | 평균 수익률 |\n|---|---|---|---|---|---|'
  L = [
      f'# 전략 연구 리포트 {dt.date.today().isoformat()}', '',
      f'- 전체 판단 {len(rows)}건, {h}일 결과 확정 {len(scored)}건',
      f'- 전략 v{strategy.version} ({strategy.name})', ''
  ]
  if len(scored) < min_bucket:
    L += [f'결과가 확정된 판단이 {min_bucket}건 미만이라 분석을 보류합니다. '
          '판단을 계속 기록하세요.', '']
    return '\n'.join(L)

  # 1. Action-level baseline.
  L += ['## 1. 판단 유형별 성과', '', head.replace('구간', '판단')]
  for a in ('buy', 'hold', 'sell', 'pass'):
    sub = [r for r in scored if r['action'] == a]
    if sub:
      L.append(f'| {a} | ' +
               _summary([r[ret_k] for r in sub], [r[lab_k] for r in sub]) +
               ' |')
  L += [
      '', 'pass의 bad는 놓친 기회입니다. pass의 평균 수익률이 buy보다 높으면 필터가 너무 '
      '엄격하다는 신호입니다.', ''
  ]

  # 2. Signal ranges (entry decisions only, so returns are comparable).
  sig_cols = sorted({k for r in scored for k in r if k.startswith('signal_')})
  if sig_cols:
    L += ['## 2. 신호 구간별 성과 (buy/hold 판단)', '']
    for col in sig_cols:
      sub = [
          r for r in scored
          if r['action'] in ('buy', 'hold') and r.get(col) is not None
      ]
      if len(sub) < 2 * min_bucket:
        L.append(f'- `{col[7:]}`: 표본 {len(sub)}건, 구간 분석에 부족')
        continue
      k = max(2, min(5, len(sub) // min_bucket))
      edges = _quantile_edges([r[col] for r in sub], k)
      L += ['', f'### {col[7:]}', '', head]
      bounds = [float('-inf')] + edges + [float('inf')]
      for lo, hi in zip(bounds, bounds[1:]):
        b = [r for r in sub if lo <= r[col] < hi
            ] if hi != float('inf') else [r for r in sub if r[col] >= lo]
        if b:
          label = (f'< {hi:g}' if lo == float('-inf') else
                   f'≥ {lo:g}' if hi == float('inf') else f'{lo:g} – {hi:g}')
          L.append(f'| {label} | ' +
                   _summary([r[ret_k] for r in b], [r[lab_k] for r in b]) +
                   ' |')
    L.append('')

  # 3. Rule combinations.
  combos: Dict[str, List[dict]] = {}
  for r in scored:
    if r['reasons']:
      combos.setdefault(f'{r["action"]}: {r["reasons"]}', []).append(r)
  ranked = sorted(((k, v) for k, v in combos.items() if len(v) >= min_bucket),
                  key=lambda kv: -statistics.fmean(x[ret_k] for x in kv[1]))
  if ranked:
    L += ['## 3. 규칙 조합별 성과', '', head.replace('구간', '판단: 규칙')]
    for k, v in ranked:
      L.append(f'| {k} | ' +
               _summary([r[ret_k] for r in v], [r[lab_k] for r in v]) + ' |')
    L.append('')

  # 4. Costliest passes.
  missed = sorted(
      (r for r in scored if r['action'] == 'pass' and r[lab_k] == 'bad'),
      key=lambda r: -r[ret_k])[:10]
  if missed:
    L += [
        '## 4. 가장 비쌌던 pass (놓친 기회 상위)', '', '| 날짜 | 종목 | 근거 규칙 | 수익률 | 메모 |',
        '|---|---|---|---|---|'
    ]
    for r in missed:
      L.append(f'| {r["date"]} | {r["symbol"]} | {r["reasons"] or "—"} '
               f'| {r[ret_k]:+.2f}% | {r["note"]} |')
    L.append('')

  # 5. Process vs outcome.
  graded = [r for r in scored if r['reasoning_quality']]
  if graded:
    L += [
        '## 5. 근거 품질 vs 결과', '',
        '규칙대로 판단했을 때(sound)가 그렇지 않을 때(flawed)보다 성과가 좋아야 전략이 '
        '유효합니다.', '',
        head.replace('구간', '근거 평가')
    ]
    for q in ('sound', 'flawed', 'insufficient_data'):
      sub = [r for r in graded if r['reasoning_quality'] == q]
      if sub:
        L.append(f'| {q} | ' +
                 _summary([r[ret_k] for r in sub], [r[lab_k] for r in sub]) +
                 ' |')
    L.append('')

  # 6. Confidence calibration.
  conf = [
      r for r in scored
      if r.get('confidence') is not None and r['action'] in ('buy', 'hold')
  ]
  if len(conf) >= 2 * min_bucket:
    L += ['## 6. 확신도 보정', '', head.replace('구간', '확신도')]
    for lo, hi in ((0, 0.5), (0.5, 0.7), (0.7, 1.01)):
      b = [r for r in conf if lo <= r['confidence'] < hi]
      if b:
        L.append(f'| {lo:g} – {min(hi, 1):g} | ' +
                 _summary([r[ret_k] for r in b], [r[lab_k] for r in b]) + ' |')
    L.append('')
  return '\n'.join(L)


TEMPLATE_HEADER = [
    'date', 'time', 'symbol', 'action', 'price', 'quantity', 'reasons',
    'against', 'confidence', 'note'
]


def write_template(path: str,
                   strategy: Strategy,
                   signals: Optional[List[str]] = None,
                   day: Optional[dt.date] = None) -> str:
  """Writes a header-only CSV to fill in during the session.

  Returns example rows as text (printed, not written, so placeholder rows
  can never be imported as real decisions).
  """
  day = day or dt.date.today()
  sig = signals or ['vol_ratio', 'gap_ma20_pct', 'rsi14', 'index_vs_ma20_pct']
  header = TEMPLATE_HEADER + [f'signal_{s}' for s in sig]
  with open(path, 'w', newline='', encoding='utf-8-sig') as f:
    csv.writer(f).writerow(header)
  entry = next((r.id for r in strategy.rules if r.kind == 'entry'), 'rule')
  flt = next((r.id for r in strategy.rules if r.kind == 'filter'), 'rule')
  return '\n'.join([
      ','.join(header),
      f'{day},09:10,005930,buy,71200,10,{entry},,0.6,돌파 확인,2.4,4.1,64,1.2',
      f'{day},10:30,000660,pass,,,{flt},{entry},,이격 과다,1.1,13.2,78,1.2',
  ])
