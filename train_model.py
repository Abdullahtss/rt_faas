"""Train the RT-FAAS fraud model and anomaly detector on the labelled BankSim file.

    .\\.venv\\Scripts\\python train_model.py

Writes models/fraud_model.joblib and models/metrics.json, and keeps a copy of
every run in models/history/.

Method (the code lives in backend/app/ml/training.py so the live "Retrain"
button runs exactly the same steps):

* Features are rebuilt causally - each row sees only that account's earlier
  rows, through the same code the live scorer uses.
* The split is by time, not random: training days come before validation
  days, which come before test days.
* The decision threshold is chosen on validation and only then applied to the
  held-out test days, so the reported numbers are not tuned on test.
* An Isolation Forest is fitted on genuine training payments only. It never
  sees a fraud label, so it can flag unusual payments of a kind nobody has
  labelled yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app.ml import registry  # noqa: E402
from app.ml.training import train  # noqa: E402


def main() -> int:
    bundle, metrics = train()
    entry = registry.record(bundle, metrics, source="train_model.py", force=True)
    print("\nsaved version v%d -> %s" % (entry["version"], registry.SERVING_MODEL))
    print("metrics -> %s" % registry.SERVING_METRICS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
