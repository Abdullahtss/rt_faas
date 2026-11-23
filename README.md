# RT-FAAS — Real-Time Transaction Fraud & Anomaly Alerting System

A website that ingests a live payment stream, scores each transaction with three explainable heuristics, and shows high-risk alerts in under one second.

## Stack

- **Website:** HTML, CSS, JavaScript (no React, no TypeScript, no npm)
- **Backend:** Python 3.9+ / FastAPI / SQLite (PostgreSQL optional)
- **Realtime:** WebSockets with reconnect and missed-alert replay
- **Rules:** velocity, impossible travel, amount spike
- **Model:** scikit-learn gradient boosting, trained on the labelled BankSim file

## Run

From this `rt-faas` folder:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

Or: `.\start.ps1`

Open **http://127.0.0.1:8000**

| Page | URL |
|---|---|
| Home | http://127.0.0.1:8000/ |
| Live console | http://127.0.0.1:8000/console |
| Search & audit | http://127.0.0.1:8000/search |
| Model monitor | http://127.0.0.1:8000/monitor |
| API docs | http://127.0.0.1:8000/docs |

Default token (already used by the site): `rtfaas-dev-token`

## Demo

1. Open **Live console** → **Play BankSim**
2. Payments are rows from the public BankSim file (Spanish card payments, Madrid).
3. Click **Next labeled fraud** to jump to a row the dataset itself marks as fraud.
4. Open the case file → **Block** or **Approve**
5. **Search & audit** → filter → **Export CSV**

## Machine learning

The three rules still run on every transaction. A trained model runs beside
them and gets an equal vote: a transaction is **High** if either the rules or
the model say so. Each side explains itself separately in the case file.

### Training

```powershell
.\.venv\Scripts\python train_model.py
```

Takes about two minutes and writes `models/fraud_model.joblib` and
`models/metrics.json`. The app loads the model at startup; without one it
falls back to rules only and says so in the log.

### How it is trained

| Step | What happens |
|---|---|
| Features | 11 numeric + one-hot category and merchant (76 columns) |
| Build | Replayed in time order — each row sees only that account's **earlier** rows |
| Split | Chronological: days 0–119 train, 120–139 validation, 140–179 test |
| Models | Logistic regression and gradient boosting, both class-balanced |
| Threshold | Chosen on validation, then applied unchanged to the test days |

Features come from `app/ml/features.py`, which the live scorer calls too, so a
feature cannot be built one way in training and another way in production.

### Results on the held-out test days (147,861 transactions, 1,600 fraud)

| | PR-AUC | Precision | Recall | F1 | Alerts/1000 |
|---|---|---|---|---|---|
| **Gradient boosting** | **0.964** | **0.911** | **0.890** | **0.900** | 10.6 |
| Logistic regression (validation) | 0.900 | 0.843 | 0.806 | 0.824 | 10.7 |
| Rules, score ≥ 70 | 0.069 | 0.000 | 0.000 | 0.000 | 0.1 |
| Rules, score ≥ 40 | 0.069 | 0.000 | 0.000 | 0.000 | 0.6 |

The rules score nothing on this dataset, and that is a fair result rather than
a broken one: BankSim puts every payment in Madrid and synthesises timestamps,
so impossible travel and velocity have nothing to detect. The rules were
written for account takeover; this file contains merchant-category fraud. It
is exactly the gap the model fills — and the reason to keep both.

Scoring costs about 1 ms per transaction and explaining about 2 ms, so the
sub-second budget is unaffected.

`GET /api/v1/model` returns the live model plus the full metrics file.

### Anomaly detector (Isolation Forest)

Next to the supervised model sits an Isolation Forest, trained only on
genuine payments from the training days. It never sees a fraud label, so it
can flag unusual payments of a kind nobody has labelled yet. Its output is a
percentile: "99.7" means rarer than 99.7% of genuine training payments.
At 99.5 or above it is High, at 98 or above Medium.

Because it is unsupervised and noisier, it gets a smaller vote: on its own it
can send a payment to the review queue (Medium) but never raises a High alert.
Its held-out test numbers are reported separately in `metrics.json`
(`test.anomaly`), including how many fraud payments it caught that the
supervised model missed.

### Analyst feedback loop and model versions

Every scored payment stores its exact model inputs. When an analyst presses
**Block** or **Approve**, that decision becomes a training label (Block =
fraud, Approve = genuine). **Model monitor → Retrain with feedback** then:

1. collects the decisions (BankSim rows from the validation and test days are
   left out, so old and new model are judged on the same unseen payments),
2. retrains in a background thread at 5× weight per decision while the server
   keeps scoring,
3. saves the result as a new version in `models/history/`,
4. promotes it only if its held-out PR-AUC is not worse than the serving
   model's (tolerance 0.005), and hot-swaps it without a restart.

`POST /api/v1/model/retrain` starts it, `GET /api/v1/model/retrain` reports
progress, `GET /api/v1/model/history` lists every version.

### Model monitor page

`http://127.0.0.1:8000/monitor` shows:

| Panel | What it tells you |
|---|---|
| Serving model | Version, PR-AUC, precision, recall, F1 on the test days |
| Threshold slider | Precision, recall and alert volume at any cut-off; **Apply live** changes the serving threshold immediately (`POST /api/v1/model/threshold`) |
| Live traffic | Payments, Medium and High per minute for the last 30 minutes |
| Who raised the alerts | Rules only, model only, both, anomaly detector |
| Analyst-confirmed precision | Of each detector's reviewed alerts, the share analysts blocked |
| Data drift | Population Stability Index of each live input against the training days (< 0.1 stable, 0.1–0.25 watch, > 0.25 drifted) |
| Model versions | Every training run and whether it was promoted |

## Tests

```powershell
.\.venv\Scripts\pytest backend\tests -q
.\.venv\Scripts\python loadtest.py
```

### A note on `loadtest.py` and US-11

The load probe fires 200 ingest requests **simultaneously** at a single
uvicorn worker and then asserts that the *client* round trip stays under one
second. It does not: the requests queue, and the last one waits several
seconds for its turn. That was already true before the model was added, and it
is a throughput ceiling, not a scoring-speed problem.

Server-side scoring is the number the one-second requirement is about, and it
holds comfortably:

| | p50 | p95 | max |
|---|---|---|---|
| Scoring + persist, per transaction | 5.9 ms | 14.8 ms | 86 ms |

Concurrent ingest used to return HTTP 500 (`database is locked`) because
SQLite serialises writers and gave up the moment one was busy. The engine now
opens SQLite in write-ahead logging mode with a 30-second busy timeout, so all
200 requests complete. Raising actual throughput would mean more uvicorn
workers or Postgres, which is what `docker-compose.yml` is there for.
