from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TransactionIn(BaseModel):
    account_id: str = Field(..., min_length=3, max_length=32)
    amount: float = Field(..., gt=0)
    currency: str = "PKR"
    timestamp: Optional[datetime] = None
    lat: float
    lon: float
    city: str = ""
    merchant: str = "POS"
    channel: str = "card_not_present"
    scenario: str = "normal"


class Reason(BaseModel):
    rule: str
    points: int
    detail: str


class MlReason(BaseModel):
    feature: str
    contribution: float
    detail: str


class ScoredTransaction(BaseModel):
    id: str
    account_id: str
    account_masked: str
    amount: float
    currency: str
    timestamp: datetime
    lat: float
    lon: float
    city: str
    merchant: str
    channel: str
    scenario: str
    score: int
    level: Literal["Low", "Medium", "High"]
    velocity_pts: int
    travel_pts: int
    amount_pts: int
    reasons: List[Reason]
    processing_ms: float
    seq: Optional[int] = None
    review: Optional[str] = None
    review_notes: Optional[str] = None
    # Model side. All None/empty when no model is loaded.
    ml_score: Optional[int] = None
    ml_flag: bool = False
    ml_level: Optional[Literal["Low", "Medium", "High"]] = None
    ml_reasons: List[MlReason] = []
    rule_level: Optional[Literal["Low", "Medium", "High"]] = None
    # Unsupervised side. None when the loaded model has no anomaly detector.
    anomaly_pct: Optional[float] = None
    anomaly_level: Optional[Literal["Low", "Medium", "High"]] = None


class ReviewIn(BaseModel):
    decision: Literal["approved", "blocked"]
    notes: str = ""
    reviewer: str = "analyst"


class SearchQuery(BaseModel):
    account_id: Optional[str] = None
    level: Optional[str] = None
    decision: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    limit: int = 100
    offset: int = 0


class ThresholdIn(BaseModel):
    # None restores the threshold the model was trained with.
    threshold: Optional[float] = Field(None, ge=0.01, le=0.999)


class SimulatorConfig(BaseModel):
    tps: float = Field(2.0, ge=0.2, le=50)
    running: bool = True


class InjectIn(BaseModel):
    scenario: Literal["burst", "travel", "amount", "combo"] = "combo"
    account_id: Optional[str] = None
