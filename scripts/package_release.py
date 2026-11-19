"""Build and validate a source-only SecureRAG ZIP archive.

This intentionally uses an allowlist; ignore files alone are not a ZIP safety
boundary. Secret values are never printed by this script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "SecureRAG"
DEFAULT_OUTPUT = ROOT / "dist" / "SecureRAG-phase9.zip"

ROOT_FILES = (
    ".github/workflows/tests.yml",
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "README.md",
    "SECURITY_ASSESSMENT.md",
    "alembic.ini",
    "docker-compose.yml",
    "pytest.ini",
)
SOURCE_DIRS = ("backend/app", "backend/alembic", "backend/tests")
ADDITIONAL_FILES = ("backend/Dockerfile", "backend/requirements.txt", "scripts/package_release.py")
SECRET_ENV_KEYS = {
    "DATABASE_URL",
    "DEV_SEED_PASSWORD",
    "JWT_SECRET",
    "JWT_SIGNING_KEY",
    "ACCESS_TOKEN_SECRET",
    "REFRESH_TOKEN_SECRET",
    "LLM_API_KEY",
    "POSTGRES_PASSWORD",
    "QDRANT_API_KEY",
    "SECRET_KEY",
}
PLACEHOLDER_MARKERS = ("replace-with-", "change-me", "changeme", "your_", "your-", "${", "<")
PRIVATE_KEY_MARKERS = tuple(
    ("-----BEGIN " + key + "-----").encode("ascii")
    for key in ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY", "DSA PRIVATE KEY")
)


def _safe_example_environment(content: str) -> None:
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip().strip("\"'")
        is_sensitive = (
            key in SECRET_ENV_KEYS
            or any(part in key for part in ("PASSWORD", "API_KEY", "SECRET", "CREDENTIAL", "PRIVATE_KEY"))
            or ("JWT" in key and any(part in key for part in ("SECRET", "SIGNING", "KEY")))
            or ("TOKEN" in key and key.endswith(("_TOKEN", "_TOKEN_KEY")))
        )
        if not is_sensitive:
            continue
        if key == "DATABASE_URL" and value:
            raise ValueError(".env.example must leave DATABASE_URL blank")
        folded = value.casefold()
        if value and folded not in {"placeholder", "example"} and not any(
            marker in folded for marker in PLACEHOLDER_MARKERS
        ):
            raise ValueError(".env.example contains a non-placeholder secret setting")


def _prohibited_archive_path(path: PurePosixPath) -> bool:
    parts = [part.casefold() for part in path.parts]
    name = parts[-1] if parts else ""
    if any(part in {"__pycache__", ".pytest_cache", ".venv", "venv", "env", "node_modules"} for part in parts):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name in {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"}:
        return True
    if name.startswith(("credentials", "secrets")):
        return True
    if path.suffix.casefold() in {
        ".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".ppk", ".tmp", ".temp", ".log", ".bak", ".swp",
    }:
        return True
    return False


def _source_files() -> list[Path]:
    paths = [ROOT / name for name in ROOT_FILES + ADDITIONAL_FILES]
    for directory in SOURCE_DIRS:
        base = ROOT / directory
        paths.extend(path for path in base.rglob("*") if path.is_file())
    result: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Required release file is missing: {path.relative_to(ROOT).as_posix()}")
        relative = PurePosixPath(path.relative_to(ROOT).as_posix())
        if _prohibited_archive_path(relative):
            continue
        if path.is_symlink():
            raise ValueError(f"Release source contains a symlink: {relative.as_posix()}")
        result.append(path)
    return sorted(set(result), key=lambda item: item.relative_to(ROOT).as_posix().casefold())


def validate_archive(archive_path: Path) -> int:
    try:
        with ZipFile(archive_path) as archive:
            damaged = archive.testzip()
            if damaged is not None:
                raise ValueError("Archive integrity check failed")
            names = [PurePosixPath(name) for name in archive.namelist()]
            for name in names:
                if name.is_absolute() or ".." in name.parts:
                    raise ValueError("Archive contains an unsafe path")
                if _prohibited_archive_path(name):
                    raise ValueError("Archive contains a prohibited secret or generated file")
            required = {PurePosixPath(f"{ARCHIVE_ROOT}/{filename}") for filename in ROOT_FILES}
            if not required.issubset(set(names)):
                raise ValueError("Archive is missing required project files")
            env_example = archive.read(f"{ARCHIVE_ROOT}/.env.example").decode("utf-8")
            _safe_example_environment(env_example)
            for name in names:
                if name.suffix.casefold() == ".zip":
                    continue
                content = archive.read(name.as_posix())
                if any(marker in content for marker in PRIVATE_KEY_MARKERS):
                    raise ValueError("Archive contains private-key material")
            return len(names)
    except BadZipFile as exc:
        raise ValueError("Invalid ZIP archive") from exc


def build_archive(output: Path) -> int:
    output = output.resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("Release ZIP must be written inside the project dist directory") from exc
    if output.parent != ROOT / "dist":
        raise ValueError("Release ZIP must be written directly into dist/")

    _safe_example_environment((ROOT / ".env.example").read_text(encoding="utf-8"))
    paths = _source_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            archive.write(path, f"{ARCHIVE_ROOT}/{relative}")
    return validate_archive(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate", type=Path, help="Validate an existing ZIP without creating it")
    args = parser.parse_args()
    try:
        count = validate_archive(args.validate) if args.validate else build_archive(args.output)
    except (OSError, ValueError) as exc:
        print(f"Archive validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Archive validation passed: {count} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
