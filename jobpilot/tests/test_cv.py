import os

import pytest

from conftest import FakeLLM, make_job
from jobpilot import cvgen
from jobpilot.agent import ground_cv
from jobpilot.fit import quick_fit
from jobpilot.models import (CVExperience, Education, Experience, Preferences,
                             TailoredCV)
from test_pipeline import FakeApplier, make_pipeline

CHROMIUM = os.environ.get('JOBPILOT_CHROMIUM', '/opt/pw-browsers/chromium')


def draft_cv():
  return TailoredCV(headline='ML Engineer for metrology',
                    summary='Builds vision models.',
                    skills=['PyTorch', 'Kubernetes', 'Rust'],
                    experience=[
                        CVExperience(company='acme',
                                     title='ML  Engineer',
                                     start='1999',
                                     end='2000',
                                     bullets=['Shipped <b>detection</b>']),
                        CVExperience(company='Google',
                                     title='Staff Engineer',
                                     start='2020',
                                     end='2024',
                                     bullets=['Invented role']),
                    ],
                    education=[Education(school='MIT', degree='PhD')],
                    languages=['Klingon'])


class CVLLM(FakeLLM):

  def structured(self, *, system, content, schema, effort='high'):
    if schema is TailoredCV:
      self.calls.append(('TailoredCV', content))
      return draft_cv()
    return super().structured(system=system,
                              content=content,
                              schema=schema,
                              effort=effort)


def full_profile(profile):
  profile.experience = [
      Experience(company='Acme',
                 title='ML Engineer',
                 start='2021-03',
                 end='present',
                 highlights=['Deployed on Kubernetes'])
  ]
  profile.education = [Education(school='Example University', degree='M.S.')]
  profile.languages = ['Korean', 'English']
  return profile


def test_ground_cv_removes_invented_facts(profile):
  cv = ground_cv(draft_cv(), full_profile(profile))
  assert [(r.company, r.title, r.start, r.end) for r in cv.experience] == [
      ('Acme', 'ML Engineer', '2021-03', 'present')
  ]  # fake role dropped
  assert cv.skills == ['PyTorch', 'Kubernetes']  # Rust is not in the master
  assert cv.education[0].school == 'Example University'
  assert cv.languages == ['Korean', 'English']


def test_quick_fit_ranks_relevant_jobs_higher(profile):
  profile.skills = ['PyTorch', 'Computer Vision', 'C++', 'Kubernetes']
  prefs = Preferences(titles=['Machine Learning Engineer'])
  good = make_job(description='PyTorch, computer vision and C++ on Kubernetes')
  partial = make_job(title='Data Engineer', description='PyTorch pipelines')
  none = make_job(title='Account Executive', description='Sell C suites')
  scores = [quick_fit(profile, prefs, j) for j in (good, partial, none)]
  assert scores[0] == 100 and scores[0] > scores[1] > scores[2] == 0


def test_write_cv_escapes_and_makes_pdf(tmp_path, profile):
  cv = ground_cv(draft_cv(), full_profile(profile))
  chrome = CHROMIUM if os.path.exists(CHROMIUM) else None
  paths = cvgen.write_cv(str(tmp_path / 'cv'), cv, profile, chrome)
  html = open(paths['html']).read()
  assert 'Shipped &lt;b&gt;detection&lt;/b&gt;' in html and 'Jane Doe' in html
  md = open(paths['md']).read()
  assert '**ML Engineer, Acme** (2021-03 – present)' in md
  if chrome:
    pytest.importorskip('playwright.sync_api')
    assert open(paths['pdf'], 'rb').read(5) == b'%PDF-'
    assert os.path.basename(paths['pdf']) == 'Jane_Doe_CV.pdf'


def test_cv_command_and_upload(tmp_path, profile, prefs, monkeypatch):
  monkeypatch.setattr(
      cvgen,
      'html_to_pdf',
      lambda h, p, e=None: bool(open(p, 'wb').write(b'%PDF-fake')))
  jobs = [
      make_job(),
      make_job(external_id='b/2',
               company='B',
               title='Staff Machine Learning Engineer')
  ]
  llm = CVLLM()
  applier = FakeApplier()
  seen = []
  applier.apply = lambda job, packet, prof, d, fn: (
      seen.append(prof.resume_path),
      FakeApplier.apply(applier, job, packet, prof, d, fn))[1]
  pipe, store = make_pipeline(tmp_path,
                              full_profile(profile),
                              prefs,
                              jobs,
                              llm=llm,
                              mode='auto',
                              tailor_cv=True,
                              applier=applier)
  pipe.search()
  pipe.match()
  assert pipe.make_cvs(limit=1) == {'created': 1, 'errors': 0}
  assert pipe.make_cvs(limit=5) == {'created': 1, 'errors': 0}
  assert pipe.make_cvs() == {'created': 0, 'errors': 0}  # none left
  d, pdf = store.cv('greenhouse:acme/1')
  assert os.path.exists(os.path.join(d, 'cv.html')) and pdf.endswith('_CV.pdf')
  # Applying uploads the tailored PDF, not the master resume, and does not
  # regenerate CVs that exist.
  n = sum(1 for c in llm.calls if c[0] == 'TailoredCV')
  pipe.apply()
  assert sum(1 for c in llm.calls if c[0] == 'TailoredCV') == n
  assert seen and all(p.endswith('_CV.pdf') for p in seen)
  assert profile.resume_path.endswith('resume.pdf')  # master untouched
