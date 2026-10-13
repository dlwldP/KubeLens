import pytest

from kubelens.tools import mask


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer abc.def-123", "abc.def-123"),
        ("api_key=kl-fake-0000000000000000", "kl-fake-0000000000000000"),
        ("API-KEY: sk-live-xyz", "sk-live-xyz"),
        ("apikey=zzz999", "zzz999"),
        ("password=hunter2", "hunter2"),
        ("DB_PASSWORD: s3cr3t!", "s3cr3t!"),
        ("access_token=tok_123", "tok_123"),
        ("client_secret = shh", "shh"),
        ('{"password": "p@ss", "user": "bob"}', "p@ss"),
        ("key AKIAIOSFODNN7EXAMPLE used", "AKIAIOSFODNN7EXAMPLE"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFU",
            "eyJzdWIiOiIxMjM0In0",
        ),
    ],
)
def test_secret_values_are_masked(raw: str, secret: str) -> None:
    out = mask(raw)
    assert secret not in out
    assert "***" in out


def test_key_names_are_kept_for_context() -> None:
    assert mask("api_key=kl-fake-0000000000000000") == "api_key=***"
    assert mask('"password": "p@ss"') == '"password": "***"'
    assert mask("Authorization: Bearer abc") == "Authorization: Bearer ***"


def test_s1_log_line_masked_but_diagnosis_kept() -> None:
    log = (
        "DEBUG loaded config api_key=kl-fake-0000000000000000\n"
        "FATAL required environment variable DB_HOST is not set\n"
    )
    out = mask(log)
    assert "kl-fake-0000000000000000" not in out
    assert "FATAL required environment variable DB_HOST is not set" in out


@pytest.mark.parametrize(
    "text",
    [
        "Back-off restarting failed container api",
        "0/2 nodes are available: 2 Insufficient memory.",
        'configmap "order-service-config" not found',
        "token bucket refilled",  # 구분자(=, :)가 없으면 건드리지 않는다
        "exit code 1",
    ],
)
def test_ordinary_text_untouched(text: str) -> None:
    assert mask(text) == text
