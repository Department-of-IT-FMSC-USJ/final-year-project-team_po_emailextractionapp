FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY apps ./apps
COPY workers ./workers
COPY integrations ./integrations
COPY classifier ./classifier
COPY extraction ./extraction
COPY domain ./domain
COPY storage ./storage
COPY config ./config

RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
