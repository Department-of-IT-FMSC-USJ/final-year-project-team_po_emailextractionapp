# PO Email Intelligence

Outlook inbox integration with **separate** classification and extraction pipelines, Streamlit UI, and background workers.

## Layout

```
apps/streamlit_app/   # UI only
apps/api/             # FastAPI
workers/              # sync + process jobs
integrations/         # Microsoft Graph
classifier/           # trained classification model (standalone)
extraction/           # body + attachment extraction (standalone)
domain/               # schemas and orchestration
storage/              # DB and blob
config/               # settings
```

Classifier and extraction are **not** combined under a shared `ml/` package.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

Run services (see `docker-compose.yml` when ready).
