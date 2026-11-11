"""LLM-backed steps: resume import, job matching and application tailoring."""
import json
from typing import List

from jobpilot.llm import StructuredLLM, document_block
from jobpilot.models import (TailoredCV, CandidateProfile, ExtractedProfile,
                             Job, MatchAssessment, Preferences, QuestionAnswer,
                             QuestionAnswers, TailoredApplication)

MAX_DESCRIPTION_CHARS = 30000

_GROUNDING = """\
Honesty rules (these override everything else):
- Use only facts stated in the candidate profile. Never invent employers, \
titles, dates, degrees, skills, metrics, certifications or visa status.
- You may reword, reorder and emphasize real experience to fit the job.
- If a question cannot be answered truthfully from the profile, set \
needs_human=true and leave answer empty rather than guessing.
- Never answer voluntary demographic / EEO questions; those are handled \
separately."""


def _profile_context(profile: CandidateProfile, prefs: Preferences) -> str:
  data = profile.model_dump(exclude={'resume_path', 'eeo'})
  return (
      f'<candidate_profile>\n{json.dumps(data, indent=1, ensure_ascii=False)}'
      f'\n</candidate_profile>\n\n<job_preferences>\n'
      f'{prefs.model_dump_json(indent=1)}\n</job_preferences>')


def _job_context(job: Job) -> str:
  salary = ''
  if job.salary_min or job.salary_max:
    salary = f'{job.salary_min or "?"}-{job.salary_max or "?"} {job.salary_currency or ""}'
  return (f'<job>\nTitle: {job.title}\nCompany: {job.company}\n'
          f'Location: {job.location}{" (remote)" if job.remote else ""}\n'
          f'Employment type: {job.employment_type or "unknown"}\n'
          f'Salary: {salary or "not listed"}\nURL: {job.url}\n\n'
          f'{job.description[:MAX_DESCRIPTION_CHARS]}\n</job>')


class ProfileImporter:
  """Turns a resume file into a structured CandidateProfile."""

  def __init__(self, llm: StructuredLLM):
    self.llm = llm

  def run(self, resume_path: str) -> CandidateProfile:
    extracted = self.llm.structured(
        system='You extract structured candidate profiles from resumes. '
        'Copy facts exactly; leave a field empty if the resume does not '
        'state it. Put the full resume text, lightly cleaned, in resume_text.',
        content=[
            document_block(resume_path), {
                'type': 'text',
                'text': 'Extract the candidate profile.'
            }
        ],
        schema=ExtractedProfile,
        effort='medium',
    )
    return CandidateProfile(**extracted.model_dump(), resume_path=resume_path)


class Matcher:
  """Scores how well a job fits the candidate."""

  def __init__(self,
               llm: StructuredLLM,
               profile: CandidateProfile,
               prefs: Preferences,
               effort: str = 'medium'):
    self.llm = llm
    self.effort = effort
    self.system = (
        'You are a senior technical recruiter screening jobs on behalf of '
        'one candidate. Judge whether the candidate would be a credible '
        'applicant and whether the job fits their stated preferences.\n\n'
        'Scoring guide: 85-100 strong fit, meets nearly all hard '
        'requirements; 70-84 good fit, minor gaps; 40-69 weak, notable '
        'gaps; 0-39 reject. Any dealbreaker (missing required work '
        'authorization, hard-required skill or years the candidate lacks, '
        'excluded location/seniority from preferences) caps the score at '
        '39. Be calibrated: applying to poor fits wastes the candidate\'s '
        'reputation.\n\n' + _profile_context(profile, prefs))

  def assess(self, job: Job) -> MatchAssessment:
    result = self.llm.structured(
        system=self.system,
        content=_job_context(job) + '\n\nAssess this job for the candidate.',
        schema=MatchAssessment,
        effort=self.effort,
    )
    result.score = max(0, min(100, result.score))
    return result


