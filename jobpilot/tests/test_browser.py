import json
import os

import pytest

from conftest import FakeLLM, make_job
from jobpilot.agent import Tailor
from jobpilot.appliers import browser
from jobpilot.models import TailoredApplication

pytest.importorskip('playwright.sync_api')

FORM = 'file://' + os.path.join(os.path.dirname(__file__), 'fixtures',
                                'form.html')
CHROMIUM = os.environ.get('JOBPILOT_CHROMIUM', '/opt/pw-browsers/chromium')


def run(tmp_path, profile, prefs, mode, confirm='submit', url=FORM):
  if not os.path.exists(CHROMIUM):
    pytest.skip('chromium not available')
  job = make_job(apply_url=url)
  packet = TailoredApplication(cover_letter='Hello team',
                               resume_highlights=[],
                               answers=[])
  tailor = Tailor(FakeLLM(), profile, prefs)
  (tmp_path / 'cover_letter.txt').write_text('Hello team')
  applier = browser.BrowserApplier(mode=mode,
                                   headless=True,
                                   confirm_fn=lambda *a: confirm,
                                   executable_path=CHROMIUM,
                                   timeout_ms=10000)
  applier.headless = True  # review mode normally forces a visible window
  return applier.apply(job, packet, profile, str(tmp_path),
                       lambda qs: tailor.answer(job, qs))


def test_review_submit(tmp_path, profile, prefs):
  out = run(tmp_path, profile, prefs, 'review')
  assert out.status == 'submitted', out.detail
  assert os.path.exists(tmp_path / 'form.png')


def test_review_skip(tmp_path, profile, prefs):
  assert run(tmp_path, profile, prefs, 'review',
             confirm='skip').status == 'skipped'


def test_auto_rejects_unknown_host(tmp_path, profile, prefs):
  out = run(tmp_path, profile, prefs, 'auto')
  assert out.status == 'needs_manual' and 'ATS' in out.detail


def test_auto_submits_on_allowed_host(tmp_path, profile, prefs, monkeypatch):
  monkeypatch.setattr(browser, 'AUTO_SUBMIT_HOSTS', ('file://',))
  out = run(tmp_path, profile, prefs, 'auto')
  assert out.status == 'submitted', out.detail


def test_submitted_values(tmp_path, profile, prefs, monkeypatch):
  captured = {}
  orig = browser.BrowserApplier._submit

  def spy(self, page, shot):
    out = orig(self, page, shot)
    captured['body'] = page.inner_text('#out')
    return out

  monkeypatch.setattr(browser.BrowserApplier, '_submit', spy)
  assert run(tmp_path, profile, prefs, 'review').status == 'submitted'
  data = json.loads(captured['body'])
  assert data['first_name'] == 'Jane' and data['last_name'] == 'Doe'
  assert data['email'] == 'jane@example.com' and data['phone'] == profile.phone
  assert data['cover'] == 'Hello team' and data['yrs'] == '5'
  assert data['gender'] == 'Decline to self-identify'


def test_auto_stops_on_required_consent(tmp_path, profile, prefs, monkeypatch):
  monkeypatch.setattr(browser, 'AUTO_SUBMIT_HOSTS', ('file://',))
  html = open(FORM[len('file://'):]).read().replace('class="consent"',
                                                    'required')
  form = tmp_path / 'form_consent.html'
  form.write_text(html)
  out = run(tmp_path, profile, prefs, 'auto', url='file://' + str(form))
  assert out.status == 'needs_manual' and 'privacy policy' in out.detail
