import json

import httpx

from jobpilot.models import Preferences
from jobpilot.sources import check_boards, configured_boards
from jobpilot.sources.apple import AppleSource


def result(i, title='Machine Learning Engineer', date='Nov 03, 2026'):
  return {
      'id': f'PIPE-{i}',
      'positionId': str(200000 + i),
      'postingTitle': title,
      'transformedPostingTitle': 'ml-engineer',
      'postingDate': date,
      'homeOffice': i == 0,
      'locations': [{
          'name': 'Cupertino',
          'countryName': 'USA'
      }],
      'team': {
          'teamName': 'Machine Learning and AI'
      },
      'jobSummary': '<p>Build models</p>'
  }


def make_http(pages, calls, token='tok123'):

  def handler(req: httpx.Request):
    calls.append(
        (req.method, req.url.path, req.headers.get('x-apple-csrf-token')))
    if req.url.path == '/api/csrfToken':
      return httpx.Response(200, headers={'x-apple-csrf-token': token})
    if req.method == 'POST' and req.url.path == '/api/role/search':
      if req.headers.get('x-apple-csrf-token') != token:
        return httpx.Response(403)
      body = json.loads(req.content)
      page = pages[body['page'] - 1] if body['page'] <= len(pages) else []
      return httpx.Response(200,
                            json={
                                'res': {
                                    'searchResults': page,
                                    'totalRecords': sum(len(p) for p in pages)
                                }
                            })
    return httpx.Response(404)

  return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_paginates_with_csrf():
  pages = [[result(i) for i in range(20)], [result(20), result(21)]]
  calls = []
  src = AppleSource(make_http(pages, calls), delay=0)
  jobs = list(src.fetch(Preferences(titles=['Machine Learning Engineer'])))
  assert len(jobs) == 22 and src.errors == []
  j = jobs[0]
  assert j.uid == 'apple:200000' and j.company == 'Apple'
  assert j.url == 'https://jobs.apple.com/en-us/details/200000/ml-engineer'
  assert j.location == 'Cupertino' and j.remote is True
  assert j.description == 'Build models'
  assert j.posted_at.date().isoformat() == '2026-11-03'
  assert j.tags == ['Machine Learning and AI']
  # Token fetched once and sent on every search.
  assert [c for c in calls if c[1] == '/api/csrfToken'
         ] == [('GET', '/api/csrfToken', None)]
  assert all(c[2] == 'tok123' for c in calls if c[0] == 'POST')


def test_errors_are_reported_not_raised():
  src = AppleSource(make_http([], [], token=''), delay=0)

  def bad(req):
    return httpx.Response(500)

  src.http = httpx.Client(transport=httpx.MockTransport(bad))
  assert list(src.fetch(Preferences(titles=['x']))) == []
  assert src.errors == [('jobs.apple.com', 'HTTP 500 from jobs.apple.com')]


def test_check_boards_and_configured_boards():
  http = make_http([[result(0)]], [])
  cfg = {
      'apple': {
          'enabled': True
      },
      'greenhouse': {
          'boards': ['stripe']
      },
      'workday': {
          'sites': [{
              'url': 'https://nvidia.wd5.myworkdayjobs.com/'
                     'NVIDIAExternalCareerSite',
              'company': 'NVIDIA'
          }]
      }
  }
  rows = [r for r in check_boards(cfg, http) if r[0] == 'apple']
  assert rows == [('apple', 'jobs.apple.com', '1 jobs (Apple)')]
  assert configured_boards(cfg) == [('greenhouse', 'stripe',
                                     'greenhouse:stripe/'),
                                    ('workday', 'NVIDIA', 'workday:nvidia/'),
                                    ('apple', 'Apple', 'apple:')]
