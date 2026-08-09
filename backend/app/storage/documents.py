"""Document storage.

Local filesystem only, on purpose. S3 was here briefly; it turned "run this
app" into "create a bucket and an IAM user first" in exchange for durability
this workload does not need — the documents are re-uploadable by the vendor
and the case record, which is what decisions are audited against, lives in the
database.

The Protocol is kept so a deployment that genuinely needs object storage can
add a provider without touching any caller.
"""

from pathlib import Path
from typing import Protocol

from backend.app import config


class StorageProvider(Protocol):
    def save(self, path: str, content: bytes) -> str:
        """Save bytes to a path and return the logical path."""
        ...

    def read(self, path: str) -> bytes:
        """Read bytes from a given logical path."""
        ...


class LocalDocumentStorage:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def save(self, path: str, content: bytes) -> str:
        dest = self.base_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return path

    def read(self, path: str) -> bytes:
        full_path = self.base_dir / path
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return full_path.read_bytes()


def get_storage() -> StorageProvider:
    return LocalDocumentStorage(config.DATA_DIR / "documents")
