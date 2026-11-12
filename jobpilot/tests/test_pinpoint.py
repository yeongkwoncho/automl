import httpx

from jobpilot.config import CATALOG, merge_catalog
from jobpilot.models import Preferences
from jobpilot.sources import check_boards, configured_boards
from jobpilot.sources.ats import PinpointSource
import yaml

PREFS = Preferences(titles=['Process Engineer'])

JSON_FEED = {
    'data': [{
        'id': '9876',
        'title': 'Senior Epitaxy Engineer',
        'url': 'https://plessey.pinpointhq.com/postings/63b1c550',
        'location': {
            'city': 'Plymouth',
            'province': 'Devon',
            'name': 'Plymouth'
        },
        'workplace_type': 'onsite',
        'employment_type': 'Full Time',
        'department': {
            'name': 'R&D'
        },
        'description': '<p>Grow <b>GaN</b> wafers</p>',
        'key_responsibilities': '<ul><li>MOCVD</li></ul>',
        'published_at': '2026-10-30T09:00:00Z'
    }]
}

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Shift Equipment Engineer</title>
<link>https://plessey.pinpointhq.com/postings/547164</link>
<description>&lt;p&gt;Keep tools running&lt;/p&gt;</description>
<pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate>
<category>Engineering</category></item></channel></rss>"""


def client(routes):

  def handler(req: httpx.Request):
    r = routes.get(req.url.path)
    if r is None:
      return httpx.Response(404)
    return httpx.Response(200, json=r) if isinstance(
        r, (dict, list)) else (httpx.Response(200, text=r))

  return httpx.Client(transport=httpx.MockTransport(handler))


def test_json_feed():
  src = PinpointSource(client({'/postings.json': JSON_FEED}), ['plessey'])
  [j] = list(src.fetch(PREFS))
  assert j.uid == 'pinpoint:plessey/9876'
  assert j.title == 'Senior Epitaxy Engineer' and j.ats == 'pinpoint'
  assert j.url == 'https://plessey.pinpointhq.com/postings/63b1c550'
  assert j.location.startswith('Plymouth')
  assert 'Grow GaN wafers' in j.description and '- MOCVD' in j.description
  assert j.employment_type == 'Full Time' and j.tags == ['R&D']
  assert j.posted_at.year == 2026 and not j.remote and j.company == 'plessey'


def test_rss_fallback_and_check():
  http = client({'/jobs.rss': RSS})
  src = PinpointSource(http, ['plessey'])
  [j] = list(src.fetch(PREFS))
  assert j.title == 'Shift Equipment Engineer' and j.external_id == (
      'plessey/547164')
  assert j.description == 'Keep tools running' and j.tags == ['Engineering']
  rows = check_boards({'pinpoint': {'boards': ['plessey']}}, http)
  assert rows == [('pinpoint', 'plessey', '1 jobs (rss feed)')]


def test_missing_board_is_reported_not_raised():
  src = PinpointSource(client({}), ['nope'])
  assert list(src.fetch(PREFS)) == []
  assert src.errors == [('nope', 'HTTP 404 from nope.pinpointhq.com')]


def test_plessey_is_in_the_catalog():
  cat = yaml.safe_load(open(CATALOG))
  merged = merge_catalog({}, cat)
  assert ('pinpoint', 'plessey',
          'pinpoint:plessey/') in configured_boards(merged)
  assert 'plessey' not in merge_catalog({}, cat,
                                        ['Plessey'])['pinpoint']['boards']
