"""Load the trained fraud model and score transactions on the live path.

The app runs fine without a model file: `MODEL.available` stays False, the
rules keep working on their own, and the UI simply shows no ML score. Train
one with `python train_model.py`.
"""
from __future__ import annotations

import json
import threading
from math import expm1
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "models" / "fraud_model.joblib"
# Analyst-chosen threshold override, kept across restarts until the next
# model is promoted.
RUNTIME_PATH = DEFAULT_PATH.parent / "runtime.json"
ANOMALY_HIGH_PCT = 99.5
ANOMALY_MEDIUM_PCT = 98.0

# Contributions below this move the probability too little to be worth a line
# in the case file.
MIN_CONTRIBUTION = 0.01


def _readable(group: str, feats: Dict[str, object]) -> str:
    """One plain-English line describing this feature group's value."""
    if group == "log_amount":
        return "Amount %.0f" % expm1(float(feats["log_amount"]))
    if group == "amount_z":
        if not feats["has_baseline"]:
            return "No 30-day amount baseline for this account yet"
        return "Amount is %.1f sigma from the account's 30-day mean" % float(feats["amount_z"])
    if group == "amount_over_mean":
        return "%.1fx the account's average amount" % float(feats["amount_over_mean"])
    if group == "velocity_10m":
        return "%d transactions in 10 minutes" % int(feats["velocity_10m"])
    if group == "implied_speed":
        return "Implied speed %.0f km/h since last transaction" % float(feats["implied_speed"])
    if group == "log_gap_seconds":
        if not feats["has_prev"]:
            return "First transaction seen on this account"
        minutes = expm1(float(feats["log_gap_seconds"])) / 60.0
        if minutes < 60:
            return "%.0f minutes since the previous transaction" % minutes
        return "%.1f hours since the previous transaction" % (minutes / 60.0)
    if group == "history_count":
        return "%d prior transactions in the last 30 days" % int(feats["history_count"])
    if group in ("hour", "is_night"):
        return "Transaction at %02d:00" % int(feats["hour"])
    if group == "category":
        return "Merchant category: %s" % (feats["category"] or "unknown")
    if group == "merchant_id":
        return "Merchant %s" % (feats["merchant_id"] or "unknown")
    if group == "has_baseline":
        return "Account has a usable amount baseline" if feats["has_baseline"] else "No amount baseline yet"
    if group == "has_prev":
        return "Account has earlier activity" if feats["has_prev"] else "No earlier activity"
    return group


