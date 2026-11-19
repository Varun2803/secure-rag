from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts.package_release import ARCHIVE_ROOT, ROOT_FILES, validate_archive


def _make_archive(directory: Path, *, extra: tuple[str, bytes] | None = None, example: str = "SECRET_KEY=\n") -> Path:
    archive_path = directory / "fixture.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for filename in ROOT_FILES:
            content = example if filename == ".env.example" else "fixture\n"
            archive.writestr(f"{ARCHIVE_ROOT}/{filename}", content)
        if extra is not None:
            archive.writestr(extra[0], extra[1])
    return archive_path


@pytest.mark.parametrize(
    "entry",
    [
        "SecureRAG/.env",
        "SecureRAG/.env.production",
        "SecureRAG/backend/app/__pycache__/module.pyc",
        "SecureRAG/backend/tests/.pytest_cache/v/cache/nodeids",
        "SecureRAG/backend/app/.venv/pyvenv.cfg",
        "SecureRAG/id_rsa",
        "SecureRAG/private/key.pem",
        "SecureRAG/backend/local/credentials-prod.json",
        "SecureRAG/backend/app/temp/upload.tmp",
    ],
)
def test_archive_validator_rejects_prohibited_entries(tmp_path: Path, entry: str) -> None:
    archive = _make_archive(tmp_path, extra=(entry, b"not a real credential"))
    with pytest.raises(ValueError):
        validate_archive(archive)


def test_archive_validator_rejects_private_key_material(tmp_path: Path) -> None:
    archive = _make_archive(
        tmp_path,
        extra=("SecureRAG/notes.md", b"-----BEGIN " + b"PRIVATE KEY-----\nfake\n"),
    )
    with pytest.raises(ValueError, match="private-key"):
        validate_archive(archive)


def test_archive_validator_rejects_non_placeholder_environment_example(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, example="POSTGRES_PASSWORD=non-placeholder\n")
    with pytest.raises(ValueError, match="non-placeholder"):
        validate_archive(archive)


def test_archive_validator_rejects_non_placeholder_signing_key(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, example="JWT_SIGNING_KEY=non-placeholder\n")
    with pytest.raises(ValueError, match="non-placeholder"):
        validate_archive(archive)


def test_archive_validator_rejects_unsafe_paths(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path, extra=("SecureRAG/../outside.txt", b"x"))
    with pytest.raises(ValueError, match="unsafe path"):
        validate_archive(archive)


def test_archive_validator_accepts_clean_source_only_archive(tmp_path: Path) -> None:
    archive = _make_archive(tmp_path)
    assert validate_archive(archive) == len(ROOT_FILES)
