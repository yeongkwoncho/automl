import datetime as dt
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from conftest import make_job
from jobpilot import web
from jobpilot.models import MatchAssessment
from jobpilot.store import Store

CHROMIUM = os.environ.get('JOBPILOT_CHROMIUM', '/opt/pw-browsers/chromium')


@pytest.fixture
def server(tmp_path):
  store = Store(str(tmp_path / 'db.sqlite'))
  now = dt.datetime.now(dt.timezone.utc)
  jobs = [
      (make_job(external_id='kla/1',
                title='Metrology Engineer',
                company='KLA',
                location='Milpitas, CA',
                source='workday'), None),
      (make_job(external_id='acme/2',
                title='ML Engineer',
                location='Seoul',
                tags=['AI']), None),
      (make_job(external_id='acme/3',
                title='Account Executive',
                location='Seoul',
                posted_at=now - dt.timedelta(days=60)), 'title does not match'),
      (make_job(external_id='x/4',
                title='<script>alert(1)</script>',
                company='Evil',
                url='javascript:alert(1)'), 'location: Mars'),
  ]
  for job, reason in jobs:
    store.add_job(job, reason)
  store.save_match(
      'workday:kla/1',
      MatchAssessment(score=88,
                      verdict='strong',
                      summary='Great fit',
                      matched_strengths=['CD'],
                      gaps=[],
                      dealbreakers=[],
                      seniority_fit='match'))
  srv = web.make_server(store, 0, {'min_score': 75})
  t = threading.Thread(target=srv.serve_forever, daemon=True)
  t.start()
  yield f'http://127.0.0.1:{srv.server_port}', store
  srv.shutdown()
  srv.server_close()
  store.close()


def get(url):
  with urllib.request.urlopen(url) as r:
    return r.status, json.loads(r.read())


def post(url, body, headers=None):
  req = urllib.request.Request(url,
                               data=json.dumps(body).encode(),
                               method='POST',
                               headers={
                                   'Content-Type': 'application/json',
                                   **(headers or {})
                               })
  try:
    with urllib.request.urlopen(req) as r:
      return r.status, json.loads(r.read())
  except urllib.error.HTTPError as e:
    return e.code, json.loads(e.read())


def test_api(server):
  base, store = server
  status, data = get(base + '/api/jobs')
  assert status == 200 and len(data['jobs']) == 4
  kla = next(j for j in data['jobs'] if j['company'] == 'KLA')
  assert kla['score'] == 88 and kla['verdict'] == 'strong'
  assert kla['source'] == 'workday' and not kla['selected']
  assert 'description' not in kla  # list stays light
  status, d = get(base + '/api/job?uid=' + urllib.request.quote(kla['uid']))
  assert d['match']['summary'] == 'Great fit' and d['description'] == 'PyTorch'

  status, r = post(base + '/api/select', {
      'uids': [kla['uid'], 'nope:1'],
      'selected': True
  })
  assert status == 200 and r['selected_count'] == 1
  assert store.selected_uids() == [kla['uid']]
  post(base + '/api/select', {'uids': [kla['uid']], 'selected': False})
  assert store.selected_uids() == []


def test_rejects_cross_origin_and_bad_requests(server):
  base, store = server
  uid = 'workday:kla/1'
  assert post(base + '/api/select', {
      'uids': [uid],
      'selected': True
  }, {'Origin': 'https://evil.example'})[0] == 403
  req = urllib.request.Request(base + '/api/select',
                               data=b'uids=x',
                               method='POST',
                               headers={'Content-Type': 'text/plain'})
  with pytest.raises(urllib.error.HTTPError) as e:
    urllib.request.urlopen(req)
  assert e.value.code == 415
  assert post(base + '/api/select', {'nope': 1})[0] == 400
  assert store.selected_uids() == []


def test_page_filters_and_selection(server, tmp_path):
  pytest.importorskip('playwright.sync_api')
  if not os.path.exists(CHROMIUM):
    pytest.skip('chromium not available')
  from playwright.sync_api import sync_playwright
  base, store = server
  with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROMIUM)
    page = b.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('dialog', lambda d: errors.append('dialog: ' + d.message))
    page.goto(base + '/')
    page.wait_for_selector('#rows tr')
    rows = lambda: page.locator('#rows tr').count()
    # Default view: jobs that passed the prefilter.
    assert rows() == 2
    # Companies whose jobs were all filtered out are still listed (dimmed),
    # and picking one explains why the list is empty.
    evil = page.locator('#companies label', has_text='Evil')
    assert evil.count() == 1 and 'zero' in evil.get_attribute('class')
    assert evil.locator('.n').inner_text() == '0 / 1'
    evil.locator('input').check()
    assert rows() == 0 and "'전체'" in page.inner_text('#empty')
    evil.locator('input').uncheck()
    page.click('#status label:has(input[value=all])')
    assert rows() == 4
    # Untrusted titles render as text, javascript: URLs are not linked.
    assert page.locator('#rows').inner_text().count(
        '<script>alert(1)</script>') == 1
    assert page.locator('a[href^="javascript"]').count() == 0
    # Search, source facet, score filter.
    page.fill('#q', 'metrology')
    page.wait_for_timeout(300)
    assert rows() == 1
    page.fill('#q', '')
    page.wait_for_timeout(300)
    page.locator('#sources label', has_text='workday').locator('input').check()
    assert rows() == 1
    page.locator('#sources label',
                 has_text='workday').locator('input').uncheck()
    page.select_option('#score', '85')
    assert rows() == 1
    page.select_option('#score', 'any')
    page.click('#status label:has(input[value=rejected])')
    page.locator('#reasons label',
                 has_text='title does not match').locator('input').check()
    assert rows() == 1
    page.click('#reset')
    assert rows() == 2
    # Select a job: saved server-side, and counted in the header.
    page.locator('#rows tr',
                 has_text='Metrology Engineer').locator('input').check()
    page.wait_for_timeout(300)
    assert store.selected_uids() == ['workday:kla/1']
    assert '선택 1' in page.inner_text('#counts')
    page.check('#onlySel')
    assert rows() == 1
    # Detail drawer.
    page.locator('#rows tr',
                 has_text='Metrology Engineer').locator('td').nth(2).click()
    page.wait_for_selector('#drawer.open')
    assert 'Great fit' in page.inner_text('#dBody')
    page.screenshot(path=str(tmp_path / 'web.png'), full_page=True)
    # Filters survive a reload (localStorage).
    page.reload()
    page.wait_for_selector('#rows tr')
    assert page.is_checked('#onlySel') and rows() == 1
    b.close()
  assert errors == []
