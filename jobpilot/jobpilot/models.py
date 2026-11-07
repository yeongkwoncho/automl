"""Core data models shared across JobPilot."""
import datetime as dt
import hashlib
import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Job(BaseModel):
  """A normalized job posting from any source."""
  source: str
  external_id: str
  title: str
  company: str
  url: str
  location: str = ''
  remote: Optional[bool] = None
  description: str = ''
  apply_url: Optional[str] = None
  posted_at: Optional[dt.datetime] = None
  employment_type: Optional[str] = None
  salary_min: Optional[float] = None
  salary_max: Optional[float] = None
  salary_currency: Optional[str] = None
  tags: List[str] = Field(default_factory=list)
  # Which applicant tracking system hosts the application form, if known.
  # Used to route to the right applier ('greenhouse', 'lever', 'ashby').
  ats: Optional[str] = None

  @property
  def uid(self) -> str:
    return f'{self.source}:{self.external_id}'

  @property
  def dedup_key(self) -> str:
    """Key that identifies the same posting listed on several boards."""
    norm = lambda s: re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()
    raw = '|'.join([norm(self.company), norm(self.title), norm(self.location)])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class Experience(BaseModel):
  company: str
  title: str
  start: str = ''
  end: str = ''
  highlights: List[str] = Field(default_factory=list)


class Education(BaseModel):
  school: str
  degree: str = ''
  field: str = ''
  end: str = ''


class CandidateProfile(BaseModel):
  """Everything JobPilot is allowed to say about the candidate.

  The LLM is instructed to only use facts from this profile, so keep it
  accurate. `answers` holds canned answers to common screening questions
  (salary expectations, notice period, ...). `eeo` holds voluntary
  self-identification answers; leave empty to always decline.
  """
  first_name: str
  last_name: str
  email: str
  phone: str = ''
  location: str = ''
  linkedin: str = ''
  github: str = ''
  website: str = ''
  headline: str = ''
  summary: str = ''
  years_experience: float = 0
  skills: List[str] = Field(default_factory=list)
  languages: List[str] = Field(default_factory=list)
  experience: List[Experience] = Field(default_factory=list)
  education: List[Education] = Field(default_factory=list)
  work_authorization: Dict[str, str] = Field(default_factory=dict)
  resume_path: Optional[str] = None
  resume_text: str = ''
  answers: Dict[str, str] = Field(default_factory=dict)
  eeo: Dict[str, str] = Field(default_factory=dict)

  @property
  def full_name(self) -> str:
    return f'{self.first_name} {self.last_name}'.strip()


class ExtractedProfile(BaseModel):
  """Resume fields the LLM extracts (structured outputs need fixed keys)."""
  first_name: str
  last_name: str
  email: str
  phone: str
  location: str
  linkedin: str
  github: str
  website: str
  headline: str
  summary: str
  years_experience: float
  skills: List[str]
  languages: List[str]
  experience: List[Experience]
  education: List[Education]
  resume_text: str


class Preferences(BaseModel):
  """What the candidate is looking for."""
  titles: List[str] = Field(default_factory=list)
  keywords: List[str] = Field(default_factory=list)
  exclude_keywords: List[str] = Field(default_factory=list)
  locations: List[str] = Field(default_factory=list)
  remote_ok: bool = True
  onsite_ok: bool = True
  seniority: List[str] = Field(default_factory=list)
  min_salary: Optional[float] = None
  salary_currency: Optional[str] = None
  exclude_companies: List[str] = Field(default_factory=list)
  max_job_age_days: int = 30
  notes: str = ''


class MatchAssessment(BaseModel):
  """LLM verdict on how well a job fits the candidate."""
  score: int = Field(description='0-100 overall fit score.')
  verdict: Literal['strong', 'good', 'weak', 'reject']
  summary: str
  matched_strengths: List[str]
  gaps: List[str]
  dealbreakers: List[str]
  seniority_fit: Literal['under', 'match', 'over', 'unknown']


class QuestionAnswer(BaseModel):
  question: str
  answer: str
  needs_human: bool = Field(
      description='True when the profile does not contain the facts needed '
      'to answer truthfully.')


class QuestionAnswers(BaseModel):
  answers: List[QuestionAnswer]


class TailoredApplication(BaseModel):
  """Application materials the LLM writes for one job."""
  cover_letter: str
  resume_highlights: List[str] = Field(
      description='Existing resume bullets, reordered/reworded for this job.')
  answers: List[QuestionAnswer]


ApplicationStatus = Literal['packet_ready', 'submitted', 'needs_manual',
                            'skipped', 'failed']


class ApplyOutcome(BaseModel):
  status: ApplicationStatus
  detail: str = ''
  screenshot: Optional[str] = None
