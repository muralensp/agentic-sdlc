"""Secrets are removed before text goes into a handoff note (ADLC-022)."""

import pytest

from adlc.redact import REDACTED, redact


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    ],
)
def test_known_token_shapes_are_removed(secret):
    out = redact(f"it failed with {secret} in the log")
    assert secret not in out
    assert REDACTED in out
    assert out.startswith("it failed with")


def test_a_bearer_token_is_removed_but_the_word_stays():
    out = redact("Authorization: Bearer abc123def456ghi789")
    assert "abc123def456ghi789" not in out
    assert "Authorization" in out


@pytest.mark.parametrize("name", ["password", "PASSWORD", "api_key", "api-key", "secret", "token"])
def test_assigned_secrets_keep_the_name_and_lose_the_value(name):
    out = redact(f"{name}=hunter2value and more")
    assert "hunter2value" not in out
    assert name in out


def test_a_private_key_block_is_removed_whole():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\ndef\n-----END RSA PRIVATE KEY-----"
    out = redact(f"before\n{block}\nafter")
    assert "MIIEabc" not in out
    assert "before" in out
    assert "after" in out


def test_credentials_in_a_url_are_removed():
    out = redact("cloning https://user:s3cr3tpass@github.com/o/r.git now")
    assert "s3cr3tpass" not in out
    assert "github.com/o/r.git" in out


def test_ordinary_text_is_untouched():
    text = "Gate 2 failed: 3 tests red in src/app.py (the token count is 12)"
    assert redact(text) == text


def test_redacting_twice_changes_nothing_more():
    once = redact("password=hunter2value")
    assert redact(once) == once
