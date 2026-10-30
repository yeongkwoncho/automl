"""Application packets and appliers."""
import datetime as dt
import json
import os
from typing import Optional

from jobpilot.models import Job, MatchAssessment, TailoredApplication
from jobpilot.textutil import slugify


def write_packet(output_dir: str, job: Job, match: MatchAssessment,
                 packet: TailoredApplication) -> str:
  """Saves everything needed to apply by hand; returns the directory."""
  name = f'{dt.date.today():%Y%m%d}-{slugify(job.company)}-{slugify(job.title)}'
  path = os.path.join(output_dir, name[:120])
  os.makedirs(path, exist_ok=True)
  with open(os.path.join(path, 'cover_letter.txt'), 'w') as f:
    f.write(packet.cover_letter)
  lines = [
      f'# {job.title} @ {job.company}', '',
      f'- Apply: {job.apply_url or job.url}', f'- Location: {job.location}',
      f'- Source: {job.source}',
      f'- Fit score: {match.score} ({match.verdict})', '', f'> {match.summary}',
      '', '## Strengths'
  ]
  lines += [f'- {s}' for s in match.matched_strengths]
  lines += ['', '## Gaps'] + [f'- {g}' for g in match.gaps]
  lines += ['', '## Resume highlights to emphasize']
  lines += [f'- {h}' for h in packet.resume_highlights]
  lines += [
      '', '## Cover letter', '', packet.cover_letter, '',
      '## Application questions'
  ]
  for qa in packet.answers:
    flag = ' **(NEEDS YOUR INPUT)**' if qa.needs_human else ''
    lines += ['', f'**{qa.question}**{flag}', '', qa.answer or '_(blank)_']
  with open(os.path.join(path, 'APPLICATION.md'), 'w') as f:
    f.write('\n'.join(lines) + '\n')
  with open(os.path.join(path, 'data.json'), 'w') as f:
    json.dump(
        {
            'job': json.loads(job.model_dump_json()),
            'match': match.model_dump(),
            'packet': packet.model_dump()
        },
        f,
        indent=1,
        ensure_ascii=False)
  return path


def load_packet(path: str) -> Optional[TailoredApplication]:
  """Loads a packet written by write_packet, or None if it is missing.

  Edits you make to cover_letter.txt are picked up here.
  """
  try:
    with open(os.path.join(path, 'data.json')) as f:
      packet = TailoredApplication(**json.load(f)['packet'])
    with open(os.path.join(path, 'cover_letter.txt')) as f:
      packet.cover_letter = f.read()
    return packet
  except (OSError, KeyError, ValueError):
    return None
