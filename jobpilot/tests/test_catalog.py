import yaml

from jobpilot.config import CATALOG, load_config, merge_catalog
from jobpilot.sources import configured_boards

CAT = yaml.safe_load(open(CATALOG))


def write(tmp_path, data):
  p = tmp_path / 'config.yaml'
  p.write_text(yaml.safe_dump(data))
  return str(p)


def companies(sources):
  return {label for _, label, _ in configured_boards(sources)}


def test_old_user_config_gets_new_companies(tmp_path):
  # A config written before NVIDIA/Apple were added, with its own extras.
  old = {
      'sources': {
          'greenhouse': {
              'boards': ['Stripe', 'mycompany']
          },
          'workday': {
              'max_pages':
                  2,
              'sites': [{
                  'url': 'https://KLA.wd1.myworkdayjobs.com/Search/',
                  'company': 'KLA'
              }, {
                  'url': 'https://micron.wd1.myworkdayjobs.com/External',
                  'company': 'Micron'
              }]
          },
          'remotive': {
              'enabled': True
          }
      }
  }
  cfg = load_config(write(tmp_path, old))
  got = companies(cfg.sources)
  for name in ('NVIDIA', 'Intel', 'Applied Materials', 'Qualcomm', 'Apple',
               'anthropic', 'invisix', 'mycompany'):
    assert name in got, name
  wd = cfg.sources['workday']
  assert [s['company'] for s in wd['sites']].count('KLA') == 1  # no dupes
  assert wd['max_pages'] == 2  # user setting wins
  assert cfg.sources['greenhouse']['boards'].count('Stripe') == 1
  assert 'stripe' not in cfg.sources['greenhouse']['boards']
  assert cfg.sources['remotive'] == {'enabled': True}


def test_exclude_and_disable(tmp_path):
  cfg = load_config(
      write(tmp_path, {
          'catalog_exclude': ['Apple', 'INTEL', 'airbnb'],
          'sources': {}
      }))
  got = companies(cfg.sources)
  assert 'Apple' not in got and 'Intel' not in got and 'airbnb' not in got
  assert 'NVIDIA' in got
  cfg = load_config(
      write(tmp_path, {
          'catalog': False,
          'sources': {
              'lever': {
                  'boards': ['x']
              }
          }
      }))
  assert companies(cfg.sources) == {'x'}


def test_user_can_switch_off_a_catalog_source(tmp_path):
  cfg = load_config(write(tmp_path, {'sources': {'apple': {'enabled': False}}}))
  assert 'Apple' not in companies(cfg.sources)


def test_merge_does_not_mutate_inputs():
  user = {'lever': {'boards': ['a']}}
  merge_catalog(user, CAT)
  assert user == {'lever': {'boards': ['a']}}