class Tailor:
  """Writes the cover letter and answers screening questions."""

  def __init__(self,
               llm: StructuredLLM,
               profile: CandidateProfile,
               prefs: Preferences,
               effort: str = 'high'):
    self.llm = llm
    self.profile = profile
    self.effort = effort
    self.system = (
        'You prepare job applications for one candidate. Write in the '
        'candidate\'s voice, concise and specific, matching the language of '
        'the job posting.\n\n' + _GROUNDING + '\n\n' +
        _profile_context(profile, prefs))

  def tailor(
      self, job: Job, assessment: MatchAssessment, questions: List[str] = ()
  ) -> TailoredApplication:
    qs = '\n'.join(f'- {q}' for q in questions) or '(none)'
    return self.llm.structured(
        system=self.system,
        content=(f'{_job_context(job)}\n\n<fit_assessment>\n'
                 f'{assessment.model_dump_json(indent=1)}\n</fit_assessment>'
                 f'\n\n<application_questions>\n{qs}\n</application_questions>'
                 '\n\nWrite a cover letter (under 300 words, no placeholders '
                 'like [Company]), 3-6 resume highlights drawn from real '
                 'experience, and an answer for every application question.'),
        schema=TailoredApplication,
        effort=self.effort,
    )

  def cv(self, job: Job, assessment: MatchAssessment) -> TailoredCV:
    """Writes a CV version for this job, then strips anything not in the
    master profile (see `ground_cv`)."""
    cv = self.llm.structured(
        system=self.system,
        content=(f'{_job_context(job)}\n\n<fit_assessment>\n'
                 f'{assessment.model_dump_json(indent=1)}\n</fit_assessment>'
                 '\n\nWrite a version of the candidate\'s CV for this '
                 'position. Keep every employer, title, date, degree and '
                 'school exactly as in the master profile and keep all '
                 'roles (gaps look worse than brevity). Choose and order '
                 'skills by relevance to this job; rewrite the headline, '
                 'summary and bullets to foreground the experience this job '
                 'asks for, using the posting\'s terminology only where it '
                 'truthfully describes what the candidate did. Do not add '
                 'skills, tools, metrics or responsibilities that are not '
                 'in the master profile. 3-5 bullets for relevant roles, '
                 '1-2 for others. One page.'),
        schema=TailoredCV,
        effort=self.effort,
    )
    return ground_cv(cv, self.profile)

  def answer(self, job: Job, questions: List[str]) -> List[QuestionAnswer]:
    """Answers extra form questions discovered while filling a form."""
    if not questions:
      return []
    canned = {k.lower(): v for k, v in self.profile.answers.items()}
    result = self.llm.structured(
        system=self.system,
        content=(
            f'{_job_context(job)}\n\nAnswer these application form '
            'fields. For fields with listed options, answer with one '
            'option exactly as written. Prefer the candidate\'s canned '
            f'answers where relevant: {json.dumps(canned, ensure_ascii=False)}'
            '\n\n' + '\n'.join(f'- {q}' for q in questions)),
        schema=QuestionAnswers,
        effort=self.effort,
    )
    return result.answers


def ground_cv(cv: TailoredCV, profile: CandidateProfile) -> TailoredCV:
  """Enforces in code what the prompt asks for: no invented facts.

  - Roles must match a master role (company and title); dates are reset to
    the master's. Unknown roles are dropped.
  - Skills must be master skills or appear in the master resume text.
  - Education and languages are taken from the master unchanged.
  """
  norm = lambda s: ' '.join((s or '').lower().split())
  master = {(norm(e.company), norm(e.title)): e for e in profile.experience}
  roles = []
  for r in cv.experience:
    m = master.get((norm(r.company), norm(r.title)))
    if m is None:
      continue
    roles.append(
        r.model_copy(update={
            'company': m.company,
            'title': m.title,
            'start': m.start,
            'end': m.end
        }))
  known = {norm(s) for s in profile.skills}
  text = norm(profile.resume_text + ' ' + profile.summary + ' ' +
              ' '.join(h for e in profile.experience for h in e.highlights))
  skills = [s for s in cv.skills if norm(s) in known or norm(s) in text]
  return cv.model_copy(
      update={
          'experience': roles,
          'skills': skills,
          'education': list(profile.education),
          'languages': list(profile.languages)
      })
