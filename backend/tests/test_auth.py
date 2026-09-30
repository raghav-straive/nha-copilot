"""Password hashing and token handling."""
import pytest

from app.auth import users as u
from app.auth.jwt import create_token, decode_token


def test_correct_password_verifies():
    h = u._hash("correct horse battery staple")
    assert u._verify("correct horse battery staple", h)


def test_wrong_password_rejected():
    h = u._hash("right")
    assert not u._verify("wrong", h)


def test_long_passwords_are_not_truncated_to_the_same_hash():
    """bcrypt ignores bytes past 72, so two long passwords sharing a 72-byte
    prefix used to collapse into the same credential."""
    a = "x" * 72 + "ending-A"
    b = "x" * 72 + "ending-B"
    h = u._hash(a)
    assert u._verify(a, h)
    assert not u._verify(b, h), "a 73rd-byte difference must still matter"


def test_multibyte_passwords_work():
    # Slicing raw UTF-8 at [:72] could cut a character in half; hashing first
    # means the byte length of the input no longer matters.
    pw = "पासवर्ड-हिंदी-" * 10
    h = u._hash(pw)
    assert u._verify(pw, h)
    assert not u._verify(pw + "x", h)


def test_emoji_password_round_trips():
    pw = "🔐" * 40
    assert u._verify(pw, u._hash(pw))


def test_same_password_gets_different_hashes():
    # Distinct salts, so identical passwords don't look identical at rest.
    assert u._hash("same") != u._hash("same")


def test_empty_password_still_hashes_and_verifies():
    h = u._hash("")
    assert u._verify("", h)
    assert not u._verify(" ", h)


def test_authenticate_accepts_a_seed_user_and_rejects_a_bad_one():
    # Seed users are active because APP_USERS is unset in the test env.
    assert u.authenticate("analyst", "analyst123") is not None
    assert u.authenticate("analyst", "nope") is None
    assert u.authenticate("nobody", "anything") is None


def test_parse_app_users_skips_malformed_entries():
    parsed = u._parse_app_users(
        "good:pw1:admin;missing-role:pw2;bad:pw3:not_a_role;;spaced :pw4: viewer "
    )
    names = [p[0] for p in parsed]
    assert "good" in names
    assert "missing-role" not in names
    assert "bad" not in names, "an unknown role must be rejected, not defaulted"


def test_token_round_trips_username_and_role():
    payload = decode_token(create_token("alice", "senior_analyst"))
    assert payload["sub"] == "alice"
    assert payload["role"] == "senior_analyst"
    assert "exp" in payload


def test_token_signed_with_another_secret_is_rejected():
    from jose import jwt as jose_jwt, JWTError

    from app.config import get_settings

    forged = jose_jwt.encode(
        {"sub": "attacker", "role": "admin"}, "a-different-secret", algorithm="HS256"
    )
    with pytest.raises(JWTError):
        decode_token(forged)
    # And the real secret still works, so the test is meaningful.
    assert decode_token(create_token("bob", "viewer"))["sub"] == "bob"
    assert get_settings().jwt_algorithm == "HS256"
