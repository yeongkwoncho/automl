import datetime as dt
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobpilot.models import (
    CandidateProfile,
    Experience,
    Job,  # pylint: disable=g-import-not-at-top
    MatchAssessment,
    Preferences,
    QuestionAnswer,
    QuestionAnswers,
    TailoredApplication)


class FakeLLM:
  """Returns canned structured outputs keyed by schema."""

  def __init__(self, score=90):
    self.score = score
    self.calls = []

  def structured(self, *, system, content, schema, effort='high'):
    self.calls.append((schema.__name__, content))
    if schema is MatchAssessment:
      return MatchAssessment(score=self.score,
                             verdict='strong',
                             summary='fit',
                             matched_strengths=['PyTorch'],
                             gaps=[],
                             dealbreakers=[],
                             seniority_fit='match')
    if schema is TailoredApplication:
      return TailoredApplication(cover_letter='Dear team, ...',
                                 resume_highlights=['Led X'],
                                 answers=[])
    if schema is QuestionAnswers:
      qs = [l[2:] for l in content.splitlines() if l.startswith('- ')]
      return QuestionAnswers(answers=[
          QuestionAnswer(
              question=q, answer='5', needs_human='visa' in q.lower())
          for q in qs
      ])
    raise AssertionError(schema)


@pytest.fixture
def profile(tmp_path):
  resume = tmp_path / 'resume.pdf'
  resume.write_bytes(b'%PDF-1.4 fake')
  return CandidateProfile(
      first_name='Jane',
      last_name='Doe',
      email='jane@example.com',
      phone='+82 10 1234 5678',
      location='Seoul',
      linkedin='https://li/jane',
      github='https://gh/jane',
      skills=['PyTorch'],
      experience=[Experience(company='Acme', title='ML Engineer')],
      resume_path=str(resume),
      answers={'notice period': '1 month'})


@pytest.fixture
def prefs():
  return Preferences(titles=['Machine Learning Engineer'],
                     keywords=['ML Engineer'],
                     exclude_keywords=['intern'],
                     locations=['Seoul', 'Remote'],
                     exclude_companies=['BadCo'])


def make_job(**kw):
  base = dict(source='greenhouse',
              external_id='acme/1',
              title='Senior Machine Learning Engineer',
              company='Acme',
              url='https://boards.greenhouse.io/acme/jobs/1',
              location='Seoul, KR',
              description='PyTorch',
              posted_at=dt.datetime.now(dt.timezone.utc),
              ats='greenhouse')
  base.update(kw)
  return Job(**base)
