import httpx
import pytest

from jobpilot.models import Preferences
from jobpilot.sources import check_boards
from jobpilot.sources.eightfold import EightfoldSource, parse_site

BASE = 'https://careers.qualcomm.com'
SITE = {'url': BASE, 'company': 'Qualcomm'}


def v2_position(i, title):
  return {
      'id': 446721161179 + i,
      'name': title,
      'location': 'San Diego, California, United States of America',
      't_create': 1759000000,
      'department': 'Engineering',
      'canonicalPositionUrl': f'{BASE}/careers/job/{446721161179 + i}',
      'work_location_option': 'onsite'
  }


def make_http(api, positions, calls):

  def handler(request: httpx.Request):
    url, q = request.url, dict(request.url.params)
    calls.append(str(url))
    assert q.get('domain') == 'qualcomm.com'
    path = url.path
    if api == 'v2' and path == '/api/apply/v2/jobs':
      start = int(q['start'])
      return httpx.Response(200,
                            json={
                                'count': len(positions),
                                'positions': positions[start:start + 10]
                            })
    if api == 'v2' and path.startswith('/api/apply/v2/jobs/'):
      return httpx.Response(
          200,
          json={
              'id': int(path.rsplit('/', 1)[1]),
              'job_description': '<p>Wafer <b>metrology</b> for modems</p>'
          })
    if api == 'pcsx' and path == '/api/pcsx/search':
      start = int(q['start'])
      return httpx.Response(200,
                            json={
                                'status': 200,
                                'data': {
                                    'count': len(positions),
                                    'positions': positions[start:start + 10]
                                }
                            })
    if api == 'pcsx' and path == '/api/pcsx/position_details':
      return httpx.Response(
          200, json={'data': {
              'jobDescription': '<p>Overlay metrology</p>'
          }})
    return httpx.Response(404)

  return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_site():
  s = parse_site(
      'https://careers.qualcomm.com/careers/job/446721161179?hl=en-US')
  assert s == ('https://careers.qualcomm.com', 'qualcomm.com', 'Qualcomm')
  s = parse_site({
      'url': 'https://jobs.example.co.kr',
      'domain': 'example.co.kr',
      'company': 'Example'
  })
  assert s.domain == 'example.co.kr'


def test_v2_api():
  positions = [v2_position(i, 'Metrology Engineer') for i in range(10)]
  positions.append(v2_position(10, 'Accountant'))
  calls = []
  src = EightfoldSource(make_http('v2', positions, calls), [SITE], delay=0)
  jobs = list(src.fetch(Preferences(titles=['Metrology Engineer'])))
  assert len(jobs) == 11
  j = jobs[0]
  assert j.uid == 'eightfold:qualcomm.com/446721161179'
  assert j.url == f'{BASE}/careers/job/446721161179'
  assert j.company == 'Qualcomm' and j.ats == 'eightfold'
  assert j.description == 'Wafer metrology for modems'
  assert j.posted_at.year == 2025 and j.tags == ['Engineering']
  assert jobs[-1].description == ''  # no detail fetch for Accountant
  details = [c for c in calls if '/api/apply/v2/jobs/' in c]
  assert len(details) == 10


def test_falls_back_to_pcsx_api():
  positions = [{
      'id': 7,
      'name': 'Metrology Engineer',
      'locations': ['Hsinchu, Taiwan'],
      'postedTs': 1759000000,
      'positionUrl': '/careers/job/7'
  }]
  src = EightfoldSource(make_http('pcsx', positions, []), [SITE], delay=0)
  [j] = list(src.fetch(Preferences(titles=['Metrology Engineer'])))
  assert j.url == f'{BASE}/careers/job/7' and j.location == 'Hsinchu, Taiwan'
  assert j.description == 'Overlay metrology'


def test_check_boards_eightfold():
  rows = check_boards({'eightfold': {
      'sites': [SITE]
  }}, make_http('pcsx', [{
      'id': 1,
      'name': 'x'
  }], []))
  assert rows == [('eightfold', BASE, '1 jobs (Qualcomm, pcsx API)')]


def test_unreachable_site_is_isolated():
  src = EightfoldSource(make_http('none', [], []), [SITE], delay=0)
  assert list(src.fetch(Preferences(titles=['x']))) == []
  with pytest.raises(httpx.HTTPStatusError):
    src.check(SITE)
