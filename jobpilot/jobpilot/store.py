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
CREATE TABLE IF NOT EXISTS selections (
  uid TEXT PRIMARY KEY REFERENCES jobs(uid),
  selected_at TEXT NOT NULL
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
    """Stores a job; returns False if it (or a cross-posted copy) exists.

    A cross-posted copy is the same company/title/location from a different
    source. Within one source, distinct ids are distinct openings (companies
    often post several identical titles for different teams).
    """
    exists = self.db.execute(
        'SELECT 1 FROM jobs WHERE uid = ? OR (dedup_key = ? AND uid NOT LIKE ?)',
        (job.uid, job.dedup_key, job.source + ':%')).fetchone()
    if exists:
      return False
    with self.db:
      self.db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)',
                      (job.uid, job.dedup_key, job.company.lower(),
                       job.model_dump_json(), _now(), filter_reason))
    return True

  def all_jobs(self) -> List[Tuple[Job, Optional[str]]]:
    rows = self.db.execute('SELECT data, filter_reason FROM jobs').fetchall()
    return [(Job.model_validate_json(d), r) for d, r in rows]

  def set_filter(self, uid: str, reason: Optional[str]):
    with self.db:
      self.db.execute('UPDATE jobs SET filter_reason = ? WHERE uid = ?',
                      (reason, uid))

  def filter_reasons(self) -> List[tuple]:
    """Rejected-job counts per reason kind ('location', 'too old', ...)."""
    return self.db.execute(
        "SELECT CASE WHEN instr(filter_reason, ':') > 0 "
        "THEN substr(filter_reason, 1, instr(filter_reason, ':') - 1) "
        'ELSE filter_reason END AS kind, COUNT(*) FROM jobs '
        'WHERE filter_reason IS NOT NULL GROUP BY kind ORDER BY 2 DESC'
    ).fetchall()

  def get_job(self, uid: str) -> Optional[Job]:
    row = self.db.execute('SELECT data FROM jobs WHERE uid = ?',
                          (uid,)).fetchone()
    return Job.model_validate_json(row[0]) if row else None

  def unscored_jobs(self, only: Optional[List[str]] = None) -> List[Job]:
    """Jobs to score. With `only` (a hand-picked list), the prefilter is
    skipped: the user chose them explicitly."""
    q = ('SELECT j.data FROM jobs j LEFT JOIN matches m ON j.uid = m.uid '
         'WHERE m.uid IS NULL')
    if only is None:
      rows = self.db.execute(q + ' AND j.filter_reason IS NULL').fetchall()
    else:
      rows = [
          r for uid in only
          for r in self.db.execute(q + ' AND j.uid = ?', (uid,)).fetchall()
      ]
    return [Job.model_validate_json(r[0]) for r in rows]

  # Selections (jobs the user picked in the web page).
  def set_selected(self, uids: List[str], selected: bool) -> int:
    with self.db:
      if selected:
        cur = self.db.executemany(
            'INSERT OR IGNORE INTO selections '
            'SELECT uid, ? FROM jobs WHERE uid = ?',
            [(_now(), u) for u in uids])
      else:
        cur = self.db.executemany('DELETE FROM selections WHERE uid = ?',
                                  [(u,) for u in uids])
    return cur.rowcount

  def selected_uids(self) -> List[str]:
    return [
        r[0] for r in self.db.execute(
            'SELECT uid FROM selections ORDER BY selected_at')
    ]

  # Matches.
  def save_match(self, uid: str, m: MatchAssessment):
    with self.db:
      self.db.execute('INSERT OR REPLACE INTO matches VALUES (?, ?, ?, ?)',
                      (uid, m.score, m.model_dump_json(), _now()))

  def candidates(
      self,
      min_score: int,
      include_packets: bool = False,
      only: Optional[List[str]] = None,
  ) -> Iterator[Tuple[Job, MatchAssessment, Optional[str]]]:
    """Scored jobs above threshold not yet applied to, best first.

    With include_packets, dry-run jobs ('packet_ready') are returned too,
    along with their packet directory, so they can be submitted later.
    With `only` (hand-picked uids), the prefilter and min_score are not
    applied: the user chose these jobs explicitly.
    """
    where = ('(a.uid IS NULL OR (? AND a.status = \'packet_ready\'))')
    args: list = [include_packets]
    if only is None:
      where += ' AND m.score >= ? AND j.filter_reason IS NULL'
      args.append(min_score)
    else:
      if not only:
        return
      where += f' AND j.uid IN ({",".join("?" * len(only))})'
      args += list(only)
    rows = self.db.execute(
        'SELECT j.data, m.data, a.packet_dir FROM matches m '
        'JOIN jobs j ON j.uid = m.uid '
        'LEFT JOIN applications a ON a.uid = m.uid '
        f'WHERE {where} ORDER BY m.score DESC', args).fetchall()
    for job, match, packet_dir in rows:
      yield (Job.model_validate_json(job),
             MatchAssessment.model_validate_json(match), packet_dir)

  # Web page.
  def web_jobs(self) -> List[dict]:
    """Every collected job as a light row (no description) for the UI."""
    rows = self.db.execute(
        'SELECT j.uid, j.data, j.filter_reason, j.first_seen, m.score, '
        'm.data, a.status, s.uid FROM jobs j '
        'LEFT JOIN matches m ON m.uid = j.uid '
        'LEFT JOIN applications a ON a.uid = j.uid '
        'LEFT JOIN selections s ON s.uid = j.uid')
    out = []
    for uid, data, reason, first_seen, score, mdata, app, sel in rows:
      j = json.loads(data)
      out.append({
          'uid': uid,
          'source': j['source'],
          'title': j['title'],
          'company': j['company'],
          'location': j.get('location') or '',
          'remote': bool(j.get('remote')),
          'url': j['url'],
          'apply_url': j.get('apply_url') or j['url'],
          'posted_at': (j.get('posted_at') or '')[:10],
          'first_seen': first_seen[:10],
          'employment_type': j.get('employment_type') or '',
          'tags': j.get('tags') or [],
          'filter_reason': reason,
          'score': score,
          'verdict': json.loads(mdata)['verdict'] if mdata else None,
          'application': app,
          'selected': sel is not None,
      })
    return out

  def web_job_detail(self, uid: str) -> Optional[dict]:
    row = self.db.execute(
        'SELECT j.data, m.data FROM jobs j LEFT JOIN matches m '
        'ON m.uid = j.uid WHERE j.uid = ?', (uid,)).fetchone()
    if not row:
      return None
    j = json.loads(row[0])
    return {
        'uid': uid,
        'description': j.get('description', ''),
        'salary_min': j.get('salary_min'),
        'salary_max': j.get('salary_max'),
        'salary_currency': j.get('salary_currency'),
        'match': json.loads(row[1]) if row[1] else None
    }

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

  def list_jobs(self,
                status: str = 'all',
                source: Optional[str] = None,
                company: Optional[str] = None,
                limit: int = 50) -> List[dict]:
    """Collected jobs, newest first, with prefilter result and score."""
    q = ('SELECT j.uid, j.data, j.filter_reason, m.score, a.status '
         'FROM jobs j LEFT JOIN matches m ON m.uid = j.uid '
         'LEFT JOIN applications a ON a.uid = j.uid WHERE 1=1')
    args = []
    if status == 'passed':
      q += ' AND j.filter_reason IS NULL'
    elif status == 'rejected':
      q += ' AND j.filter_reason IS NOT NULL'
    if source:
      q += ' AND j.uid LIKE ?'
      args.append(source + ':%')
    if company:
      q += ' AND j.company LIKE ?'
      args.append(f'%{company.lower()}%')
    q += ' ORDER BY j.first_seen DESC, j.rowid DESC LIMIT ?'
    args.append(limit)
    out = []
    for uid, data, reason, score, app in self.db.execute(q, args):
      job = json.loads(data)
      out.append(
          dict(uid=uid,
               title=job['title'],
               company=job['company'],
               location=job.get('location', ''),
               url=job['url'],
               filter_reason=reason,
               score=score,
               application=app))
    return out

  def prefix_counts(self, prefix: str) -> Tuple[int, int, List[str]]:
    """(collected, passed prefilter, company names) for uids with prefix."""
    like = prefix.replace('%', '').replace('_', r'\_') + '%'
    n, passed = self.db.execute(
        "SELECT COUNT(*), SUM(filter_reason IS NULL) FROM jobs "
        "WHERE uid LIKE ? ESCAPE '\\'", (like,)).fetchone()
    names = [
        r[0] for r in self.db.execute(
            "SELECT json_extract(data, '$.company'), COUNT(*) AS c FROM jobs "
            "WHERE uid LIKE ? ESCAPE '\\' GROUP BY 1 ORDER BY c DESC LIMIT 3", (
                like,))
    ]
    return n or 0, passed or 0, names

  def source_counts(self) -> List[tuple]:
    return self.db.execute(
        "SELECT substr(uid, 1, instr(uid, ':') - 1) AS src, COUNT(*), "
        'SUM(filter_reason IS NULL) FROM jobs GROUP BY src ORDER BY src'
    ).fetchall()

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
