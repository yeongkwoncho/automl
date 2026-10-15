import httpx

from jobpilot.models import Preferences
from jobpilot.sources import build_sources, check_boards
from jobpilot.sources.ats import (AshbySource, GreenhouseSource, LeverSource,
                                  RecruiteeSource)
from jobpilot.sources.boards import RemoteOKSource, RemotiveSource


def client(routes):

  def handler(request: httpx.Request):
    for prefix, body in routes.items():
      if str(request.url).startswith(prefix):
        return httpx.Response(200, json=body)
    return httpx.Response(404)

  return httpx.Client(transport=httpx.MockTransport(handler))


PREFS = Preferences(titles=['ML Engineer'])


def test_greenhouse():
  http = client({
      'https://boards-api.greenhouse.io/v1/boards/acme/jobs': {
          'jobs': [{
              'id': 7,
              'title': 'ML Engineer',
              'company_name': 'Acme',
              'absolute_url': 'https://job-boards.greenhouse.io/acme/jobs/7',
              'location': {
                  'name': 'Remote - APAC'
              },
              'content': '&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;',
              'updated_at': '2026-09-01T10:00:00-04:00'
          }]
      }
  })
  jobs = list(GreenhouseSource(http, ['acme', 'missing']).fetch(PREFS))
  assert len(jobs) == 1  # missing board 404s but does not abort
  j = jobs[0]
  assert j.uid == 'greenhouse:acme/7' and j.company == 'Acme'
  assert j.remote and j.description == 'Build & ship' and j.ats == 'greenhouse'
  assert j.posted_at.year == 2026


def test_lever():
  http = client({
      'https://api.lever.co/v0/postings/acme': [{
          'id': 'abc',
          'text': 'ML Engineer',
          'hostedUrl': 'https://jobs.lever.co/acme/abc',
          'categories': {
              'location': 'Seoul',
              'commitment': 'Full-time',
              'team': 'AI'
          },
          'descriptionPlain': 'About',
          'lists': [{
              'text': 'Requirements',
              'content': '<li>PyTorch</li>'
          }],
          'createdAt': 1756000000000,
          'workplaceType': 'onsite'
      }]
  })
  j = next(LeverSource(http, ['acme']).fetch(PREFS))
  assert j.apply_url == 'https://jobs.lever.co/acme/abc/apply'
  assert 'Requirements' in j.description and '- PyTorch' in j.description
  assert j.employment_type == 'Full-time' and j.tags == ['AI']
  assert j.remote is None


def test_ashby_skips_unlisted():
  http = client({
      'https://api.ashbyhq.com/posting-api/job-board/acme': {
          'jobs': [{
              'id': '1',
              'title': 'ML Engineer',
              'jobUrl': 'https://jobs.ashbyhq.com/acme/1',
              'location': 'Remote',
              'isRemote': True,
              'descriptionPlain': 'x',
              'isListed': True
          }, {
              'id': '2',
              'title': 'Hidden',
              'jobUrl': 'u',
              'isListed': False
          }]
      }
  })
  jobs = list(AshbySource(http, ['acme']).fetch(PREFS))
  assert [j.external_id for j in jobs] == ['acme/1']


def test_remotive_and_remoteok():
  http = client({
      'https://remotive.com/api/remote-jobs': {
          'jobs': [{
              'id': 1,
              'url': 'https://remotive.com/j/1',
              'title': 'ML Engineer',
              'company_name': 'R',
              'candidate_required_location': 'Worldwide',
              'description': '<b>hi</b>',
              'publication_date': '2026-09-20T00:00:00'
          }]
      },
      'https://remoteok.com/api': [{
          'legal': 'notice'
      }, {
          'id': '9',
          'position': 'ML Engineer',
          'company': 'O',
          'url': 'https://remoteok.com/9',
          'epoch': 1758000000,
          'apply_url': 'https://jobs.lever.co/o/9',
          'salary_max': 200000
      }]
  })
  r = list(RemotiveSource(http).fetch(PREFS))
  assert r[0].remote and r[0].description == 'hi'
  o = list(RemoteOKSource(http).fetch(PREFS))
  assert len(o) == 1 and o[0].ats == 'lever' and o[0].salary_currency == 'USD'


def test_build_sources_skips_missing_keys(monkeypatch):
  monkeypatch.delenv('ADZUNA_APP_ID', raising=False)
  srcs = build_sources(
      {
          'greenhouse': {
              'boards': ['a']
          },
          'remotive': {
              'enabled': True
          },
          'adzuna': {
              'countries': ['us'],
              'app_id_env': 'ADZUNA_APP_ID',
              'app_key_env': 'ADZUNA_APP_KEY'
          }
      }, httpx.Client())
  assert [s.name for s in srcs] == ['greenhouse', 'remotive']


def test_recruitee():
  http = client({
      'https://invisix.recruitee.com/api/offers/': {
          'offers': [{
              'id':
                  11,
              'slug':
                  'sensing-calibration-architect',
              'title':
                  'Sensing & Calibration Architect',
              'company_name':
                  'Invisix',
              'city':
                  'Eindhoven',
              'country':
                  'Netherlands',
              'location':
                  '',
              'remote':
                  False,
              'status':
                  'published',
              'careers_url':
                  'https://invisix.recruitee.com/o/sensing-calibration-architect',
              'description':
                  '<p>Own the sensing architecture.</p>',
              'requirements':
                  '<ul><li>Optics</li></ul>',
              'published_at':
                  '2026-09-01 10:00:00 UTC',
              'employment_type_code':
                  'fulltime_permanent',
              'department':
                  'R&D'
          }, {
              'id': 12,
              'slug': 'old',
              'title': 'Closed role',
              'status': 'closed'
          }]
      }
  })
  [j] = list(RecruiteeSource(http, ['invisix']).fetch(PREFS))
  assert j.uid == 'recruitee:invisix/11' and j.company == 'Invisix'
  assert j.location == 'Eindhoven, Netherlands' and j.remote is None
  assert j.apply_url.endswith('/o/sensing-calibration-architect/c/new')
  assert 'Own the sensing' in j.description and '- Optics' in j.description
  assert j.posted_at.isoformat() == '2026-09-01T10:00:00+00:00'
  assert j.tags == ['R&D'] and j.ats == 'recruitee'


def test_check_boards():
  http = client({
      'https://api.lever.co/v0/postings/gausslabs': [],
      'https://invisix.recruitee.com/api/offers/': {
          'offers': []
      }
  })
  rows = check_boards(
      {
          'lever': {
              'boards': ['gausslabs', 'typo']
          },
          'recruitee': {
              'boards': ['invisix']
          }
      }, http)
  assert rows == [('lever', 'gausslabs', '0 jobs'),
                  ('lever', 'typo', 'HTTP 404 (wrong slug?)'),
                  ('recruitee', 'invisix', '0 jobs')]
