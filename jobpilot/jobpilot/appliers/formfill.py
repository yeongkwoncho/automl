"""Browser-independent logic that decides what to type into each form field.

Kept separate from Playwright so it can be unit tested. Field descriptors
come from `COLLECT_FIELDS_JS` running in the page.
"""
import re
from typing import Callable, Dict, List, NamedTuple, Optional

from pydantic import BaseModel, Field

from jobpilot.models import CandidateProfile, QuestionAnswer

# Runs in the page; returns one descriptor per input/textarea/select in
# document order, so `locator('input, textarea, select').nth(index)` matches.
COLLECT_FIELDS_JS = r"""
() => Array.from(document.querySelectorAll('input, textarea, select')).map((el, i) => {
  const text = n => (n && n.innerText || '').trim();
  // A select still showing its "Select..." placeholder counts as empty.
  const currentValue = el => {
    if (el.type === 'checkbox' || el.type === 'radio') return el.checked ? 'checked' : '';
    if (el.type === 'file') return el.files.length ? 'file' : '';
    if (el.tagName !== 'SELECT') return el.value || '';
    const opt = el.options[el.selectedIndex];
    if (!opt || !el.value || /^(select|choose|please|--)/i.test(opt.text.trim())) return '';
    return opt.text.trim();
  };
  let label = '';
  if (el.id) label = text(document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
  if (!label) label = text(el.closest('label'));
  if (!label) label = el.getAttribute('aria-label') || '';
  if (!label && el.getAttribute('aria-labelledby'))
    label = el.getAttribute('aria-labelledby').split(/\s+/)
      .map(id => text(document.getElementById(id))).join(' ');
  if (!label) {
    const box = el.closest('fieldset, .field, .application-question, [class*="question"], [class*="field"]');
    if (box) label = text(box.querySelector('legend, label, .application-label, [class*="label"]'));
  }
  const type = (el.getAttribute('type') || el.tagName).toLowerCase();
  const style = window.getComputedStyle(el);
  const visible = type === 'file' || (style.display !== 'none' &&
      style.visibility !== 'hidden' && el.getClientRects().length > 0);
  return {
    index: i, tag: el.tagName.toLowerCase(), type,
    label: label.replace(/\s+/g, ' ').trim(), name: el.name || '',
    placeholder: el.placeholder || '',
    required: el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label.trim()),
    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean) : [],
    visible, value: currentValue(el),
  };
})
"""

_SKIP_TYPES = {'hidden', 'submit', 'button', 'reset', 'image', 'search'}

_EEO = re.compile(r'gender|race|ethnic|hispanic|latino|veteran|disabilit|'
                  r'pronoun|sexual orientation|transgender|self[- ]identif')
_DECLINE = re.compile(
    r"decline|prefer not|don.?t wish|not to (say|answer|disclose)|"
    r'choose not')


class FieldInfo(BaseModel):
  index: int
  tag: str
  type: str
  label: str = ''
  name: str = ''
  placeholder: str = ''
  required: bool = False
  options: List[str] = Field(default_factory=list)
  visible: bool = True
  value: str = ''

  @property
  def key(self) -> str:
    return f'{self.label} {self.name} {self.placeholder}'.strip().lower()

  @property
  def question(self) -> str:
    q = self.label or self.placeholder or self.name
    if self.options:
      q += ' (options: ' + ' | '.join(self.options) + ')'
    return q


class FillAction(NamedTuple):
  index: int
  kind: str  # 'fill' | 'select' | 'upload'
  value: str
  label: str


class FillPlan(NamedTuple):
  actions: List[FillAction]
  # Fields we could not fill confidently; required ones block auto-submit.
  unresolved: List[FieldInfo]
  questions: List[FieldInfo]


def _profile_fields(p: CandidateProfile) -> List[tuple]:
  current = p.experience[0] if p.experience else None
  return [
      (r'current.?(company|employer)|\borg\b',
       current.company if current else ''),
      (r'current.?(title|role|position)', current.title if current else ''),
      (r'first.?name|given.?name', p.first_name),
      (r'last.?name|surname|family.?name', p.last_name),
      (r'preferred.?name', p.first_name),
      (r'^name\b|full.?name|your name|성명|이름', p.full_name),
      (r'e-?mail', p.email),
      (r'phone|mobile|telephone|연락처|전화', p.phone),
      (r'linkedin', p.linkedin),
      (r'github', p.github),
      (r'website|portfolio|personal (site|url)|other url|blog', p.website or
       p.github),
      (r'\blocation\b|\bcity\b|where are you (based|located)', p.location),
  ]


