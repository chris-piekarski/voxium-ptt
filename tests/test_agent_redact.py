"""Tests for voxium.agent_redact (log redaction)."""

from voxium.agent_redact import (
    _looks_like_token,
    redact_lines,
    redact_log_line,
)

# ---------------------------------------------------------------------------
# Bearer / token redaction
# ---------------------------------------------------------------------------


def test_redact_bearer_header():
    line = "Auth check: Authorization: Bearer abc123def456"
    result = redact_log_line(line)
    assert "abc123def456" not in result
    assert "Authorization" not in result  # entire match replaced


def test_redact_long_token():
    token = "a" * 44  # 44 chars of base64url-ish
    line = f"frob {token} leaked"
    result = redact_log_line(line)
    assert token not in result


def test_redact_live_token_via_extra_tokens():
    live = "my_live_bearer_token_12345"
    line = f"token={live} used"
    result = redact_log_line(line, extra_tokens=[live])
    assert live not in result
    assert "<redacted>" in result


def test_no_redact_short_strings():
    line = "hello world 12345"
    assert redact_log_line(line) == line


# ---------------------------------------------------------------------------
# Path redaction
# ---------------------------------------------------------------------------


def test_redact_posix_path():
    line = "file at /home/user/secret/file.log"
    result = redact_log_line(line)
    assert "/home/user" not in result
    assert "<path>" in result


def test_redact_macos_path():
    line = "path /Users/alice/Documents/thing.txt"
    result = redact_log_line(line)
    assert "/Users/alice" not in result
    assert "<path>" in result


def test_redact_windows_path():
    line = "file C:\\Users\\bob\\thing.txt"
    result = redact_log_line(line)
    assert "C:\\Users\\bob" not in result
    assert "<path>" in result


def test_line_only_a_path_is_non_empty():
    line = "/home/user/somefile.txt"
    result = redact_log_line(line)
    assert result
    assert "<path>" in result
    assert "/home/user" not in result


# ---------------------------------------------------------------------------
# PEM block redaction
# ---------------------------------------------------------------------------


def test_redact_pem_block():
    # Realistic PEM block with base64 key data
    line = (
        "key -----BEGIN RSA PRIVATE KEY----- "
        "ABCDEFMIQKBhDgEAAAA "
        "-----END RSA PRIVATE KEY----- done"
    )
    result = redact_log_line(line)
    assert "BEGIN" not in result
    assert "ABCDEFMIQKBhDgEAAAA" not in result
    assert "<redacted>" in result
    # "PRIVATE KEY" after END is a label, not sensitive data
    # so we only check the key DATA is redacted


# ---------------------------------------------------------------------------
# Lines iterator with cap
# ---------------------------------------------------------------------------


def test_redact_lines_cap():
    lines = [f"line_{i}" for i in range(10)]
    redacted, truncated = redact_lines(lines, max_lines=3)
    assert len(redacted) == 3
    assert truncated is True


def test_redact_lines_no_truncation():
    lines = ["a", "b", "c"]
    redacted, truncated = redact_lines(lines, max_lines=10)
    assert len(redacted) == 3
    assert truncated is False


def test_redact_lines_no_cap():
    lines = ["a", "b"]
    redacted, truncated = redact_lines(lines)
    assert len(redacted) == 2
    assert truncated is False


# ---------------------------------------------------------------------------
# Extra tokens
# ---------------------------------------------------------------------------


def test_redact_lines_with_extra_tokens():
    secret = "super_secret_token_12345678901234567890"
    lines = [f"found {secret} here", "clean line"]
    redacted, _ = redact_lines(lines, extra_tokens=[secret])
    assert secret not in redacted[0]
    assert "<redacted>" in redacted[0]


# ---------------------------------------------------------------------------
# _looks_like_token heuristic
# ---------------------------------------------------------------------------


def test_looks_like_token_true():
    assert _looks_like_token("aB3dEfGhIjKlMnOpQrStUv" * 3) is True


def test_looks_like_token_false_with_slash():
    assert _looks_like_token("/home/user/aaa/111/bbb/222/ccc/333") is False


def test_looks_like_token_false_with_space():
    assert _looks_like_token("Hello World Foo Bar Baz Qux") is False
