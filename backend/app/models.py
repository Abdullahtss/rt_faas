from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="PKR")
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    city: Mapped[str] = mapped_column(String(64), default="")
    merchant: Mapped[str] = mapped_column(String(128), default="")
    channel: Mapped[str] = mapped_column(String(32), default="card_not_present")
    ingested_at: Mapped[datetime] = mapped_column(DateTime)
    processing_ms: Mapped[float] = mapped_column(Float, default=0.0)
    scenario: Mapped[str] = mapped_column(String(32), default="normal")

    risk: Mapped[Optional["RiskResult"]] = relationship(back_populates="transaction", uselist=False)
    review: Mapped[Optional["Review"]] = relationship(back_populates="transaction", uselist=False)
    alert: Mapped[Optional["Alert"]] = relationship(back_populates="transaction", uselist=False)


class RiskResult(Base):
    __tablename__ = "risk_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("transactions.id"), unique=True, index=True)
    score: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(16), index=True)
    velocity_pts: Mapped[int] = mapped_column(Integer, default=0)
    travel_pts: Mapped[int] = mapped_column(Integer, default=0)
    amount_pts: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    # Model side of the score. Null on rows written before a model existed.
    ml_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ml_flag: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ml_reasons: Mapped[str] = mapped_column(Text, default="[]")
    # Isolation Forest percentile (99.7 = rarer than 99.7% of genuine payments).
    anomaly_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The exact model inputs, kept so analyst decisions can become training rows.
    features: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="risk")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("seq", name="uq_alert_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("transactions.id"), unique=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    transaction: Mapped[Transaction] = relationship(back_populates="alert")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("transactions.id"), unique=True, index=True)
    decision: Mapped[str] = mapped_column(String(16))  # approved | blocked
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(64), default="analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime)

    transaction: Mapped[Transaction] = relationship(back_populates="review")