class FraudModel:
    """Thin wrapper around the joblib bundle written by train_model.py."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bundle = None
        self._groups: Dict[str, List[int]] = {}
        self._np = None
        self.path: Optional[Path] = None
        self.error = ""
        self._override: Optional[float] = None

    # -- lifecycle ----------------------------------------------------

    def load(self, path: Optional[Path] = None) -> bool:
        path = Path(path or DEFAULT_PATH)
        with self._lock:
            self.path = path
            if not path.exists():
                self.error = "no model file at %s (run: python train_model.py)" % path
                self._bundle = None
                return False
            try:
                import numpy as np
                from joblib import load

                bundle = load(path)
                groups: Dict[str, List[int]] = {}
                for i, name in enumerate(bundle["feature_names"]):
                    groups.setdefault(name.split("=")[0], []).append(i)
                self._np = np
                self._bundle = bundle
                self._groups = groups
                self.error = ""
                self._override = self._read_override(bundle)
                return True
            except Exception as exc:  # pragma: no cover - defensive
                self._bundle = None
                self.error = "could not load model: %s" % exc
                return False

    def _read_override(self, bundle) -> Optional[float]:
        try:
            data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        # An override belongs to the model version it was set on.
        if data.get("version") != bundle.get("version"):
            return None
        t = data.get("threshold")
        return float(t) if t is not None else None

    def set_threshold(self, value: Optional[float]) -> None:
        """Override the trained threshold (None restores it)."""
        self._override = None if value is None else float(value)
        RUNTIME_PATH.write_text(json.dumps({
            "threshold": self._override,
            "version": self._bundle.get("version") if self._bundle else None,
        }), encoding="utf-8")

    def unload(self) -> None:
        with self._lock:
            self._bundle = None
            self._groups = {}

    # -- properties ---------------------------------------------------

    @property
    def available(self) -> bool:
        return self._bundle is not None

    @property
    def name(self) -> str:
        return self._bundle["model_name"] if self._bundle else ""

    @property
    def trained_threshold(self) -> float:
        return float(self._bundle["threshold"]) if self._bundle else 1.0

    @property
    def threshold(self) -> float:
        if not self._bundle:
            return 1.0
        return self._override if self._override is not None else self.trained_threshold

    @property
    def version(self) -> Optional[int]:
        return self._bundle.get("version") if self._bundle else None

    @property
    def has_anomaly(self) -> bool:
        return bool(self._bundle and self._bundle.get("anomaly"))

    @property
    def reference(self) -> dict:
        return (self._bundle or {}).get("reference") or {}

    def info(self) -> dict:
        return {
            "available": self.available,
            "model": self.name,
            "version": self.version,
            "threshold": round(self.threshold, 4) if self.available else None,
            "trained_threshold": round(self.trained_threshold, 4) if self.available else None,
            "threshold_overridden": self._override is not None,
            "anomaly_detector": self.has_anomaly,
            "path": str(self.path) if self.path else None,
            "error": self.error,
        }

    # -- scoring ------------------------------------------------------

    def probability(self, feats: Dict[str, object]) -> Optional[float]:
        """Fraud probability for one feature dict, or None without a model."""
        bundle = self._bundle  # one snapshot, in case a retrain swaps it mid-call
        if not bundle:
            return None
        row = bundle["vectorizer"].transform([feats])
        return float(bundle["model"].predict_proba(row)[0, 1])

    def anomaly(self, feats: Dict[str, object]) -> Optional[float]:
        """Percentile of how unusual this payment is versus genuine training
        payments (99.7 = rarer than 99.7% of them), or None without a detector."""
        bundle = self._bundle
        if not bundle or not bundle.get("anomaly"):
            return None
        np = self._np
        det = bundle["anomaly"]
        row = bundle["vectorizer"].transform([feats])
        raw = -det["forest"].score_samples(row[:, det["numeric_idx"]])[0]
        ref = det["reference"]
        return round(100.0 * float(np.searchsorted(ref, raw, side="right")) / len(ref), 1)

    def explain(self, feats: Dict[str, object], top: int = 4) -> List[dict]:
        """Which feature groups pushed this probability up, and by how much.

        Each group is swapped back to the training-median reference row and the
        transaction re-scored; the drop in probability is that group's share.
        All the swaps go through the model in one batch, so this costs about as
        much as a single prediction.
        """
        bundle = self._bundle
        if not bundle:
            return []
        np = self._np
        vec = bundle["vectorizer"]
        model = bundle["model"]
        baseline = bundle.get("baseline")

        row = vec.transform([feats])
        full = float(model.predict_proba(row)[0, 1])
        if baseline is None:
            return []

        names = list(self._groups.keys())
        variants = np.repeat(np.asarray(row, dtype=np.float32), len(names), axis=0)
        for r, name in enumerate(names):
            for col in self._groups[name]:
                variants[r, col] = baseline[col]
        probs = model.predict_proba(variants)[:, 1]

        out = []
        for name, p in zip(names, probs):
            delta = full - float(p)
            if delta >= MIN_CONTRIBUTION:
                out.append({
                    "feature": name,
                    "contribution": round(delta, 4),
                    "detail": _readable(name, feats),
                })
        out.sort(key=lambda d: -d["contribution"])
        return out[:top]


def anomaly_level(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    if pct >= ANOMALY_HIGH_PCT:
        return "High"
    if pct >= ANOMALY_MEDIUM_PCT:
        return "Medium"
    return "Low"


MODEL = FraudModel()
