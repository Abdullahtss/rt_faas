"""Model versions on disk.

Every training run is kept as models/history/v<N>.joblib with its metrics.
The serving copy is models/fraud_model.joblib + models/metrics.json.

A retrained model is only promoted to serving if it is at least as good as
the current one on the same held-out test days (PR-AUC may drop by at most
PROMOTE_TOLERANCE). Otherwise it is kept in history as "rejected" and the
current model keeps serving. That gate stops a bad batch of analyst feedback
from silently making the system worse.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import List, Optional

from joblib import dump

MODEL_DIR = Path(__file__).resolve().parents[3] / "models"
HISTORY_DIR = MODEL_DIR / "history"
INDEX = HISTORY_DIR / "index.json"
SERVING_MODEL = MODEL_DIR / "fraud_model.joblib"
SERVING_METRICS = MODEL_DIR / "metrics.json"
PROMOTE_TOLERANCE = 0.005


def history() -> List[dict]:
    if not INDEX.exists():
        return []
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []


def current_metrics() -> Optional[dict]:
    if not SERVING_METRICS.exists():
        return None
    try:
        return json.loads(SERVING_METRICS.read_text(encoding="utf-8"))
    except Exception:
        return None


def record(bundle: dict, metrics: dict, source: str, force: bool = False) -> dict:
    """Save a trained model as a new version and promote it if it earns it."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    entries = history()
    version = max([e["version"] for e in entries], default=0) + 1
    bundle["version"] = version
    metrics["version"] = version

    old = current_metrics()
    old_ap = old["test"]["model"]["pr_auc"] if old else None
    new_ap = metrics["test"]["model"]["pr_auc"]
    if force or old_ap is None:
        promoted, note = True, "first model" if old_ap is None else "trained from the command line"
    elif new_ap >= old_ap - PROMOTE_TOLERANCE:
        promoted, note = True, "PR-AUC %.4f vs %.4f: kept or improved" % (new_ap, old_ap)
    else:
        promoted, note = False, "PR-AUC %.4f vs %.4f: worse, current model kept" % (new_ap, old_ap)
    metrics["promotion"] = {"promoted": promoted, "note": note, "previous_pr_auc": old_ap}

    model_path = HISTORY_DIR / ("v%d.joblib" % version)
    dump(bundle, model_path)
    (HISTORY_DIR / ("v%d.metrics.json" % version)).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if promoted:
        shutil.copyfile(model_path, SERVING_MODEL)
        SERVING_METRICS.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    t = metrics["test"]["model"]
    entry = {
        "version": version,
        "trained_at": metrics["trained_at"],
        "source": source,
        "model": metrics["chosen_model"],
        "pr_auc": t["pr_auc"],
        "precision": t["precision"],
        "recall": t["recall"],
        "threshold": metrics["threshold"],
        "feedback_used": metrics["feedback"]["used"],
        "promoted": promoted,
        "note": note,
    }
    entries.append(entry)
    INDEX.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return entry
