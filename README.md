[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/n73txmTf)

# PO Email Intelligence

Read Outlook inbox messages, learn which ones are Purchase Orders, and pull out
the structured fields — PO numbers, suppliers, dates, amounts, item codes, and
size-keyed contract tables — plus OCR text from image attachments.

The app is two processes: a **FastAPI** backend that handles OAuth, Microsoft
Graph calls, ML, and extraction, and a **Streamlit** frontend that calls the
API over HTTP. No database — sessions, labels, and models live in local files
or memory.

### At a glance

| | |
|---|---|
| **Classifier** | TF-IDF (1–2 grams) + Logistic Regression, scikit-learn |
| **Raw labels** | 62 hand-labeled emails — `data/labels.jsonl` |
| **After cleaning** | 0 empty dropped, **2 duplicates dropped** → **60 unique** (29 PO / 31 Not-PO) |
| **Train / test split** | Time-based — **oldest 48 → train pool**, **newest 12 → held-out test** (never seen during fit or CV) |
| **Headline (test set)** | **Accuracy 83.3% (10 / 12)**, macro F1 0.829, macro precision 0.875, macro recall 0.833 |
| **Confusion (test)** | `[[6, 0], [2, 4]]` — **0 false negatives, 2 false positives** (both Non-PO → PO, both at <60% confidence) |
| **CV stability (train pool only)** | Stratified 5-fold inside the 48-email pool — **100.0% ± 0.0%** accuracy, **1.000 ± 0.000** macro F1. Still perfect because the hard examples sit in the test set, not the pool. |
| **Metadata coverage** | 12 / 60 labels carry `received_at`, 12 / 60 carry `from_addr` — time basis = `mixed` |
| **Misclassified** | 2 FPs surfaced — complaint email referencing "PO-5312" and a quote request — both fired at near-boundary confidence |
| **Live unseen-inbox check** | 13 inbox emails unseen during training, mean confidence **59.9%** (max 65.3%) — model knows it's unsure on truly fresh email |
| **Deployed model** | The model scored on the test set is the one that ships (no separate "fit on 100%" variant) |
| **Body extraction** | Regex — 5 fields, 14 date formats, currency-aware, ≤20 item codes |
| **Table extraction** | BeautifulSoup → 13-column MASTER schema (3 text + 10 size cols) |
| **Image OCR** | Tesseract → re-run body regex on OCR text |

---

## Features

### Outlook sign-in (Microsoft Graph OAuth2)
- Authorization-code flow against `login.microsoftonline.com/consumers`
  (personal Microsoft accounts).
- `prompt=select_account` always shows the account chooser, never silently
  reuses a cached identity.
- Tokens held in-memory in the API process; refresh token used to mint new
  access tokens when Graph returns 401.

### Live inbox
- Fetches the newest messages from `/me/mailFolders/inbox/messages`.
- Per message shows subject, sender, received time, read state, and
  attachment indicator.
- After the classifier is trained, each message also shows a PO / Not-PO
  prediction badge with confidence.

### PO classifier (train your own)

- Label individual emails as **PO** or **Not PO** from the UI.
- Trains a `TfidfVectorizer + LogisticRegression` pipeline on the labels —
  fast on CPU (sub-second training on ~50 samples), no GPU needed.
- **Feature extraction:** subject + body combined into a single text;
  TF-IDF with 1–2 grams, English stop-words removed, sublinear TF,
  20 000 max features, `min_df=1`.
- **Classifier:** Logistic Regression, `max_iter=1000`,
  `class_weight="balanced"` — robust against class imbalance.

#### Training architecture — train pool / held-out test, CV inside pool only

The pipeline follows a strict train / validation / test discipline:

1. **Load + clean.** Drop records where both the subject and body are
   empty (nothing to learn from).
2. **Deduplicate** by normalized (subject + body) text. Forwarded POs
   that hit the inbox three times with three different Outlook IDs
   collapse to one record (newest `received_at` / `labeled_at` wins).
