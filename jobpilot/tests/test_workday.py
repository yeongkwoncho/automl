import json

import httpx
import pytest

from jobpilot.models import Preferences
from jobpilot.sources import check_boards
from jobpilot.sources.workday import WorkdaySource, parse_site

API = 'https://kla.wd1.myworkdayjobs.com/wday/cxs/kla/Search'


def posting(i, title):
  return {
      'title': title,
      'externalPath': f'/job/Milpitas-CA/T_{i}',
      'locationsText': 'Milpitas, CA',
      'postedOn': 'Posted 3 Days Ago',
      'bulletFields': [f'R{i}']
  }


def make_http(pages, calls):

  def handler(request: httpx.Request):
    url = str(request.url)
    calls.append((request.method, url))
    if request.method == 'POST' and url == f'{API}/jobs':
      body = json.loads(request.content)
      page = pages[body['offset'] //
                   20] if body['offset'] // 20 < len(pages) else []
      total = sum(len(p) for p in pages) if body['offset'] == 0 else 0
      return httpx.Response(200, json={'total': total, 'jobPostings': page})
    if request.method == 'GET' and url.startswith(f'{API}/job/'):
      i = url.rsplit('_', 1)[1]
      return httpx.Response(
          200,
          json={
              'jobPostingInfo': {
                  'title':
                      f'Metrology Engineer {i}',
                  'jobReqId':
                      f'R{i}',
                  'jobDescription':
                      '<p>Optical <b>CD</b> metrology</p>',
                  'location':
                      'Milpitas, CA',
                  'startDate':
                      '2026-09-20',
                  'timeType':
                      'Full time',
                  'externalUrl':
                      f'https://kla.wd1.myworkdayjobs.com/Search/job/Milpitas-CA/T_{i}'
              }
          })
    return httpx.Response(404)

  return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_site():
  s = parse_site('https://kla.wd1.myworkdayjobs.com/en-US/Search/details/x')
  assert (s.tenant, s.site, s.company) == ('kla', 'Search', 'kla')
  s = parse_site({
      'url': 'https://micron.wd1.myworkdayjobs.com/External',
      'company': 'Micron'
  })
  assert s.api == 'https://micron.wd1.myworkdayjobs.com/wday/cxs/micron/External'
  assert s.company == 'Micron'
  with pytest.raises(ValueError):
    parse_site('https://example.com/careers')


def test_fetch_paginates_and_skips_details_for_other_titles():
  pages = [[posting(i, 'Metrology Engineer') for i in range(20)],
           [posting(20, 'Metrology Engineer'),
            posting(21, 'Accountant')]]
  calls = []
  src = WorkdaySource(make_http(pages, calls), [{
      'url': 'https://kla.wd1.myworkdayjobs.com/Search',
      'company': 'KLA'
  }],
                      delay=0)
  jobs = list(src.fetch(Preferences(titles=['Metrology Engineer'])))
  assert len(jobs) == 22
  j = jobs[0]
  assert j.uid == 'workday:kla/R0' and j.company == 'KLA'
  assert j.description == 'Optical CD metrology' and j.ats == 'workday'
  assert j.apply_url.endswith('/T_0/apply')
  assert j.posted_at.date().isoformat() == '2026-09-20'
  other = jobs[-1]
  assert other.title == 'Accountant' and other.description == ''
  assert other.posted_at is not None  # from "Posted 3 Days Ago"
  posts = [c for c in calls if c[0] == 'POST']
  gets = [c for c in calls if c[0] == 'GET']
  assert len(posts) == 2 and len(gets) == 21  # no detail fetch for Accountant


def test_check_boards_workday():
  pages = [[posting(0, 'Metrology Engineer')]]
  rows = check_boards(
      {
          'workday': {
              'sites': [{
                  'url': 'https://kla.wd1.myworkdayjobs.com/Search',
                  'company': 'KLA'
              }, 'https://nope.wd1.myworkdayjobs.com/X']
          }
      }, make_http(pages, []))
  assert rows[0] == ('workday', 'https://kla.wd1.myworkdayjobs.com/Search',
                     '1 jobs (KLA)')
  assert rows[1][2] == 'HTTP 404 (wrong slug?)'
