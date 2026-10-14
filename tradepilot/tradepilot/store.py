"""SQLite persistence for decisions, outcomes, reviews and strategy versions."""
import datetime as dt
import json
import sqlite3
from typing import List, Optional

from tradepilot.models import Decision, Outcome, RuleChange, Strategy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_date ON decisions(date);
CREATE TABLE IF NOT EXISTS outcomes (
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  horizon INTEGER NOT NULL,
  ret_pct REAL NOT NULL,
  label TEXT NOT NULL,
  data TEXT NOT NULL,
  scored_at TEXT NOT NULL,
  PRIMARY KEY (decision_id, horizon)
);
CREATE TABLE IF NOT EXISTS reviews (
  date TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategies (
  version INTEGER PRIMARY KEY,
  status TEXT NOT NULL,          -- active | proposed | retired | rejected
  data TEXT NOT NULL,
  changes TEXT NOT NULL,
  created_at TEXT NOT NULL,
  approved_at TEXT
);
"""


def _now() -> str:
  return dt.datetime.now(dt.timezone.utc).isoformat()


class Store:

  def __init__(self, path: str):
    self.db = sqlite3.connect(path)
    self.db.executescript(_SCHEMA)

  def close(self):
    self.db.close()

  # Decisions.
  def add_decision(self, d: Decision) -> int:
    with self.db:
      cur = self.db.execute(
          'INSERT INTO decisions (date, symbol, action, data, created_at) '
          'VALUES (?, ?, ?, ?, ?)', (d.date.isoformat(), d.symbol, d.action,
                                     d.model_dump_json(exclude={'id'}), _now()))
    return cur.lastrowid

  def _decisions(self, where: str = '', args=()) -> List[Decision]:
    rows = self.db.execute(
        f'SELECT id, data FROM decisions {where} ORDER BY date, id', args)
    return [Decision(id=i, **json.loads(d)) for i, d in rows]

  def decisions_on(self, day: dt.date) -> List[Decision]:
    return self._decisions('WHERE date = ?', (day.isoformat(),))

  def decisions_since(self, day: Optional[dt.date]) -> List[Decision]:
    if day is None:
      return self._decisions()
    return self._decisions('WHERE date >= ?', (day.isoformat(),))

  def unscored(self, horizon: int) -> List[Decision]:
    return self._decisions(
        'WHERE id NOT IN (SELECT decision_id FROM outcomes WHERE horizon = ?)',
        (horizon,))

  # Outcomes.
  def save_outcome(self, o: Outcome):
    with self.db:
      self.db.execute(
          'INSERT OR REPLACE INTO outcomes VALUES (?, ?, ?, ?, ?, ?)',
          (o.decision_id, o.horizon, o.ret_pct, o.label, o.model_dump_json(),
           _now()))

  def outcomes(self, horizon: Optional[int] = None) -> List[Outcome]:
    q, args = 'SELECT data FROM outcomes', ()
    if horizon is not None:
      q, args = q + ' WHERE horizon = ?', (horizon,)
    return [Outcome.model_validate_json(r[0]) for r in self.db.execute(q, args)]

  def outcomes_for(self, decision_id: int) -> List[Outcome]:
    rows = self.db.execute(
        'SELECT data FROM outcomes WHERE decision_id = ? ORDER BY horizon',
        (decision_id,))
    return [Outcome.model_validate_json(r[0]) for r in rows]

  # Reviews.
  def save_review(self, day: dt.date, data: dict):
    with self.db:
      self.db.execute(
          'INSERT OR REPLACE INTO reviews VALUES (?, ?, ?)',
          (day.isoformat(), json.dumps(data, ensure_ascii=False), _now()))

  def review(self, day: dt.date) -> Optional[dict]:
    row = self.db.execute('SELECT data FROM reviews WHERE date = ?',
                          (day.isoformat(),)).fetchone()
    return json.loads(row[0]) if row else None

  def recent_lessons(self, limit: int = 10) -> List[str]:
    rows = self.db.execute(
        'SELECT data FROM reviews ORDER BY date DESC LIMIT ?', (limit,))
    out = []
    for (data,) in rows:
      out += (json.loads(data).get('llm') or {}).get('lessons', [])
    return out

  # Strategies.
  def save_strategy(self,
                    s: Strategy,
                    status: str,
                    changes: List[RuleChange] = ()):
    with self.db:
      self.db.execute(
          'INSERT OR REPLACE INTO strategies VALUES (?, ?, ?, ?, ?, ?)',
          (s.version, status, s.model_dump_json(),
           json.dumps([c.model_dump() for c in changes], ensure_ascii=False),
           _now(), _now() if status == 'active' else None))

  def strategy(self, version: int) -> Optional[Strategy]:
    row = self.db.execute('SELECT data FROM strategies WHERE version = ?',
                          (version,)).fetchone()
    return Strategy.model_validate_json(row[0]) if row else None

  def active_strategy(self) -> Optional[Strategy]:
    row = self.db.execute("SELECT data FROM strategies WHERE status = 'active' "
                          'ORDER BY version DESC LIMIT 1').fetchone()
    return Strategy.model_validate_json(row[0]) if row else None

  def latest_version(self) -> int:
    row = self.db.execute('SELECT MAX(version) FROM strategies').fetchone()
    return row[0] or 0

  def strategy_changes(self, version: int) -> List[RuleChange]:
    row = self.db.execute('SELECT changes FROM strategies WHERE version = ?',
                          (version,)).fetchone()
    return [RuleChange(**c) for c in json.loads(row[0])] if row else []

  def strategy_status(self, version: int) -> Optional[str]:
    row = self.db.execute('SELECT status FROM strategies WHERE version = ?',
                          (version,)).fetchone()
    return row[0] if row else None

  def activate(self, version: int):
    with self.db:
      self.db.execute(
          "UPDATE strategies SET status = 'retired' WHERE status = 'active'")
      self.db.execute(
          "UPDATE strategies SET status = 'active', approved_at = ? "
          'WHERE version = ?', (_now(), version))

  def set_status(self, version: int, status: str):
    with self.db:
      self.db.execute('UPDATE strategies SET status = ? WHERE version = ?',
                      (status, version))

  def list_strategies(self) -> List[tuple]:
    return self.db.execute(
        'SELECT version, status, created_at, approved_at FROM strategies '
        'ORDER BY version').fetchall()