3. **Sort by time** — `received_at` if present, else `labeled_at` as
   fallback.
4. **Time-based train pool / test split.** The **newest 20%** becomes
   the **final test set** — never used during fitting, CV, or tuning.
   The older 80% is the **train pool**. Refuses to train if either side
   loses a class.
5. **Cross-validation lives inside the train pool only.** Used as a
   model-selection / stability check, never as the headline number.
   - **`StratifiedGroupKFold` by sender** auto-activates once ≥80% of
     train-pool labels carry `from_addr` (with ≥2 distinct senders) —
     emails from the same supplier never straddle a fold, which catches
     "same template" leakage that random folds miss.
   - Falls back to **`StratifiedKFold`** when sender info isn't yet
     available (e.g. labels created before sender capture was added).
6. **Fit one model on the train pool**, score it on the held-out test
   set. Those test metrics are the **headline accuracy / precision /
   recall / F1 / confusion matrix**. The model artifact written to
   `models/classifier/model.joblib` is the *same model* that produced
   those numbers — no separate "fit on 100%" variant to muddy the
   story.
7. **Misclassified test examples** (FP and FN) are written into
   `metadata.json` with confidence, sender, and received-at, and
   surfaced in the UI for manual review.
8. **Split before preprocessing.** TF-IDF lives inside the sklearn
   `Pipeline` so the vocabulary, IDF weights and stop-word list never
   see evaluation data — `Pipeline.fit()` is called per fold / per
   split.

This maps 1-to-1 onto the standard ML hygiene checklist:

| Best practice | How it's enforced |
|---|---|
| Don't test on training emails | Final test set held out before fit; CV inside train pool only |
| Split before preprocessing | TF-IDF inside `Pipeline`, refit per fold |
| Remove duplicates before splitting | `_deduplicate()` by normalized text |
| Avoid random splits when senders correlate | `StratifiedGroupKFold` by `from_addr` when coverage allows |
| Prefer time-based test split | Sort by `received_at` (or `labeled_at`); newest 20% is test |
| Separate final test set | Never enters CV; only scored once at the end |
| CV used only for model selection / stability | Reported as a secondary stability number, not the headline |
| Report accuracy, precision, recall, F1, confusion | All five, per class + macro, on both test and CV |
| Track FP / FN | `misclassified_test` list saved in metadata, with kind / confidence / sender |
| Don't trust 100% blindly | UI shows a "add hard examples" hint when test ≥99% on a small set |

- Re-label any email at any time (overwrites the previous label); re-train
  to update the model.
- Reset training data (labels and/or model) from the UI with a confirmation.
- Model artifact saved as `models/classifier/model.joblib` plus a
  `metadata.json` with all training stats.

#### Current model performance

Starting from **62 hand-labeled emails**, the pipeline drops 0 empty and
**2 duplicate** forwarded POs, leaving **60 unique labels: 29 PO + 31
Not-PO**. The 12 newest emails — many of them adversarial cases
(complaints referencing a PO, quote requests, ambiguous wording) —
were added after the previous training round.

The 60 records are sorted oldest-first by `received_at` where available
(12 of 60) and by `labeled_at` for older records — `time_basis` reports
as `"mixed"`. The **newest 12** form the held-out test set; the
**oldest 48** form the train pool.

##### Headline — held-out test set

The reported model never saw these 12 emails during fit or CV.

| | PO | Not-PO | Macro avg |
|---|---|---|---|
| Precision | 0.75 | 1.00 | **0.875** |
| Recall    | 1.00 | 0.67 | **0.833** |
| F1        | 0.857 | 0.800 | **0.829** |
| Support   | 6    | 6    | 12 |

- **Accuracy: 83.3% (10 / 12 correct)**
- **Confusion `[[6, 0], [2, 4]]`** — 6 PO correctly called PO, **0 false
  negatives** (every actual PO was caught), **2 false positives**
  (Non-PO predicted as PO), 4 Not-PO correctly called Not-PO.

