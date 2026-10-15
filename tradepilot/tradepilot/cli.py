"""Command line interface: `tradepilot <command>`."""
import argparse
import csv
import datetime as dt
import json
import logging
import os
import shutil
import sys

from pydantic import BaseModel, Field
import yaml

from tradepilot.learn import rule_stats, stats_window_start
from tradepilot.llm import DEFAULT_MODEL, ClaudeLLM
from tradepilot.models import Decision, Strategy
from tradepilot.prices import build_prices
from tradepilot.review import Reviewer
from tradepilot.store import Store

EXAMPLES = os.path.join(os.path.dirname(__file__), 'examples')


class Config(BaseModel):
  database: str = 'tradepilot.db'
  strategy_file: str = 'strategy.yaml'
  strategy_out: str = 'strategies/active.yaml'
  journal_dir: str = 'journal'
  prices: dict = Field(default_factory=lambda: {
      'provider': 'csv',
      'dir': 'prices'
  })
  # none: every change waits for `strategy approve`
  # stats: bounded, statistics-driven changes apply automatically
  # all: LLM-suggested param changes apply automatically too
  auto_approve: str = 'stats'
  model: str = DEFAULT_MODEL
  effort: str = 'high'
  base_dir: str = '.'

  def path(self, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(self.base_dir, p)


def load_config(path: str) -> Config:
  if not os.path.exists(path):
    sys.exit(f'{path} not found; run `tradepilot init` first')
  with open(path) as f:
    cfg = Config(**(yaml.safe_load(f) or {}))
  cfg.base_dir = os.path.dirname(os.path.abspath(path))
  return cfg


def open_store(cfg: Config) -> Store:
  """Opens the DB, activating strategy_file as v1 on first use."""
  store = Store(cfg.path(cfg.database))
  if store.active_strategy() is None:
    path = cfg.path(cfg.strategy_file)
    if not os.path.exists(path):
      sys.exit(f'no active strategy and {path} not found')
    with open(path) as f:
      s = Strategy(**yaml.safe_load(f))
    s.version = store.latest_version() + 1
    store.save_strategy(s, 'active')
    print(f'activated strategy v{s.version} from {path}', file=sys.stderr)
  return store


def _parse_date(s):
  return dt.date.fromisoformat(s) if s else dt.date.today()


def _split(s):
  return [
      x.strip() for x in (s or '').replace(';', ',').split(',') if x.strip()
  ]


def cmd_init(args):
  for name in ('config.yaml', 'strategy.yaml'):
    dst = os.path.join(args.dir, name)
    if os.path.exists(dst):
      print(f'exists, not overwriting: {dst}')
    else:
      shutil.copy(os.path.join(EXAMPLES, name), dst)
      print(f'wrote {dst}')
  print('\nNext: edit strategy.yaml so its rules match how you trade, put '
        'daily prices in prices/<symbol>.csv (or set prices.provider: '
        'yfinance), and log decisions with `tradepilot log`.')


def cmd_log(args):
  cfg = load_config(args.config)
  store = open_store(cfg)
  strategy = store.active_strategy()
  signals = {}
  for kv in args.signal or []:
    k, _, v = kv.partition('=')
    signals[k.strip()] = float(v)
  d = Decision(date=_parse_date(args.date),
               time=args.time or '',
               symbol=args.symbol,
               action=args.action,
               price=args.price,
               quantity=args.quantity or 0,
               reasons=_split(args.reasons),
               against=_split(args.against),
               signals=signals,
               confidence=args.confidence,
               note=args.note or '',
               strategy_version=strategy.version)
  unknown = [r for r in d.reasons + d.against if not strategy.rule(r)]
  if unknown:
    print(
        f'warning: not rules in strategy v{strategy.version}: '
        f'{", ".join(unknown)}',
        file=sys.stderr)
  print(f'logged decision #{store.add_decision(d)}')
  store.close()


def cmd_import(args):
  """CSV columns: date,time,symbol,action,price,quantity,reasons,against,
  confidence,note plus any signal_<name> columns."""
  cfg = load_config(args.config)
  store = open_store(cfg)
  version = store.active_strategy().version
  n = 0
  with open(args.csv, newline='', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
      num = lambda k: float(row[k]) if row.get(k) not in (None, '') else None
      store.add_decision(
          Decision(date=dt.date.fromisoformat(row['date']),
                   time=row.get('time', ''),
                   symbol=row['symbol'],
                   action=row['action'].strip().lower(),
                   price=num('price'),
                   quantity=num('quantity') or 0,
                   reasons=_split(row.get('reasons')),
                   against=_split(row.get('against')),
                   signals={
                       k[7:]: float(v)
                       for k, v in row.items()
                       if k.startswith('signal_') and v not in (None, '')
                   },
                   confidence=num('confidence'),
                   note=row.get('note', ''),
                   strategy_version=version))
      n += 1
  print(f'imported {n} decisions')
  store.close()


def cmd_review(args):
  cfg = load_config(args.config)
  store = open_store(cfg)
  llm = None if args.no_llm else ClaudeLLM(cfg.model)
  reviewer = Reviewer(store, build_prices(cfg.prices, cfg.base_dir), llm,
                      cfg.path(cfg.strategy_out), cfg.path(cfg.journal_dir),
                      cfg.auto_approve, cfg.effort)
  result = reviewer.run(_parse_date(args.date))
  store.close()
  ch = result['changes']
  print(f'scored outcomes: {result["scored"]}')
  print(f'decisions today: {result["decisions_today"]}, '
        f'matured: {result["matured"]}')
  if result['llm']:
    print(f'\n{result["llm"]["summary"]}')
  for c in ch['applied']:
    print(f'applied: {c["rule_id"]}.{c["field"]} {c["old"]} -> {c["new"]}')
  if ch['proposed']:
    print(f'\n{len(ch["proposed"])} change(s) await approval: '
          f'tradepilot strategy approve {ch["proposed_version"]}')
  print(f'\nreport: {result["report"]}')


def cmd_stats(args):
  cfg = load_config(args.config)
  store = open_store(cfg)
  s = store.active_strategy()
  window = store.decisions_since(stats_window_start(s, dt.date.today()))
  primary = {o.decision_id: o for o in store.outcomes(s.params.primary_horizon)}
  stats = rule_stats(window, primary)
  print(f'strategy v{s.version}, horizon {s.params.primary_horizon}d, '
        f'{len(window)} decisions in window')
  print(f'{"rule":24} {"n":>4} {"good":>5} {"bad":>4} {"hit":>5} '
        f'{"avg%":>7} {"weight":>6}')
  for rule in s.rules:
    st = stats.get(rule.id)
    if st:
      print(f'{rule.id:24} {st.n:4} {st.good:5} {st.bad:4} '
            f'{st.hit_rate:5.0%} {st.avg_ret_pct:+7.2f} {rule.weight:6.2f}'
            f'{"" if rule.enabled else "  (disabled)"}')
    else:
      print(f'{rule.id:24} {"-":>4} {"":5} {"":4} {"":5} {"":7} '
            f'{rule.weight:6.2f}')
  store.close()


def cmd_strategy(args):
  cfg = load_config(args.config)
  store = open_store(cfg)
  reviewer = Reviewer(store, None, None, cfg.path(cfg.strategy_out),
                      cfg.path(cfg.journal_dir))
  if args.action == 'list':
    for v, status, created, approved in store.list_strategies():
      print(f'v{v:<4} {status:9} created {created[:16]}'
            f'{"  approved " + approved[:16] if approved else ""}')
  elif args.action == 'show':
    s = store.strategy(
        args.version) if args.version else (store.active_strategy())
    print(
        yaml.safe_dump(json.loads(s.model_dump_json()),
                       allow_unicode=True,
                       sort_keys=False))
  elif args.action == 'diff':
    for c in store.strategy_changes(args.version):
      print(f'{c.rule_id}.{c.field}: {c.old} -> {c.new} ({c.source}) '
            f'{c.reason}')
  elif args.action in ('approve', 'reject'):
    status = store.strategy_status(args.version)
    if status != 'proposed':
      sys.exit(f'v{args.version} is {status}, not proposed')
    if args.action == 'approve':
      store.activate(args.version)
      reviewer.export_active()
      print(f'v{args.version} is now active -> {cfg.path(cfg.strategy_out)}')
    else:
      store.set_status(args.version, 'rejected')
      print(f'v{args.version} rejected')
  elif args.action == 'load':
    with open(args.file) as f:
      s = Strategy(**yaml.safe_load(f))
    s.version = store.latest_version() + 1
    store.save_strategy(s, 'proposed')
    store.activate(s.version)
    reviewer.export_active()
    print(f'loaded {args.file} as active v{s.version}')
  store.close()


def cmd_brief(args):
  """What to trade with tomorrow: active rules, pending changes, lessons."""
  cfg = load_config(args.config)
  store = open_store(cfg)
  s = store.active_strategy()
  print(f'# 다음 거래일 전략 v{s.version} ({s.name})\n')
  for r in sorted(s.rules, key=lambda r: -r.weight):
    if r.enabled:
      params = ', '.join(f'{k}={v}' for k, v in r.params.items())
      print(f'- [{r.kind}] {r.id} (weight {r.weight}): {r.description}'
            f'{" — " + params if params else ""}')
  disabled = [r.id for r in s.rules if not r.enabled]
  if disabled:
    print(f'\n비활성 규칙: {", ".join(disabled)}')
  changes = store.strategy_changes(s.version)
  if changes:
    print('\n## 이번 버전에서 바뀐 점')
    for c in changes:
      print(f'- {c.rule_id}.{c.field}: {c.old} → {c.new} — {c.reason}')
  proposed = [v for v, st, _, _ in store.list_strategies() if st == 'proposed']
  if proposed:
    print(f'\n승인 대기: v{proposed[-1]} (tradepilot strategy diff '
          f'{proposed[-1]})')
  lessons = store.recent_lessons(5)
  if lessons:
    print('\n## 최근 교훈')
    for x in lessons[:8]:
      print(f'- {x}')
  store.close()


def main(argv=None):
  parser = argparse.ArgumentParser(
      prog='tradepilot',
      description='Trade journal that reviews every decision, including '
      'trades you skipped, and improves the strategy daily.')
  parser.add_argument('-c', '--config', default='config.yaml')
  parser.add_argument('-v', '--verbose', action='store_true')
  sub = parser.add_subparsers(dest='command', required=True)

  p = sub.add_parser('init', help='write example config.yaml/strategy.yaml')
  p.add_argument('dir', nargs='?', default='.')
  p.set_defaults(func=cmd_init)

  p = sub.add_parser('log', help='record one decision (incl. pass)')
  p.add_argument('symbol')
  p.add_argument('action', choices=['buy', 'sell', 'hold', 'pass'])
  p.add_argument('--price', type=float)
  p.add_argument('--quantity', type=float)
  p.add_argument('--reasons', help='rule ids, comma separated')
  p.add_argument('--against', help='rule ids that argued the other way')
  p.add_argument('--signal', action='append', metavar='NAME=VALUE')
  p.add_argument('--confidence', type=float)
  p.add_argument('--note')
  p.add_argument('--date')
  p.add_argument('--time')
  p.set_defaults(func=cmd_log)

  p = sub.add_parser('import', help='import decisions from CSV')
  p.add_argument('csv')
  p.set_defaults(func=cmd_import)

  p = sub.add_parser('review', help='end-of-day review and strategy update')
  p.add_argument('--date')
  p.add_argument('--no-llm', action='store_true')
  p.set_defaults(func=cmd_review)

  sub.add_parser('stats',
                 help='per-rule track record').set_defaults(func=cmd_stats)
  sub.add_parser(
      'brief',
      help='strategy for the next session').set_defaults(func=cmd_brief)

  p = sub.add_parser('strategy', help='inspect/approve strategy versions')
  p.add_argument('action',
                 choices=['list', 'show', 'diff', 'approve', 'reject', 'load'])
  p.add_argument('version', nargs='?', type=int)
  p.add_argument('--file')
  p.set_defaults(func=cmd_strategy)

  args = parser.parse_args(argv)
  if args.command == 'strategy':
    if args.action in ('diff', 'approve', 'reject') and not args.version:
      parser.error(f'strategy {args.action} needs a version')
    if args.action == 'load' and not args.file:
      parser.error('strategy load needs --file')
  logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                      format='%(levelname)s %(name)s: %(message)s')
  logging.getLogger('httpx').setLevel(logging.WARNING)
  args.func(args)


if __name__ == '__main__':
  main()
