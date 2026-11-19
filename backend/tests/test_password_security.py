import pytest

from app.auth.security import hash_password, validate_password, verify_password


@pytest.mark.parametrize("password", ["", "           ", "short", "x" * 129])
def test_rejects_invalid_password_length_or_content(password: str) -> None:
    with pytest.raises(ValueError):
        validate_password(password)


def test_argon2_hash_verifies_without_storing_plaintext() -> None:
    password = "SecurePassword123!"
    hashed = hash_password(password)
    assert hashed != password
    assert hashed.startswith("$argon2id$")
    assert verify_password(password, hashed)
    assert not verify_password("incorrect password", hashed)


def test_unknown_or_malformed_hash_fails_safely() -> None:
    assert not verify_password("SecurePassword123!", None)
    assert not verify_password("SecurePassword123!", "not-a-valid-hash")
