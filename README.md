[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/n73txmTf)

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
