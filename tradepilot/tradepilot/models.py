"""Core data models."""
import datetime as dt
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Action = Literal['buy', 'sell', 'hold', 'pass']
Label = Literal['good', 'bad', 'neutral']


class Decision(BaseModel):
  """One trading judgment, including the trades you decided NOT to make.

  `reasons` are ids of strategy rules that supported the action (for a
  'pass', the rules that made you pass). `against` are rules that argued
  the other way. `signals` holds the raw indicator values you saw, so the
  reasoning can be checked later against what the rules say.
  """
  id: Optional[int] = None
  date: dt.date
  time: str = ''
  symbol: str
  action: Action
  price: Optional[float] = None
  quantity: float = 0
  reasons: List[str] = Field(default_factory=list)
  against: List[str] = Field(default_factory=list)
  signals: Dict[str, float] = Field(default_factory=dict)
  confidence: Optional[float] = None  # 0-1
  note: str = ''
  strategy_version: Optional[int] = None


class Bar(BaseModel):
  date: dt.date
  open: float
  high: float
  low: float
  close: float
  volume: float = 0


class Outcome(BaseModel):
  """How a decision played out after `horizon` trading days.

  Horizon 0 is the close of the decision day.
  """
  decision_id: int
  horizon: int
  ret_pct: float  # close at horizon vs decision price
  max_up_pct: float  # best high within the window
  max_down_pct: float  # worst low within the window
  label: Label


class Rule(BaseModel):
  id: str
  description: str
  kind: Literal['entry', 'filter', 'exit'] = 'entry'
  weight: float = 1.0
  enabled: bool = True
  params: Dict[str, float] = Field(default_factory=dict)


class StrategyParams(BaseModel):
  # A move of at least this many percent counts as a real move when
  # labeling outcomes (should exceed round-trip costs).
  win_threshold_pct: float = 1.0
  loss_threshold_pct: float = 1.0
  horizons: List[int] = Field(default_factory=lambda: [0, 1, 5])
  primary_horizon: int = 5
  # Learning guardrails.
  min_samples: int = 20
  max_weight_change: float = 0.25  # fraction per review
  min_weight: float = 0.1
  max_weight: float = 3.0
  disable_below: float = 0.35  # posterior hit rate
  # Rule statistics use decisions from this many calendar days back, so
  # the strategy adapts to the current market regime (0 = all history).
  lookback_days: int = 120


class Strategy(BaseModel):
  version: int = 1
  name: str = 'default'
  params: StrategyParams = Field(default_factory=StrategyParams)
  rules: List[Rule] = Field(default_factory=list)
  notes: str = ''

  def rule(self, rule_id: str) -> Optional[Rule]:
    return next((r for r in self.rules if r.id == rule_id), None)


class RuleStat(BaseModel):
  rule_id: str
  n: int = 0
  good: int = 0
  bad: int = 0
  neutral: int = 0
  avg_ret_pct: float = 0.0
  by_action: Dict[str, int] = Field(default_factory=dict)

  @property
  def hit_rate(self) -> float:
    """Posterior good/(good+bad) with a uniform prior; 0.5 when unknown."""
    return (self.good + 1) / (self.good + self.bad + 2)


class RuleChange(BaseModel):
  rule_id: str
  field: str
  old: str
  new: str
  reason: str
  source: Literal['stats', 'llm']


# LLM review schemas (structured outputs need fixed keys, no free dicts).


class DecisionReview(BaseModel):
  decision_id: int
  reasoning_quality: Literal['sound', 'flawed', 'insufficient_data']
  outcome_attribution: Literal['skill', 'luck', 'bad_luck', 'mistake',
                               'unclear']
  comment: str


class RuleAdjustment(BaseModel):
  rule_id: str
  param: str = Field(description="A key in the rule's params, or 'weight'.")
  new_value: float
  rationale: str


class DailyReview(BaseModel):
  summary: str
  decision_reviews: List[DecisionReview]
  lessons: List[str]
  rule_adjustments: List[RuleAdjustment]
  new_rule_ideas: List[str]
