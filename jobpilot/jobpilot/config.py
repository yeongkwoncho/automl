"""Config loading."""
import os
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
import yaml

from jobpilot.llm import DEFAULT_MODEL
from jobpilot.models import CandidateProfile, Preferences


class LLMConfig(BaseModel):
  model: str = DEFAULT_MODEL
  match_effort: str = 'medium'
  tailor_effort: str = 'high'
  max_workers: int = 4


class ApplyConfig(BaseModel):
  # dry_run: only write application packets (default, nothing is sent).
  # review:  fill forms in a visible browser, you confirm each submit.
  # auto:    submit unattended when everything is filled and no CAPTCHA.
  mode: Literal['dry_run', 'review', 'auto'] = 'dry_run'
  min_score: int = 75
  max_per_day: int = 10
  company_cooldown_days: int = 30
  delay_seconds: float = 45
  headless: bool = False
  chromium_path: Optional[str] = None


class Config(BaseModel):
  profile: str = 'profile.yaml'
  database: str = 'jobpilot.db'
  output_dir: str = 'applications'
  preferences: Preferences = Field(default_factory=Preferences)
  llm: LLMConfig = Field(default_factory=LLMConfig)
  apply: ApplyConfig = Field(default_factory=ApplyConfig)
  sources: Dict[str, Any] = Field(default_factory=dict)
  base_dir: str = '.'

  def path(self, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(self.base_dir, p)

  def load_profile(self) -> CandidateProfile:
    with open(self.path(self.profile)) as f:
      profile = CandidateProfile(**(yaml.safe_load(f) or {}))
    if profile.resume_path:
      profile.resume_path = self.path(profile.resume_path)
      if not profile.resume_text and profile.resume_path.endswith(
          ('.txt', '.md')):
        with open(profile.resume_path) as f:
          profile.resume_text = f.read()
    return profile


def load_config(path: str) -> Config:
  with open(path) as f:
    data = yaml.safe_load(f) or {}
  cfg = Config(**data)
  cfg.base_dir = os.path.dirname(os.path.abspath(path))
  return cfg


def missing_profile_fields(profile: CandidateProfile) -> List[str]:
  missing = [
      k for k in ('first_name', 'last_name', 'email')
      if not getattr(profile, k)
  ]
  if not profile.resume_path:
    missing.append('resume_path')
  return missing
