import hashlib
from pathlib import Path

from config.settings import settings


class BlobStorage:
    def __init__(self, base_path: Path | None = None) -> None:
        self._base = base_path or settings.blob_storage_path
        self._base.mkdir(parents=True, exist_ok=True)

    def save(self, email_id: str, filename: str, content: bytes) -> str:
        safe_name = Path(filename).name
        digest = hashlib.sha256(content).hexdigest()[:16]
        dest_dir = self._base / email_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{digest}_{safe_name}"
        dest.write_bytes(content)
        return str(dest)

    def path_for(self, email_id: str, filename: str) -> Path:
        return self._base / email_id / Path(filename).name
