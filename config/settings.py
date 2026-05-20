from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    api_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://127.0.0.1:8501"

    database_url: str = "postgresql://postgres:postgres@localhost:5432/po_email"
    redis_url: str = "redis://localhost:6379/0"
    blob_storage_path: Path = Path("./data/attachments")

    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    graph_redirect_uri: str = "http://localhost:8000/auth/callback"
    graph_scopes: str = "Mail.Read offline_access"

    token_encryption_key: str = ""

    classifier_model_path: Path = Path("./models/classifier")
    classifier_model_version: str = "1.0.0"

    extraction_rules_path: Path = Path("./extraction/rules")
    extractor_version: str = "1.0.0"
    ocr_enabled: bool = True


settings = Settings()
