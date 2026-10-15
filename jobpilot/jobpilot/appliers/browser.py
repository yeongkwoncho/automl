"""Fills (and optionally submits) hosted application forms with Playwright."""
import logging
import os
import re
from typing import Callable, List, Optional

from jobpilot.appliers.formfill import (COLLECT_FIELDS_JS, FieldInfo, FillPlan,
                                        plan_fill, resolve_questions)
from jobpilot.models import (ApplyOutcome, CandidateProfile, Job,
                             QuestionAnswer, TailoredApplication)

logger = logging.getLogger(__name__)

CAPTCHA_SELECTOR = ('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
                    'iframe[src*="turnstile"], .g-recaptcha, .h-captcha, '
                    '.cf-turnstile')
SUBMIT_SELECTOR = ('button[type="submit"], input[type="submit"], '
                   'button:has-text("Submit application"), '
                   'button:has-text("Submit")')
APPLY_BUTTON_SELECTOR = ('a:has-text("Apply for this job"), '
                         'button:has-text("Apply for this job"), '
                         'a:has-text("Apply now"), button:has-text("Apply")')
CONFIRMATION_RE = re.compile(
    r'thank(s| you) for (applying|your (application|interest))|'
    r'application (has been |was )?(received|submitted)|'
    r'we(\'ve| have) received your application', re.I)

# Hosts where auto mode may submit; anything else is review-only.
AUTO_SUBMIT_HOSTS = ('greenhouse.io', 'lever.co', 'ashbyhq.com')

AnswerFn = Callable[[List[str]], List[QuestionAnswer]]
# Called in review mode; returns 'submit', 'skip' or 'manual'.
ConfirmFn = Callable[[Job, FillPlan, str], str]


def terminal_confirm(job: Job, plan: FillPlan, screenshot: str) -> str:
  print(f'\n=== {job.title} @ {job.company}\n{job.apply_url or job.url}')
  print(f'Filled {len(plan.actions)} fields. Screenshot: {screenshot}')
  for f in plan.unresolved:
    print(f'  ! needs your input: {f.question}')
  print('Review/edit the form in the browser window, then choose:')
  choice = input('[s]ubmit / [k]skip / [m]anual (leave for later)? ').strip()
  return {'s': 'submit', 'k': 'skip'}.get(choice[:1].lower(), 'manual')