The error pattern is asymmetric: the model **catches every PO** but
**over-predicts PO** when an email's vocabulary brushes against PO
territory.

##### Misclassified test emails

| # | Kind | Confidence | True | Pred | Subject |
|---|------|------------|------|------|---------|
| 1 | False positive (Non-PO → PO) | **52%** | Not-PO | PO | *Issue with items supplied under PO-5312* |
| 2 | False positive (Non-PO → PO) | **56%** | Not-PO | PO | *Request for quotation - toner cartridges* |

Both are textbook hard examples the README/UI predicted would surface:

- (1) is a **complaint about an existing PO** — the email *mentions* a
  PO number but isn't itself a purchase order. TF-IDF picks up the
  "PO-5312" reference and tips the linear model over.
- (2) is a **quotation request** — the buyer asking the supplier
  *to quote*, not an order. Vocabulary overlaps with real orders
  ("toner cartridges", units, urgency words).

Both mistakes fired at **near-decision-boundary confidence (52% and
56%)**, which is the healthiest possible failure mode: the model isn't
wrong with conviction, it's wrong while admitting it doesn't know.
These two emails are the **highest-value relabels** to feed into the
next training round.

##### Cross-validation on the train pool

CV is the secondary stability signal — run only on the 48-email train
pool, **never on the test set**.

