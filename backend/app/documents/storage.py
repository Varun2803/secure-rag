import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile

from app.config import get_settings


class UploadTooLargeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredUpload:
    path: Path
    storage_key: str
    size: int
    sha256: str


def storage_root() -> Path:
    root = Path(get_settings().document_storage_path).expanduser().resolve()
    application_dir = Path(__file__).resolve().parents[1]
    if root == application_dir or application_dir in root.parents:
        raise ValueError("Document storage must be outside the application directory")
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


async def store_upload(upload: UploadFile) -> StoredUpload:
    root = storage_root()
    storage_key = str(uuid4())
    target = root / f"{storage_key}.pdf"
    digest = hashlib.sha256()
    size = 0
    fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=root)
    try:
        with os.fdopen(fd, "wb") as output:
            while data := await upload.read(1024 * 1024):
                size += len(data)
                if size > get_settings().max_upload_size_bytes:
                    raise UploadTooLargeError("The uploaded file exceeds the size limit")
                digest.update(data)
                output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise ValueError("The uploaded file is empty")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
        return StoredUpload(target, storage_key, size, digest.hexdigest())
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
