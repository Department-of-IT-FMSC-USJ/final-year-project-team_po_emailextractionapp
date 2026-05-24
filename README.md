[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/n73txmTf)

# PO Email Intelligence

Read Outlook inbox messages, learn which ones are Purchase Orders, and pull out
the structured fields — PO numbers, suppliers, dates, amounts, item codes, and
size-keyed contract tables — plus OCR text from image attachments.

The app is two processes: a **FastAPI** backend that handles OAuth, Microsoft
Graph calls, ML, and extraction, and a **Streamlit** frontend that calls the
API over HTTP. No database — sessions, labels, and models live in local files
or memory.

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
  fast on CPU, no GPU needed.
- **80/20 stratified train/test split** — reports test accuracy as the
  headline metric, plus train accuracy and sample counts.
- Re-label any email at any time (overwrites the previous label); re-train
  to update the model.
- Reset training data (labels and/or model) from the UI with a confirmation.
- Model artifact saved as `models/classifier/model.joblib` plus a
  `metadata.json` with training stats.

### Field extraction from email body (regex)
- **PO number** — recognizes MEL-style (`AB1234PO99999`) and labeled forms
  (`Purchase Order Number: ...`, `PO#: ...`).
- **Supplier** — from labeled lines (`Supplier:`, `Vendor:`) or by detecting
  company-suffix signatures (`LTD`, `LLC`, `INC`, `PVT LTD`).
- **Date** — parses 10+ formats including `dd/mm/yyyy`, `12 May 2024`,
  `May 12, 2024`; normalizes to `YYYY-MM-DD`.
- **Amount** — currency-aware, near labels like `Total`, `Amount`,
  `Grand Total`, `Invoice Total`, etc.
- **Item codes** — alphanumeric codes like `ABC2024-X12`, deduplicated and
  capped at 20.
- Each field stores a provenance tag (`body:regex`, `attachment:ocr`, ...).

### Structured PO table extraction (BeautifulSoup)
- Parses HTML tables in the email body into a fixed MASTER schema:
  `Type`, `Contract No`, `Item Category`, `5lb`, `First Size`, `Up To 1Mth`,
  `Up To 3Mth`, `3-6 Mths`, `6-9 Mths`, `9-12 Mths`, `12-18 Mths`,
  `1.5-2 Yrs`, `Total`.
- Handles both screenshot styles seen in real POs: per-contract size tables
  and combined Online/Retail tables.
- Detects implicit Total columns (trailing numeric column with no header).
- Auto-fetched (per PO email) only when the Extraction page is open, to keep
  the regular inbox fast.

### Image attachment OCR (Tesseract)
- Per-email **OCR image attachments** button on the Extraction page.
- Downloads images via Graph, runs Tesseract, then re-runs the body regex on
  the OCR text — same extracted-field schema.
- Auto-detects the Tesseract binary at `TESSERACT_CMD`, project-local
  `./tessaret/` or `./tesseract/`, `C:\Program Files\Tesseract-OCR\`, or PATH.

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
