"""Renders a tailored CV to Markdown, HTML and (via Chromium) PDF."""
import html
import logging
import os
from typing import Optional

from jobpilot.models import CandidateProfile, TailoredCV

logger = logging.getLogger(__name__)

_CSS = """
@page { size: A4; margin: 16mm 16mm; }
body { font: 10.5pt/1.4 -apple-system, "Helvetica Neue", "Apple SD Gothic Neo",
  "Noto Sans KR", Arial, sans-serif; color: #111; max-width: 780px;
  margin: 24px auto; padding: 0 16px; }
h1 { font-size: 20pt; margin: 0; }
.headline { font-size: 11.5pt; color: #333; margin: 2px 0 6px; }
.contact { color: #444; font-size: 9.5pt; }
h2 { font-size: 10.5pt; text-transform: uppercase; letter-spacing: .06em;
  border-bottom: 1px solid #999; padding-bottom: 2px; margin: 14px 0 6px; }
.role { display: flex; justify-content: space-between; gap: 12px;
  font-weight: 600; margin-top: 8px; }
.role span { font-weight: 400; color: #444; white-space: nowrap; }
ul { margin: 3px 0 0; padding-left: 18px; } li { margin: 1px 0; }
p { margin: 4px 0; }
@media print { body { margin: 0; padding: 0; max-width: none; } }
"""


def _contact(p: CandidateProfile) -> list:
  return [
      x for x in (p.email, p.phone, p.location, p.linkedin, p.github, p.website)
      if x
  ]


def to_markdown(cv: TailoredCV, p: CandidateProfile) -> str:
  L = [
      f'# {p.full_name}', '', f'**{cv.headline}**', '', ' · '.join(_contact(p)),
      '', '## Summary', '', cv.summary, '', '## Skills', '',
      ', '.join(cv.skills), '', '## Experience'
  ]
  for r in cv.experience:
    L += ['', f'**{r.title}, {r.company}** ({r.start} – {r.end})', '']
    L += [f'- {b}' for b in r.bullets]
  if cv.education:
    L += ['', '## Education', '']
    L += [
        f'- {" ".join(x for x in (e.degree, e.field) if x)}, {e.school}'
        f'{" (" + e.end + ")" if e.end else ""}' for e in cv.education
    ]
  if cv.languages:
    L += ['', '## Languages', '', ', '.join(cv.languages)]
  return '\n'.join(L) + '\n'


def to_html(cv: TailoredCV, p: CandidateProfile) -> str:
  e = html.escape
  parts = [
      f'<!doctype html><html><head><meta charset="utf-8">'
      f'<title>{e(p.full_name)} CV</title><style>{_CSS}</style></head>'
      f'<body><h1>{e(p.full_name)}</h1>'
      f'<div class="headline">{e(cv.headline)}</div>'
      f'<div class="contact">{e(" · ".join(_contact(p)))}</div>'
      f'<h2>Summary</h2><p>{e(cv.summary)}</p>'
      f'<h2>Skills</h2><p>{e(", ".join(cv.skills))}</p>'
      '<h2>Experience</h2>'
  ]
  for r in cv.experience:
    parts.append(f'<div class="role">{e(r.title)}, {e(r.company)}'
                 f'<span>{e(r.start)} – {e(r.end)}</span></div><ul>' +
                 ''.join(f'<li>{e(b)}</li>' for b in r.bullets) + '</ul>')
  if cv.education:
    parts.append('<h2>Education</h2><ul>' + ''.join(
        f'<li>{e(" ".join(x for x in (ed.degree, ed.field) if x))}, '
        f'{e(ed.school)}{" (" + e(ed.end) + ")" if ed.end else ""}</li>'
        for ed in cv.education) + '</ul>')
  if cv.languages:
    parts.append(f'<h2>Languages</h2><p>{e(", ".join(cv.languages))}</p>')
  return ''.join(parts) + '</body></html>'


def html_to_pdf(html_path: str,
                pdf_path: str,
                executable_path: Optional[str] = None) -> bool:
  """Prints the HTML to PDF with Chromium; False if it is unavailable."""
  try:
    from playwright.sync_api import sync_playwright  # pylint: disable=g-import-not-at-top
    with sync_playwright() as pw:
      kwargs = {'executable_path': executable_path} if executable_path else {}
      browser = pw.chromium.launch(**kwargs)
      try:
        page = browser.new_page()
        page.goto('file://' + os.path.abspath(html_path))
        page.pdf(path=pdf_path, format='A4', print_background=True)
      finally:
        browser.close()
    return True
  except Exception as e:  # pylint: disable=broad-except
    logger.warning(
        'PDF not created (%s); cv.html can be printed to PDF '
        'from a browser',
        str(e).splitlines()[0])
    return False


def write_cv(directory: str,
             cv: TailoredCV,
             profile: CandidateProfile,
             executable_path: Optional[str] = None) -> dict:
  """Writes cv.md, cv.html, cv.json and, if possible, a PDF."""
  os.makedirs(directory, exist_ok=True)
  paths = {k: os.path.join(directory, f'cv.{k}') for k in ('md', 'html', 'json')}
  with open(paths['md'], 'w') as f:
    f.write(to_markdown(cv, profile))
  with open(paths['html'], 'w') as f:
    f.write(to_html(cv, profile))
  with open(paths['json'], 'w') as f:
    f.write(cv.model_dump_json(indent=1))
  name = '_'.join(x for x in (profile.first_name, profile.last_name) if x)
  pdf = os.path.join(directory, f'{name or "CV"}_CV.pdf')
  if html_to_pdf(paths['html'], pdf, executable_path):
    paths['pdf'] = pdf
  return paths
