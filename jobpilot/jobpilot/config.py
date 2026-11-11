"""Config loading."""
import copy
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
  # Write a position-specific CV for each application and upload it
  # instead of the master resume.
  tailor_cv: bool = True


class Config(BaseModel):
  profile: str = 'profile.yaml'
  database: str = 'jobpilot.db'
  output_dir: str = 'applications'
  preferences: Preferences = Field(default_factory=Preferences)
  llm: LLMConfig = Field(default_factory=LLMConfig)
  apply: ApplyConfig = Field(default_factory=ApplyConfig)
  sources: Dict[str, Any] = Field(default_factory=dict)
  # Merge the built-in company list (jobpilot/catalog.yaml) into sources.
  catalog: bool = True
  catalog_exclude: List[str] = Field(default_factory=list)
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


CATALOG = os.path.join(os.path.dirname(__file__), 'catalog.yaml')
_BOARD_KEYS = ('greenhouse', 'lever', 'ashby', 'recruitee')
_SITE_KEYS = ('workday', 'eightfold')


def _site_key(entry) -> str:
  url = entry if isinstance(entry, str) else entry.get('url', '')
  return url.lower().split('://')[-1].rstrip('/')


def _site_names(entry) -> List[str]:
  if isinstance(entry, str):
    return [_site_key(entry)]
  return [entry.get('company', ''), _site_key(entry)]


def merge_catalog(
    user: Dict[str, Any], catalog: Dict[str, Any],
    exclude: List[str] = ()) -> Dict[str, Any]:
  """User sources plus every catalog company they do not already list.

  The user's entries and settings win; catalog entries named in
  `exclude` (board slug, company name or URL, any case) are skipped.
  """
  skip = {e.strip().lower() for e in exclude}
  out = copy.deepcopy(user)
  for key in _BOARD_KEYS:
    boards = (catalog.get(key) or {}).get('boards') or []
    sec = out.setdefault(key, {}) or {}
    out[key] = sec
    have = {b.lower() for b in sec.get('boards') or []}
    sec['boards'] = list(sec.get('boards') or []) + [
        b for b in boards if b.lower() not in have and b.lower() not in skip
    ]
  for key in _SITE_KEYS:
    cat = catalog.get(key) or {}
    sec = out.setdefault(key, {}) or {}
    out[key] = sec
    for k, v in cat.items():
      if k != 'sites':
        sec.setdefault(k, v)
    have = {_site_key(e) for e in sec.get('sites') or []}
    sec['sites'] = list(sec.get('sites') or []) + [
        e for e in cat.get('sites') or []
        if _site_key(e) not in have and not any(n.lower() in skip
                                                for n in _site_names(e))
    ]
  if 'apple' in catalog and 'apple' not in out and 'apple' not in skip:
    out['apple'] = copy.deepcopy(catalog['apple'])
  return out


def load_config(path: str) -> Config:
  with open(path) as f:
    data = yaml.safe_load(f) or {}
  cfg = Config(**data)
  cfg.base_dir = os.path.dirname(os.path.abspath(path))
  if cfg.catalog:
    with open(CATALOG) as f:
      cfg.sources = merge_catalog(cfg.sources,
                                  yaml.safe_load(f) or {}, cfg.catalog_exclude)
  return cfg


def missing_profile_fields(profile: CandidateProfile) -> List[str]:
  missing = [
      k for k in ('first_name', 'last_name', 'email')
      if not getattr(profile, k)
  ]
  if not profile.resume_path:
    missing.append('resume_path')
  return missing