| | |
|---|---|
| Strategy | `StratifiedKFold`, `shuffle=True`, `random_state=42` |
| Folds (k) | 5 (auto-clamped to `min(5, n_po, n_not_po, distinct_groups)`) |
| Train-pool size per fold | 38 train / 10 val (with stratification) |
| Per-fold accuracy | 100%, 100%, 100%, 100%, 100% |
| **Accuracy (mean ± std)** | **100.0% ± 0.0%** |
| Per-fold macro F1 | 1.000, 1.000, 1.000, 1.000, 1.000 |
| **Macro F1 (mean ± std)** | **1.000 ± 0.000** |
| OOF support | 23 PO + 25 Not-PO = 48 (every train-pool email predicted exactly once by a model that didn't see it) |
| OOF confusion | `[[23, 0], [0, 25]]` — 0 false positives, 0 false negatives |

CV will auto-upgrade to **`StratifiedGroupKFold` by sender** the moment
`from_addr` coverage on the train pool crosses ≥80% (currently 0 / 48
on the train pool — the 12 labels that carry sender info all landed in
the newest-20% test set this round). Group-based CV stops emails from
the same supplier from straddling a fold — the strictest random-CV
correction for sender-clustered corpora.

##### Why is CV still 100% while the test set is 83%?

This gap is real and explainable, not a bug. The **10 newly-labeled
hard examples** (complaints, quotes, ambiguous PO-ish emails) were
added most recently, so they sorted to the newest 20% by time and
landed in the **held-out test set**. The 48 emails in the train pool
are still the clean, easily-separable set the model learned in earlier
rounds — so CV inside that pool stays perfect.

As you keep labeling, today's test emails will age into older positions,
shift into the train pool, and CV will start dropping below 100% too —
which is the correct dynamic. The test number is the honest one
*right now*; CV will become a more useful stability indicator as the
hard examples propagate into the training data.

This is the architecture working as designed:

- **Test is the headline** — it's the only number that reflects "predict
  on emails the model has never seen, including the hardest ones".
- **CV is a stability check** — it tells you whether the training pool
  itself is internally consistent. 100% here means "the model fits the
  train pool perfectly with no train/val variance", which is fine; the
  pool just isn't yet a hard distribution.

##### Live unseen-inbox check

After every training run the API also predicts the **current inbox
emails that aren't in `labels.jsonl`**. No ground truth, so no accuracy
— but the confidence distribution is the strongest signal we have for
out-of-distribution behavior.

| | |
|---|---|
| Inbox fetched | 50 |
| Unseen (not in labels) | **13** |
| Predicted PO | 10 / 13 |
| Predicted Not-PO | 3 / 13 |
| **Mean confidence** | **59.9%** |
| Min confidence | 51.9% |
| Max confidence | **65.3%** |

Confidence histogram:

| Bucket | Count |
|---|---|
| 50–60% | 4 |
| 60–70% | 9 |
| 70–80% | 0 |
| 80–90% | 0 |
| 90–100% | 0 |

**Nothing crosses 70%.** That's a huge drop from the labeled corpus,
where everything in CV / train fits with 100% confidence. Two
interpretations, both compatible:

- The labeled corpus is still **narrower than the real inbox**.
- The model is **calibrated honestly**: it knows it doesn't know.

Either way, every email with <60% confidence (currently 4) is the
single best thing to label next — they sit closest to the decision
boundary and carry the most information per label.

##### Next steps

1. **Relabel the 2 misclassified test emails** ("Issue with items
   supplied under PO-5312", "Request for quotation - toner
   cartridges"). Once they're in the train pool, the model will learn
   that PO-mention ≠ PO.
2. **Label the 4 lowest-confidence unseen emails** (all <55% confident).
   These directly inform the next CV round.
3. **Watch CV drop below 100%** as the hard examples propagate from
   "newest" into "oldest" through subsequent training rounds — that's
   the signal that the train pool itself is no longer trivial.

Live numbers always at `models/classifier/metadata.json` and on the
Classifier page in the UI.

### Field extraction from email body (regex)
- **PO number** — three patterns, tried in order:
  1. MEL-style (no label needed): 2–4 letter prefix + 4-digit year + `PO` +
     digits, e.g. `MEL2025PO12345`, `AB1234PO99999`.
  2. Labeled long-form: `Purchase Order Number: …`, `Purchase Order #: …`,
     `Purchase Order No.: …`.
  3. Labeled short-form: `PO#: …`, `PO Number: …`, `PO No: …`
     (also tolerates `P0` typos).
- **Supplier** — from labeled lines (`Supplier:`, `Vendor:`, `Seller:`,
  `Manufacturer:`, `Company:`) **or** as a fallback from the last 8 lines
  of the email by detecting company-suffix signatures
  (`LTD`, `LIMITED`, `LLC`, `INC`, `CORP`, `CO.`, `PVT LTD`).
  Sign-off phrases (`Thanks`, `Regards`, …) are stripped; URLs and email
  addresses are rejected.
- **Date** — recognizes **14 date formats** spanning numeric and verbose
  variants (`dd/mm/yyyy`, `dd-mm-yyyy`, `dd.mm.yyyy`, `mm/dd/yyyy`,
  `yyyy-mm-dd`, `12 May 2024`, `May 12, 2024`, 2-digit years, …); first
  checks for labels (`Date:`, `Delivery Date:`, `Due Date:`, `Order Date:`,
  `PO Date:`, `Ship Date:`) then falls back to a free-form match. All
  outputs normalized to `YYYY-MM-DD`.
- **Amount** — currency-aware (`$`, `£`, `€`); anchored on the labels
  `Grand Total`, `Sub-Total`, `Invoice Total`, `Total`, `Amount`, `Due`,
  `Balance`. Strips internal whitespace, keeps thousands separators and
  up to 2 decimal places.
- **Item codes** — alphanumeric pattern (`[A-Z]{2,4}\d+[A-Z]?\d*-[A-Z]?\d+`),
  e.g. `ABC2024-X12`, `MEL12-A99`. Deduplicated (order-preserving), capped
  at 20 per email to keep noisy footers from blowing up the UI.
- Each field stores a **provenance tag** (`body:regex`, `attachment:ocr`,
  …) so the UI can show where every value came from. When the same field
  is found in both the body and an attachment, the attachment OCR value
  wins (see [extraction/merge.py](extraction/merge.py)).

### Structured PO table extraction (BeautifulSoup)
- Parses every HTML `<table>` in the email body into rows of a fixed
  **13-column MASTER schema**:
  - **Text columns (3):** `Type`, `Contract No`, `Item Category`.
  - **Size columns (10):** `5lb`, `First Size`, `Up To 1Mth`, `Up To 3Mth`,
    `3-6 Mths`, `6-9 Mths`, `9-12 Mths`, `12-18 Mths`, `1.5-2 Yrs`, `Total`.
- **Header alias map** — each canonical size column has 4–6 known aliases
  (`up to 1 month` ↔ `Up To 1Mth`, `3 to 6 mths` ↔ `3-6 Mths`, …) so real
  emails with inconsistent header spelling still map cleanly.
- **Header row detection** — scores the first 8 rows by how many cells look
  like size headers; requires ≥3 recognizable size columns before treating
  a table as a PO table (skips signature/layout tables).
- **Implicit Total column** — if no header maps to `Total`, the rightmost
  unmapped column is auto-promoted to `Total` when ≥60% of its data rows
  are numeric.
- **Contract No** — regex `[A-Z]{2}\d{6,}` matched anywhere in the table
  (handles `VA`, `VJ`, `VQ`, `VB`, and any 2-letter prefix).
- **Item Category** — pattern-matched against a known set
  (`7%`, `PRICE TICKET`, `CARTON STICKER`, `LAMINATING`, `POS`, `Base Qty`);
  unlabeled rows with only numeric values are kept as `Base Qty` rather
  than dropped.
- **Type** — auto-detects `Online` / `Retail` from any cell in the table.
- Handles both screenshot styles seen in real POs: per-contract size tables
  and combined Online/Retail tables.
- Auto-fetched (per PO email) only when the Extraction page is open, to keep
  the regular inbox fast.

### Image attachment OCR (Tesseract)
- Per-email **OCR image attachments** button on the Extraction page.
- Downloads images via Graph, runs Tesseract, then re-runs the body regex on
  the OCR text — same extracted-field schema, same five fields.
- **Supported formats:** `.png`, `.jpg/.jpeg`, `.gif`, `.webp`, `.bmp`,
  `.tif/.tiff` (plus the matching MIME types from Graph).
- **OCR → regex pipeline:** image bytes → `pytesseract.image_to_string` →
  same `extract_from_body` regex used on plain-text bodies, so OCR'd POs
  surface PO number, supplier, date, amount, and item codes too.
- **Field merging:** when both the email body and an image attachment
  yield the same field, the **attachment OCR value wins** (images are
  usually the canonical PO document, the body is the cover note).
- Auto-detects the Tesseract binary in this order:
  `TESSERACT_CMD` env var → project-local `./tessaret/` or `./tesseract/`
  → `C:\Program Files\Tesseract-OCR\` → `C:\Program Files (x86)\Tesseract-OCR\`
  → `%LOCALAPPDATA%\Programs\Tesseract-OCR\` → system PATH.

---

## Tech stack

| Layer | Library | Why |
|---|---|---|
| Backend API | **FastAPI** + **uvicorn** | Async, typed, auto OpenAPI docs |
| HTTP client | **httpx** (async) | Graph calls + OAuth token exchange |
| Settings | **pydantic-settings** | `.env`-driven config with validation |
| Frontend | **Streamlit** | Fast UI iteration; one file per page |
| Classifier | **scikit-learn** (`TfidfVectorizer`, `LogisticRegression`) | Light, accurate on small hand-labeled data |
| Model I/O | **joblib** | Pipeline serialization |
| HTML parsing | **BeautifulSoup4** (`html.parser`) | Pure-Python, no binary deps |
| OCR | **pytesseract** + **Pillow** | Industry-standard image OCR |
| Microsoft Graph | Direct REST | OAuth2 + mail + attachments |

Scaffolded but not active yet: **SQLAlchemy**, **alembic**, **redis**, **rq**,
**cryptography** (token encryption), **psycopg2-binary** — the codebase
includes models, repositories, and worker queues for a future database-backed
mode.

---

## Architecture

```
                Browser
                   |
                   v  HTTP (http://127.0.0.1:8501)
              +-----------+
              | Streamlit |   UI only — no business logic
              +-----------+
                   |
                   |  httpx.Client (http://127.0.0.1:8000)
                   v
+--------------------------------------+
| FastAPI (uvicorn)                    |
|                                      |
|  /auth/login    /classifier/labels   |
|  /auth/callback /classifier/train    |
|  /inbox         /classifier/status   |
|                 /extraction/email/.. |
+--------------------------------------+
        |              |              |
        v              v              v
   Microsoft       classifier/    extraction/
   Graph API       (ml model)     (regex + BS4 + OCR)
```

- The frontend never imports business logic — only calls the API.
- The API is stateless except for the in-memory token store.
- Each request to `/inbox` triggers (in order): Graph list → classify each
  message (if model exists) → run extraction (if predicted PO) → return JSON.

---

## Project layout

```
apps/
  api/
    main.py              # FastAPI app + router mounts
    token_store.py       # In-memory OAuth token (single session)
    routes/
      auth.py            # OAuth login + callback
      inbox.py           # Live inbox; classify + extract + table parse
      classifier.py      # Labels, training, status, predict, reset
      extraction.py      # Attachment OCR
      health.py
      emails.py          # (dormant — DB-backed views)
      sync.py            # (dormant — RQ enqueue)
  streamlit_app/
    main.py              # Sidebar nav + API client
    pages/
      inbox.py           # View emails + predictions
      classifier.py      # Label + train + reset
      extraction.py      # PO fields + tables + OCR
      settings.py        # Read-only config view

classifier/
  dataset.py             # JSONL label store
  features.py            # Subject+body text combiner
  loader.py              # joblib load + metadata read
  predict.py             # Inference
  service.py             # ClassifierService orchestration
  train.py               # TF-IDF + LogReg, 80/20 stratified split

extraction/
  body.py                # Regex: PO number, supplier, date, amount, items
  tables.py              # BS4 parser + MASTER schema
  ocr.py                 # pytesseract wrapper, auto-detect binary
  merge.py               # Combine body + attachment fields
  service.py             # ExtractionService orchestration
  rules/                 # (reserved for custom rule files)

integrations/
  graph_client.py        # OAuth code flow + Graph REST (list/get/attach)
  auth.py                # Token encryption helpers (dormant)
  __init__.py

config/
  settings.py            # pydantic-settings (.env-driven)

domain/
  schemas.py             # ClassificationResult, ExtractionResult, ...
  pipeline.py            # Combined orchestration (used by workers)

storage/                 # SQLAlchemy models + repositories (dormant)
workers/                 # RQ jobs (dormant)

run.ps1                  # Launches API + Streamlit in two terminals
docker-compose.yml       # Postgres + Redis + API + workers (dormant)
pyproject.toml           # Dependencies + ruff config
```

Two architectural rules followed throughout:
1. **Classifier and extraction are separate packages.** Neither imports the
   other. There is no shared `ml/` package.
2. **The UI calls only the API.** Streamlit never imports business modules
   directly; it goes through HTTP.

---

## Setup

### 1. Python environment

Requires **Python 3.11+**.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 2. Azure AD app registration (one-time)

In the [Azure portal](https://portal.azure.com) → **Microsoft Entra ID** →
**App registrations** → **New registration**:

- Redirect URI (Web): `http://localhost:8000/auth/callback`
- Supported account types: **"…any organizational directory and personal
  Microsoft accounts"** (required for `consumers` endpoint).
- Under **Certificates & secrets** → create a client secret.
- Under **API permissions** → add `Microsoft Graph > Delegated > Mail.Read`
  and `User.Read`.

Copy the **Application (client) ID** and the secret **Value**.

### 3. Configure `.env`

```powershell
copy .env.example .env
```

Fill in the Azure values you copied. The full set of variables:

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | Environment marker |
| `API_BASE_URL` | `http://127.0.0.1:8000` | Where the Streamlit frontend reaches the API |
| `FRONTEND_BASE_URL` | `http://127.0.0.1:8501` | Where `/auth/callback` redirects after sign-in |
| `AZURE_TENANT_ID` | `consumers` | `consumers` = personal accounts only; `common` = any account |
| `AZURE_CLIENT_ID` | *(required)* | Application (client) ID |
| `AZURE_CLIENT_SECRET` | *(required)* | Client secret value |
| `GRAPH_REDIRECT_URI` | `http://localhost:8000/auth/callback` | Must match Azure registration |
| `GRAPH_SCOPES` | `https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access` | Delegated permissions requested |
| `TOKEN_ENCRYPTION_KEY` | generated | Fernet key for refresh-token encryption (DB mode) |
| `TESSERACT_CMD` | *(blank)* | Path to `tesseract.exe`; auto-detected if blank |
| `CLASSIFIER_MODEL_PATH` | `./models/classifier` | Where `model.joblib` is saved |
| `CLASSIFIER_LABELS_PATH` | `./data/labels.jsonl` | Where labels are stored |
| `OCR_ENABLED` | `true` | Toggle attachment OCR |

### 4. Install Tesseract OCR (optional, only for image attachments)

Easiest via winget:
```powershell
winget install --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
```

Or download the installer from
<https://github.com/UB-Mannheim/tesseract/wiki>. The default install path
`C:\Program Files\Tesseract-OCR\tesseract.exe` is auto-detected.

---

## Run

```powershell
.\run.ps1
```

Opens two PowerShell windows — one running `uvicorn` on port 8000, one
running `streamlit` on port 8501. Then open
<http://127.0.0.1:8501> in your browser.

Stop with `Ctrl+C` in each window.

To run them individually:
```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload
.\.venv\Scripts\python.exe -m streamlit run apps/streamlit_app/main.py --server.port 8501 --server.address 127.0.0.1
```

---

## Typical workflow

1. **Inbox tab** → **Connect Outlook** → sign in → land back on the inbox
   with your messages.
2. **Classifier tab** → label emails as PO / Not PO (≥3 of each) →
   **Train model**.
3. **Inbox tab** → predictions now show as green PO / grey Not-PO badges.
4. **Extraction tab** → table view of every PO-classified email with PO
   number, supplier, date, amount, items, plus per-email expanders for body
   fields, parsed PO tables, and image OCR.
5. Re-label any wrong predictions on the Classifier tab and re-train —
   accuracy improves with each iteration.

---

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/auth/login` | Redirects to Microsoft sign-in |
| GET | `/auth/callback` | OAuth code exchange; redirects to Streamlit |
| GET | `/inbox?top=N&include_tables=bool` | List messages with predictions + (optional) parsed tables |
| POST | `/classifier/labels` | Save a label `{email_id, subject, body_text, label}` |
| GET | `/classifier/status` | Label counts + trained model metadata |
| POST | `/classifier/train` | Train on stored labels (raises 422 if too few) |
| POST | `/classifier/predict` | Single-email prediction `{subject, body_text}` |
| DELETE | `/classifier/labels` | Delete every stored label |
| DELETE | `/classifier/model` | Delete the trained model artifact |
| POST | `/extraction/email/{id}/attachments` | Fetch + OCR all image attachments |

OpenAPI docs are served at <http://127.0.0.1:8000/docs>.

---

## Current limitations

- **In-memory token** — restarting the API loses the OAuth session;
  re-sign-in is required. Refresh tokens are not persisted.
- **Personal accounts only** by default (`AZURE_TENANT_ID=consumers`).
  Switch to `common` and update the Azure app's *supported account types* to
  also accept work/school accounts.
- **No PDF attachment extraction** yet — only image attachments are OCR'd.
- **No CSV export** — extracted tables are shown in-app only.
- The **storage**, **workers**, and **emails** modules are scaffolded for a
  database-backed mode but are not currently used at runtime.

---

## Project rules

- **Classifier and extraction are kept in separate packages** with no shared
  `ml/` package and no cross-imports.
- **Streamlit pages call only the API** — no business logic in the UI.
- **No code is committed for hypothetical use cases.** Dormant modules are
  marked as such in the layout above.