class BrowserApplier:
  """Drives a real browser through an application form.

  mode='review': opens a visible browser, fills the form and waits for the
    user to confirm before clicking submit.
  mode='auto': submits without asking, but only on known ATS hosts, only
    when every required field was filled and no CAPTCHA is present.
    Otherwise the job is left as 'needs_manual'. CAPTCHAs are never solved
    or bypassed.
  """

  def __init__(self,
               mode: str,
               headless: bool = False,
               confirm_fn: ConfirmFn = terminal_confirm,
               executable_path: Optional[str] = None,
               timeout_ms: int = 30000):
    assert mode in ('review', 'auto'), mode
    self.mode = mode
    self.headless = headless and mode == 'auto'
    self.confirm_fn = confirm_fn
    self.executable_path = executable_path or os.environ.get(
        'JOBPILOT_CHROMIUM')
    self.timeout_ms = timeout_ms

  def apply(self, job: Job, packet: TailoredApplication,
            profile: CandidateProfile, packet_dir: str,
            answer_fn: AnswerFn) -> ApplyOutcome:
    from playwright.sync_api import Error as PlaywrightError  # pylint: disable=g-import-not-at-top
    from playwright.sync_api import sync_playwright  # pylint: disable=g-import-not-at-top

    url = job.apply_url or job.url
    if self.mode == 'auto' and not any(h in url for h in AUTO_SUBMIT_HOSTS):
      return ApplyOutcome(status='needs_manual',
                          detail='auto-submit only supports known ATS hosts')
    cover_file = os.path.join(packet_dir, 'cover_letter.txt')
    with sync_playwright() as p:
      kwargs = {'headless': self.headless}
      if self.executable_path:
        kwargs['executable_path'] = self.executable_path
      browser = p.chromium.launch(**kwargs)
      try:
        page = browser.new_page()
        page.set_default_timeout(self.timeout_ms)
        return self._run(page, job, url, packet, profile, packet_dir,
                         cover_file, answer_fn)
      except PlaywrightError as e:
        return ApplyOutcome(status='failed', detail=f'browser error: {e}')
      finally:
        browser.close()

  def _run(self, page, job, url, packet, profile, packet_dir, cover_file,
           answer_fn) -> ApplyOutcome:
    page.goto(url, wait_until='domcontentloaded')
    page.wait_for_load_state('networkidle', timeout=self.timeout_ms)
    fields = self._fields(page)
    if not any(f.type == 'file' or f.tag == 'textarea' for f in fields):
      # Job description page; follow its "Apply" button to the form.
      btn = page.locator(APPLY_BUTTON_SELECTOR).first
      if btn.count():
        btn.click()
        page.wait_for_load_state('networkidle', timeout=self.timeout_ms)
        fields = self._fields(page)
    if not fields:
      return ApplyOutcome(status='needs_manual', detail='no form found')

    plan = plan_fill(fields, profile, packet.cover_letter, cover_file)
    plan = resolve_questions(plan, answer_fn)
    self._execute(page, plan)
    # Uploading a resume can make the ATS autofill or add fields; re-check.
    refreshed = {f.index: f for f in self._fields(page)}
    plan = plan._replace(unresolved=[
        f for f in plan.unresolved
        if not (refreshed.get(f.index) and refreshed[f.index].value)
    ])

    shot = os.path.join(packet_dir, 'form.png')
    page.screenshot(path=shot, full_page=True)
    has_captcha = page.locator(CAPTCHA_SELECTOR).count() > 0

    if self.mode == 'review':
      choice = self.confirm_fn(job, plan, shot)
      if choice == 'skip':
        return ApplyOutcome(status='skipped',
                            detail='skipped in review',
                            screenshot=shot)
      if choice != 'submit':
        return ApplyOutcome(status='needs_manual',
                            detail='deferred in review',
                            screenshot=shot)
      if CONFIRMATION_RE.search(page.inner_text('body')):
        return ApplyOutcome(status='submitted',
                            detail='submitted by user',
                            screenshot=shot)
    else:
      blockers = []
      if has_captcha:
        blockers.append('captcha present')
      blockers += [
          f'unfilled required field: {f.question}' for f in plan.unresolved
          if f.required
      ]
      if blockers:
        return ApplyOutcome(status='needs_manual',
                            detail='; '.join(blockers),
                            screenshot=shot)
    return self._submit(page, shot)

  def _fields(self, page) -> List[FieldInfo]:
    return [FieldInfo(**d) for d in page.evaluate(COLLECT_FIELDS_JS)]

  def _execute(self, page, plan: FillPlan):
    loc = page.locator('input, textarea, select')
    for a in plan.actions:
      el = loc.nth(a.index)
      try:
        if a.kind == 'upload':
          el.set_input_files(a.value)
        elif a.kind == 'select':
          el.select_option(label=a.value)
        else:
          el.fill(a.value)
      except Exception as e:  # pylint: disable=broad-except
        logger.warning('could not fill %r: %s', a.label, e)

  def _submit(self, page, shot: str) -> ApplyOutcome:
    btn = page.locator(SUBMIT_SELECTOR).first
    if not btn.count():
      return ApplyOutcome(status='needs_manual',
                          detail='submit button not found',
                          screenshot=shot)
    btn.click()
    try:
      page.wait_for_load_state('networkidle', timeout=self.timeout_ms)
    except Exception:  # pylint: disable=broad-except
      pass
    page.wait_for_timeout(1500)
    after = shot.replace('form.png', 'after_submit.png')
    page.screenshot(path=after, full_page=True)
    if CONFIRMATION_RE.search(page.inner_text('body')):
      return ApplyOutcome(status='submitted', screenshot=after)
    return ApplyOutcome(
        status='needs_manual',
        screenshot=after,
        detail='clicked submit but saw no confirmation; check the screenshot')
