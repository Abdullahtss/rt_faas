"""Training pipeline shared by `train_model.py` and the live retrain button.

One function, `train`, does the whole job:

1. Rebuild features from the BankSim file by causal replay (no peeking ahead).
2. Optionally add analyst feedback (Approve / Block decisions) to the
   training days, with extra weight.
3. Split by time: train days 0-119, validation 120-139, test 140-179.
4. Fit the supervised fraud model(s) and pick a threshold on validation.
5. Fit an Isolation Forest on genuine training rows only, as an
   unsupervised "has this account ever looked like this?" detector.
6. Score everything on the held-out test days.
7. Record reference distributions for drift monitoring and a precision /
   recall curve for the threshold slider.

Nothing here touches the database or the web app, so it can run in a
background thread while the server keeps scoring.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .features import NUMERIC_FEATURES
from .replay import build_dataset

TRAIN_END_DAY = 119   # days 0..119 fit the model
VAL_END_DAY = 139     # days 120..139 choose the threshold
# days 140..179 are the held-out test set

FEEDBACK_WEIGHT = 5.0          # one analyst decision counts as five file rows
ANOMALY_HIGH_PCT = 99.5        # rarer than 99.5% of genuine payments
ANOMALY_MEDIUM_PCT = 98.0
DRIFT_FEATURES = [
    "log_amount",
    "amount_z",
    "amount_over_mean",
    "velocity_10m",
    "implied_speed",
    "log_gap_seconds",
    "history_count",
    "hour",
]

Log = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def summarise(name, y_true, scores, threshold) -> dict:
    pred = (scores >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, pred, average="binary", zero_division=0
    )
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "name": name,
        "threshold": round(float(threshold), 4),
        "pr_auc": round(float(average_precision_score(y_true, scores)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, scores)), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_negatives": int(tn),
        "alerts_per_1000": round(1000.0 * int(pred.sum()) / len(y_true), 2),
    }


def best_threshold(y_true, scores) -> float:
    """Probability cut-off with the best F1 on the validation days."""
    best_f1, best_t = -1.0, 0.5
    grid = np.unique(np.round(np.quantile(scores, np.linspace(0.90, 0.9999, 200)), 6))
    for t in grid:
        pred = (scores >= t).astype(int)
        if pred.sum() == 0:
            continue
        _, _, f1, _ = precision_recall_fscore_support(
            y_true, pred, average="binary", zero_division=0
        )
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def threshold_curve(y_true, scores) -> List[dict]:
    """Precision, recall and alert volume at a range of cut-offs.

    The monitoring page draws this so the threshold trade-off can be explored
    without retraining anything.
    """
    grid = sorted(set(
        [round(float(x), 4) for x in np.linspace(0.05, 0.95, 19)]
        + [round(float(x), 4) for x in np.linspace(0.955, 0.999, 23)]
    ))
    out = []
    total_fraud = int(y_true.sum())
    for t in grid:
        pred = scores >= t
        flagged = int(pred.sum())
        tp = int((pred & (y_true == 1)).sum())
        out.append({
            "threshold": t,
            "precision": round(tp / flagged, 4) if flagged else 1.0,
            "recall": round(tp / total_fraud, 4) if total_fraud else 0.0,
            "alerts_per_1000": round(1000.0 * flagged / len(y_true), 2),
        })
    return out


def reference_bins(X, names: List[str]) -> Dict[str, dict]:
    """Decile bins of each monitored feature on the training days."""
    ref = {}
    for feat in DRIFT_FEATURES:
        if feat not in names:
            continue
        col = X[:, names.index(feat)]
        edges = np.unique(np.quantile(col, np.linspace(0.1, 0.9, 9)))
        counts = np.bincount(np.searchsorted(edges, col, side="right"), minlength=len(edges) + 1)
        ref[feat] = {
            "edges": [float(e) for e in edges],
            "frac": [float(c) / len(col) for c in counts],
        }
    return ref


def _numeric_index(names: List[str]) -> List[int]:
    return [names.index(f) for f in NUMERIC_FEATURES if f in names]


def fit_anomaly(X_train_genuine, numeric_idx, log: Log, seed: int = 42) -> dict:
    """Isolation Forest on genuine training payments only.

    It never sees a fraud label. It learns what normal looks like and scores
    how easy a payment is to isolate from that crowd. The score is turned into
    a percentile against genuine training payments, so "99.7" reads as "rarer
    than 99.7% of normal payments".
    """
    rng = np.random.RandomState(seed)
    Xn = X_train_genuine[:, numeric_idx]
    fit_rows = rng.choice(Xn.shape[0], min(120000, Xn.shape[0]), replace=False)
    forest = IsolationForest(
        n_estimators=200, max_samples=1024, contamination="auto",
        random_state=seed, n_jobs=1,
    )
    t0 = time.perf_counter()
    forest.fit(Xn[fit_rows])
    ref_rows = rng.choice(Xn.shape[0], min(40000, Xn.shape[0]), replace=False)
    reference = np.sort(-forest.score_samples(Xn[ref_rows])).astype(np.float32)
    log("isolation forest      fitted in %5.1fs on %d genuine rows"
        % (time.perf_counter() - t0, len(fit_rows)))
    return {"forest": forest, "reference": reference, "numeric_idx": numeric_idx}


def anomaly_percentile(anomaly: dict, X) -> np.ndarray:
    raw = -anomaly["forest"].score_samples(X[:, anomaly["numeric_idx"]])
    ref = anomaly["reference"]
    return 100.0 * np.searchsorted(ref, raw, side="right") / len(ref)


def train(
    feedback: Optional[List[dict]] = None,
    candidates: tuple = ("logistic_regression", "gradient_boosting"),
    log: Log = print,
    stage: Log = _noop,
) -> tuple:
    """Train everything. Returns (bundle, metrics).

    `feedback` is a list of {"features": dict, "label": 0|1}. Those rows are
    added to the training days only, so validation and test stay untouched and
    the old and new model are judged on exactly the same payments.
    """
    started = time.perf_counter()
    feedback = feedback or []

    stage("Rebuilding features from the BankSim file")
    log("1. Rebuilding features from the BankSim file (causal replay)")
    features, labels, rule_scores, days = build_dataset()
    y = np.asarray(labels, dtype=np.int8)
    rules = np.asarray(rule_scores, dtype=np.float32)
    day = np.asarray(days, dtype=np.int16)
    log("   %d transactions, %d fraud (%.2f%%), %d days"
        % (len(y), int(y.sum()), 100.0 * y.mean(), int(day.max()) + 1))

    stage("Splitting by time and vectorising")
    train_mask = day <= TRAIN_END_DAY
    val_mask = (day > TRAIN_END_DAY) & (day <= VAL_END_DAY)
    test_mask = day > VAL_END_DAY
    vec = DictVectorizer(sparse=False, dtype=np.float32)
    X = vec.fit_transform(features)
    del features[:]
    names = list(vec.feature_names_)
    log("2. %d rows x %d features; train %d / validation %d / test %d rows"
        % (X.shape[0], X.shape[1], int(train_mask.sum()), int(val_mask.sum()), int(test_mask.sum())))

    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    w_train = np.ones(len(y_train), dtype=np.float32)

    fb_blocked = sum(1 for f in feedback if f["label"] == 1)
    if feedback:
        X_fb = vec.transform([f["features"] for f in feedback]).astype(np.float32)
        y_fb = np.asarray([f["label"] for f in feedback], dtype=np.int8)
        X_train = np.vstack([X_train, X_fb])
        y_train = np.concatenate([y_train, y_fb])
        w_train = np.concatenate([w_train, np.full(len(y_fb), FEEDBACK_WEIGHT, dtype=np.float32)])
        log("   + %d analyst decisions (%d blocked, %d approved) at weight %.0f"
            % (len(feedback), fb_blocked, len(feedback) - fb_blocked, FEEDBACK_WEIGHT))

    stage("Training the supervised model")
    log("3. Training")
    models = {}
    if "logistic_regression" in candidates:
        t0 = time.perf_counter()
        logreg = Pipeline([
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)),
        ])
        logreg.fit(X_train, y_train, clf__sample_weight=w_train)
        log("   logistic regression   fitted in %5.1fs" % (time.perf_counter() - t0))
        models["logistic_regression"] = logreg
    if "gradient_boosting" in candidates:
        t0 = time.perf_counter()
        hgb = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=40,
            l2_regularization=1.0, class_weight="balanced", early_stopping=True,
            validation_fraction=0.1, random_state=42,
        )
        hgb.fit(X_train, y_train, sample_weight=w_train)
        log("   gradient boosting     fitted in %5.1fs (%d trees)"
            % (time.perf_counter() - t0, hgb.n_iter_))
        models["gradient_boosting"] = hgb

    stage("Choosing the model and threshold on validation days")
    log("4. Choosing on the validation days")
    chosen_name, chosen_model, chosen_t, chosen_ap = None, None, 0.5, -1.0
    val_report = {}
    for name, model in models.items():
        scores = model.predict_proba(X_val)[:, 1]
        t = best_threshold(y_val, scores)
        report = summarise(name, y_val, scores, t)
        val_report[name] = report
        log("   %-22s PR-AUC %.4f  P %.3f  R %.3f  F1 %.3f"
            % (name, report["pr_auc"], report["precision"], report["recall"], report["f1"]))
        if report["pr_auc"] > chosen_ap:
            chosen_name, chosen_model, chosen_t, chosen_ap = name, model, t, report["pr_auc"]
    log("   chosen: %s (threshold %.4f)" % (chosen_name, chosen_t))

    stage("Fitting the Isolation Forest anomaly detector")
    log("5. Unsupervised anomaly detector")
    numeric_idx = _numeric_index(names)
    genuine_train = X[train_mask][y[train_mask] == 0]
    anomaly = fit_anomaly(genuine_train, numeric_idx, log)

    stage("Scoring the held-out test days")
    log("6. Held-out test days (never tuned on)")
    test_scores = chosen_model.predict_proba(X_test)[:, 1]
    model_report = summarise("ML: " + chosen_name, y_test, test_scores, chosen_t)
    test_pct = anomaly_percentile(anomaly, X_test)
    anomaly_report = summarise("Isolation Forest (>= %.1f pct)" % ANOMALY_HIGH_PCT,
                               y_test, test_pct, ANOMALY_HIGH_PCT)
    rules_high = summarise("Rules (score >= 70)", y_test, rules[test_mask], 70.0)
    rules_med = summarise("Rules (score >= 40)", y_test, rules[test_mask], 40.0)
    for r in (model_report, anomaly_report, rules_high):
        log("   %-34s PR-AUC %.4f  P %.3f  R %.3f  (%.1f alerts/1000)"
            % (r["name"], r["pr_auc"], r["precision"], r["recall"], r["alerts_per_1000"]))

    # How many fraud rows the anomaly detector catches that the model misses.
    model_hit = test_scores >= chosen_t
    anom_hit = test_pct >= ANOMALY_HIGH_PCT
    fraud = y_test == 1
    anomaly_report["fraud_missed_by_model_but_caught"] = int((fraud & ~model_hit & anom_hit).sum())

    stage("Measuring feature importance")
    log("7. What the model leans on")
    importances = []
    if chosen_name == "logistic_regression":
        coefs = chosen_model.named_steps["clf"].coef_[0]
        for nm, c in sorted(zip(names, coefs), key=lambda kv: -abs(kv[1]))[:12]:
            importances.append({"feature": nm, "weight": round(float(c), 4)})
    else:
        from sklearn.inspection import permutation_importance
        sub = min(40000, X_val.shape[0])
        idx = np.random.RandomState(0).choice(X_val.shape[0], sub, replace=False)
        pi = permutation_importance(
            chosen_model, X_val[idx], y_val[idx], n_repeats=3,
            random_state=0, scoring="average_precision", n_jobs=1,
        )
        for i in np.argsort(-pi.importances_mean)[:12]:
            importances.append({"feature": names[i], "weight": round(float(pi.importances_mean[i]), 4)})
    for imp in importances[:5]:
        log("   %-34s %.4f" % (imp["feature"], imp["weight"]))

    bundle = {
        "vectorizer": vec,
        "model": chosen_model,
        "model_name": chosen_name,
        "threshold": chosen_t,
        "feature_names": names,
        # Training-median row, used to explain individual predictions.
        "baseline": np.median(X[train_mask], axis=0).astype(np.float32),
        "anomaly": anomaly,
        "reference": reference_bins(X[train_mask], names),
    }
    metrics = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": "bs140513_032310.csv (BankSim, Lopez-Rojas & Axelsson 2014)",
        "rows": int(len(y)),
        "fraud_rows": int(y.sum()),
        "fraud_rate": round(float(y.mean()), 5),
        "split": {
            "method": "chronological",
            "train_days": [0, TRAIN_END_DAY],
            "validation_days": [TRAIN_END_DAY + 1, VAL_END_DAY],
            "test_days": [VAL_END_DAY + 1, int(day.max())],
        },
        "chosen_model": chosen_name,
        "threshold": round(float(chosen_t), 4),
        "validation": val_report,
        "test": {
            "model": model_report,
            "anomaly": anomaly_report,
            "rules_high": rules_high,
            "rules_medium": rules_med,
        },
        "curve": threshold_curve(y_test, test_scores),
        "feedback": {
            "used": len(feedback),
            "blocked": fb_blocked,
            "approved": len(feedback) - fb_blocked,
            "weight": FEEDBACK_WEIGHT,
        },
        "top_features": importances,
        "train_seconds": round(time.perf_counter() - started, 1),
    }
    log("done in %.1fs" % metrics["train_seconds"])
    return bundle, metrics