def match_option(answer: str, options: List[str]) -> Optional[str]:
  """Maps a free-text answer onto one of a select's options."""
  a = answer.strip().lower()
  if not a:
    return None
  for o in options:
    if o.lower() == a:
      return o
  hits = [o for o in options if a in o.lower() or o.lower() in a]
  real = [o for o in hits if not re.match(r'^(select|choose|--)', o.lower())]
  return real[0] if len(real) == 1 else None


def plan_fill(fields: List[FieldInfo], profile: CandidateProfile,
              cover_letter: str, cover_letter_file: Optional[str]) -> FillPlan:
  """First pass: fill everything answerable from the profile alone."""
  actions, unresolved, questions = [], [], []
  canned = {k.lower(): v for k, v in profile.answers.items()}

  for f in fields:
    if f.type in _SKIP_TYPES or not f.visible or f.value:
      continue
    key = f.key

    if f.type == 'file':
      if re.search(r'resume|cv|이력서', key) and profile.resume_path:
        actions.append(
            FillAction(f.index, 'upload', profile.resume_path, f.label))
      elif re.search(r'cover', key) and cover_letter_file:
        actions.append(FillAction(f.index, 'upload', cover_letter_file,
                                  f.label))
      elif f.required:
        unresolved.append(f)
      continue

    if f.type in ('checkbox', 'radio'):
      # Consent boxes and radio groups are left to a human on purpose.
      if f.required:
        unresolved.append(f)
      continue

    if _EEO.search(key):
      value = next((v for k, v in profile.eeo.items() if k.lower() in key), '')
      if f.tag == 'select':
        opt = match_option(value, f.options) if value else next(
            (o for o in f.options if _DECLINE.search(o.lower())), None)
        if opt:
          actions.append(FillAction(f.index, 'select', opt, f.label))
        elif f.required:
          unresolved.append(f)
      elif value:
        actions.append(FillAction(f.index, 'fill', value, f.label))
      elif f.required:
        unresolved.append(f)
      continue

    value = None
    if f.tag == 'textarea' and re.search(
        r'cover.?letter|additional info|'
        r'anything else', key):
      value = cover_letter
    if value is None:
      value = next((v for k, v in canned.items() if k in key), None)
    if value is None and f.tag != 'textarea':
      for pattern, v in _profile_fields(profile):
        if re.search(pattern, key):
          value = v
          break

    if value:
      if f.tag == 'select':
        opt = match_option(value, f.options)
        if opt:
          actions.append(FillAction(f.index, 'select', opt, f.label))
          continue
      else:
        actions.append(FillAction(f.index, 'fill', value, f.label))
        continue
    if f.label or f.placeholder:
      questions.append(f)
    elif f.required:
      unresolved.append(f)

  return FillPlan(actions, unresolved, questions)


def resolve_questions(
    plan: FillPlan, answer_fn: Callable[[List[str]],
                                        List[QuestionAnswer]]) -> FillPlan:
  """Second pass: ask the LLM about fields the profile could not answer."""
  if not plan.questions:
    return plan
  answers = answer_fn([f.question for f in plan.questions])
  by_q: Dict[str, QuestionAnswer] = {
      a.question.strip().lower(): a for a in answers
  }
  actions, unresolved = list(plan.actions), list(plan.unresolved)
  for i, f in enumerate(plan.questions):
    a = by_q.get(f.question.strip().lower())
    if a is None and len(answers) == len(plan.questions):
      a = answers[i]
    if a is None or a.needs_human or not a.answer.strip():
      if f.required:
        unresolved.append(f)
      continue
    if f.tag == 'select':
      opt = match_option(a.answer, f.options)
      if opt:
        actions.append(FillAction(f.index, 'select', opt, f.label))
      elif f.required:
        unresolved.append(f)
    else:
      actions.append(FillAction(f.index, 'fill', a.answer, f.label))
  return FillPlan(actions, unresolved, [])
