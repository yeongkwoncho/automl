"""SQLite persistence: seen jobs, match scores and application history."""
import datetime as dt
import json
import sqlite3
from typing import Iterator, List, Optional, Tuple

from jobpilot.models import ApplyOutcome, Job, MatchAssessment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  uid TEXT PRIMARY KEY,
  dedup_key TEXT NOT NULL,
  company TEXT NOT NULL,
  data TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  filter_reason TEXT
);
CREATE INDEX IF NOT EXISTS jobs_dedup ON jobs(dedup_key);
CREATE TABLE IF NOT EXISTS matches (
  uid TEXT PRIMARY KEY REFERENCES jobs(uid),
  score INTEGER NOT NULL,
  data TEXT NOT NULL,
  scored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
  uid TEXT PRIMARY KEY REFERENCES jobs(uid),
  company TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  packet_dir TEXT,
  updated_at TEXT NOT NULL
);
"""


def _now() -> str:
  return dt.datetime.now(dt.timezone.utc).isoformat()


class Store:

  def __init__(self, path: str):
    self.db = sqlite3.connect(path, check_same_thread=False)
    self.db.executescript(_SCHEMA)

  def close(self):
    self.db.close()

  # Jobs.
  def add_job(self, job: Job, filter_reason: Optional[str]) -> bool:
    """Stores a job; returns False if it (or a cross-posted copy) exists."""
    exists = self.db.execute(
        'SELECT 1 FROM jobs WHERE uid = ? OR dedup_key = ?',
        (job.uid, job.dedup_key)).fetchone()
    if exists:
      return False
    with self.db:
      self.db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)',
                      (job.uid, job.dedup_key, job.company.lower(),
                       job.model_dump_json(), _now(), filter_reason))
    return True

  def get_job(self, uid: str) -> Optional[Job]:
    row = self.db.execute('SELECT data FROM jobs WHERE uid = ?',
                          (uid,)).fetchone()
    return Job.model_validate_json(row[0]) if row else None

  def unscored_jobs(self) -> List[Job]:
    rows = self.db.execute(
        'SELECT j.data FROM jobs j LEFT JOIN matches m ON j.uid = m.uid '
        'WHERE j.filter_reason IS NULL AND m.uid IS NULL').fetchall()
    return [Job.model_validate_json(r[0]) for r in rows]

  # Matches.
  def save_match(self, uid: str, m: MatchAssessment):
    with self.db:
      self.db.execute('INSERT OR REPLACE INTO matches VALUES (?, ?, ?, ?)',
                      (uid, m.score, m.model_dump_json(), _now()))

  def candidates(
      self,
      min_score: int,
      include_packets: bool = False
  ) -> Iterator[Tuple[Job, MatchAssessment, Optional[str]]]:
    """Scored jobs above threshold not yet applied to, best first.

    With include_packets, dry-run jobs ('packet_ready') are returned too,
    along with their packet directory, so they can be submitted later.
    """
    rows = self.db.execute(
        'SELECT j.data, m.data, a.packet_dir FROM matches m '
        'JOIN jobs j ON j.uid = m.uid '
        'LEFT JOIN applications a ON a.uid = m.uid '
        'WHERE m.score >= ? AND (a.uid IS NULL OR '
        "(? AND a.status = 'packet_ready')) ORDER BY m.score DESC",
        (min_score, include_packets)).fetchall()
    for job, match, packet_dir in rows:
      yield (Job.model_validate_json(job),
             MatchAssessment.model_validate_json(match), packet_dir)

  # Applications.
  def record_application(self,
                         job: Job,
                         outcome: ApplyOutcome,
                         packet_dir: str = ''):
    with self.db:
      self.db.execute(
          'INSERT OR REPLACE INTO applications VALUES (?, ?, ?, ?, ?, ?)',
          (job.uid, job.company.lower(), outcome.status, outcome.detail,
           packet_dir, _now()))

  def set_status(self, uid: str, status: str, detail: str = '') -> bool:
    with self.db:
      cur = self.db.execute(
          'UPDATE applications SET status = ?, detail = ?, updated_at = ? '
          'WHERE uid = ?', (status, detail, _now(), uid))
    return cur.rowcount > 0

  def submitted_since(self, since: dt.datetime) -> int:
    return self.db.execute(
        "SELECT COUNT(*) FROM applications WHERE status = 'submitted' "
        'AND updated_at >= ?', (since.isoformat(),)).fetchone()[0]

  def applied_to_company_since(self, company: str, since: dt.datetime) -> bool:
    return self.db.execute(
        "SELECT 1 FROM applications WHERE status = 'submitted' "
        'AND company = ? AND updated_at >= ?',
        (company.lower(), since.isoformat())).fetchone() is not None

  def report(self, status: Optional[str] = None) -> List[dict]:
    q = ('SELECT a.uid, a.status, a.detail, a.packet_dir, a.updated_at, '
         'm.score, j.data FROM applications a JOIN jobs j ON j.uid = a.uid '
         'LEFT JOIN matches m ON m.uid = a.uid')
    args = ()
    if status:
      q += ' WHERE a.status = ?'
      args = (status,)
    out = []
    for uid, st, detail, pdir, upd, score, data in self.db.execute(
        q + ' ORDER BY a.updated_at DESC', args):
      job = json.loads(data)
      out.append(
          dict(uid=uid,
               status=st,
               detail=detail,
               packet_dir=pdir,
               updated_at=upd,
               score=score,
               title=job['title'],
               company=job['company'],
               url=job['url']))
    return out

  def stats(self) -> dict:
    q = lambda sql: self.db.execute(sql).fetchone()[0]
    return {
        'jobs_seen':
            q('SELECT COUNT(*) FROM jobs'),
        'passed_prefilter':
            q('SELECT COUNT(*) FROM jobs '
              'WHERE filter_reason IS NULL'),
        'scored':
            q('SELECT COUNT(*) FROM matches'),
        **dict(
            self.db.execute('SELECT status, COUNT(*) FROM applications '
                            'GROUP BY status').fetchall()),
    }
