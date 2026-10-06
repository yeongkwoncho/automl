"""Command line interface: `jobpilot <command>`."""
import argparse
import json
import logging
import os
import shutil
import sys

import httpx
import yaml

from jobpilot.agent import ProfileImporter
from jobpilot.config import load_config, missing_profile_fields
from jobpilot.llm import ClaudeLLM
from jobpilot.pipeline import Pipeline
from jobpilot.sources import check_boards
from jobpilot.store import Store

EXAMPLES = os.path.join(os.path.dirname(__file__), 'examples')


def cmd_init(args):
  for name in ('config.yaml', 'profile.yaml'):
    dst = os.path.join(args.dir, name)
    if os.path.exists(dst):
      print(f'exists, not overwriting: {dst}')
      continue
    shutil.copy(os.path.join(EXAMPLES, name), dst)
    print(f'wrote {dst}')
  print('\nNext: edit profile.yaml (or run `jobpilot import-resume '
        'resume.pdf`), then config.yaml preferences/sources.')


def cmd_import_resume(args):
  cfg = load_config(args.config)
  profile = ProfileImporter(ClaudeLLM(cfg.llm.model)).run(args.resume)
  out = args.output or cfg.path(cfg.profile)
  if os.path.exists(out) and not args.force:
    sys.exit(f'{out} exists; pass --force to overwrite')
  with open(out, 'w') as f:
    yaml.safe_dump(profile.model_dump(), f, allow_unicode=True, sort_keys=False)
  print(f'wrote {out} -- review it: everything in it may be used in '
        'applications.')


def _pipeline(args):
  cfg = load_config(args.config)
  if getattr(args, 'mode', None):
    cfg.apply.mode = args.mode
  profile = cfg.load_profile()
  missing = missing_profile_fields(profile)
  if missing:
    sys.exit(f'profile is missing: {", ".join(missing)}')
  store = Store(cfg.path(cfg.database))
  return Pipeline(cfg, profile, ClaudeLLM(cfg.llm.model), store), store


def cmd_step(args):
  pipe, store = _pipeline(args)
  try:
    if args.command == 'search':
      result = pipe.search()
    elif args.command == 'match':
      result = pipe.match(limit=args.limit)
    elif args.command == 'apply':
      result = pipe.apply(limit=args.limit)
    else:
      result = pipe.run()
    print(json.dumps(result, indent=1))
  finally:
    store.close()


def cmd_status(args):
  cfg = load_config(args.config)
  store = Store(cfg.path(cfg.database))
  print(json.dumps(store.stats(), indent=1))
  for r in store.report(args.status):
    print(f'[{r["status"]:12}] {r["score"] or "-":>3}  {r["title"]} @ '
          f'{r["company"]}\n{"":17}{r["url"]}')
    if r['detail']:
      print(f'{"":17}{r["detail"]}')
    if r['packet_dir']:
      print(f'{"":17}{r["packet_dir"]}')
  store.close()


def cmd_check_boards(args):
  cfg = load_config(args.config)
  with httpx.Client(timeout=30, follow_redirects=True) as http:
    rows = check_boards(cfg.sources, http)
  for source, board, result in rows:
    print(f'{source:11} {board:24} {result}')
  if not rows:
    print('no company boards configured under sources:')


def cmd_mark(args):
  cfg = load_config(args.config)
  store = Store(cfg.path(cfg.database))
  ok = store.set_status(args.uid, args.status, args.note or 'marked by user')
  store.close()
  sys.exit(0 if ok else f'no application for {args.uid}')


def main(argv=None):
  parser = argparse.ArgumentParser(
      prog='jobpilot',
      description='Find matching jobs on global boards and apply with AI help.')
  parser.add_argument('-c', '--config', default='config.yaml')
  parser.add_argument('-v', '--verbose', action='store_true')
  sub = parser.add_subparsers(dest='command', required=True)

  p = sub.add_parser('init', help='write example config.yaml/profile.yaml')
  p.add_argument('dir', nargs='?', default='.')
  p.set_defaults(func=cmd_init)

  p = sub.add_parser('import-resume', help='build profile.yaml from a resume')
  p.add_argument('resume')
  p.add_argument('-o', '--output')
  p.add_argument('--force', action='store_true')
  p.set_defaults(func=cmd_import_resume)

  sub.add_parser('search',
                 help='fetch jobs and prefilter').set_defaults(func=cmd_step)
  for name, help_ in (('match', 'score new jobs with Claude'),
                      ('apply', 'tailor and apply to top matches')):
    p = sub.add_parser(name, help=help_)
    p.add_argument('--limit', type=int)
    if name == 'apply':
      p.add_argument('--mode', choices=['dry_run', 'review', 'auto'])
    p.set_defaults(func=cmd_step)
  p = sub.add_parser('run', help='search + match + apply')
  p.add_argument('--mode', choices=['dry_run', 'review', 'auto'])
  p.set_defaults(func=cmd_step)

  p = sub.add_parser('status', help='show application history')
  p.add_argument('--status')
  p.set_defaults(func=cmd_status)

  sub.add_parser('check-boards',
                 help='verify configured company board slugs').set_defaults(
                     func=cmd_check_boards)

  p = sub.add_parser('mark', help='set status after applying by hand')
  p.add_argument('uid')
  p.add_argument('status', choices=['submitted', 'skipped', 'needs_manual'])
  p.add_argument('--note')
  p.set_defaults(func=cmd_mark)

  args = parser.parse_args(argv)
  logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                      format='%(levelname)s %(name)s: %(message)s')
  logging.getLogger('httpx').setLevel(logging.WARNING)
  args.func(args)


if __name__ == '__main__':
  main()
