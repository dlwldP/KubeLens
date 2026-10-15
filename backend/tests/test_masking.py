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


# --- 보강된 패턴 -------------------------------------------------------------

PEM = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun
VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK
-----END RSA PRIVATE KEY-----"""


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (PEM, "MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun"),
        ("DATABASE_URL=postgres://app:s3cr3t-pw@db:5432/orders", "s3cr3t-pw"),
        ("connecting to redis://default:hunter2@cache:6379", "hunter2"),
        ("Authorization: Basic YWRtaW46cGFzc3dvcmQ=", "YWRtaW46cGFzc3dvcmQ="),
        ("GITHUB_TOKEN ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnop"),
        ("key sk-ant-api03-abcdefghijklmnop used", "sk-ant-api03-abcdefghijklmnop"),
        ("slack xoxb-1234567890-abcdefghij", "xoxb-1234567890-abcdefghij"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "wJalrXUtnFEMI/K7MDENG"),
        ("credentials: c0nf1dential", "c0nf1dential"),
        ("private_key=abc123def456", "abc123def456"),
    ],
)
def test_extended_secret_patterns(raw: str, secret: str) -> None:
    out = mask(raw)
    assert secret not in out
    assert "***" in out


def test_pem_truncated_by_log_tail_is_still_masked() -> None:
    cut = PEM.rsplit("\n", 1)[0]  # END 줄이 잘린 경우
    out = mask("before\n" + cut)
    assert "MIIEowIBAAKCAQEA" not in out
    assert out.startswith("before\n-----BEGIN RSA PRIVATE KEY----- ***")


def test_url_credential_keeps_user_and_host() -> None:
    assert mask("postgres://app:pw123@db:5432/x") == "postgres://app:***@db:5432/x"


@pytest.mark.parametrize(
    "text",
    [
        "Image: nginx@sha256:0d17b565c37bcbd895e9d92315a05c1c3c9a29f762b011a10c54a66cd53c9b31",
        "https://registry-1.docker.io/v2/library/nginx/manifests/9.99.99",
        "Basic auth disabled",
        "secretName: db-credentials-ref",
        "Liveness probe failed: HTTP probe failed with statuscode: 500",
    ],
)
def test_extended_patterns_do_not_mask_ordinary_text(text: str) -> None:
    assert mask(text) == text


# --- 속성 기반 테스트: 무작위 비밀값이 어떤 형식으로 들어가도 출력에 남지 않아야 한다 ----

from hypothesis import assume, given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

SECRET_CHARS = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+!"),
    min_size=8,
    max_size=40,
)
TEMPLATES = [
    "password={s}",
    "DB_PASSWORD: {s}",
    '{{"api_key": "{s}"}}',
    "access_token={s} expires_in=3600",
    "Authorization: Bearer {s}",
    "mysql://root:{s}@mysql:3306/app",
    "level=info msg=login secret={s} user=bob",
]


@given(secret=SECRET_CHARS, template=st.sampled_from(TEMPLATES), prefix=st.text(max_size=30))
def test_random_secrets_never_survive(secret: str, template: str, prefix: str) -> None:
    line = template.format(s=secret)
    # 비밀값이 템플릿이나 앞 문자열의 일부로 우연히 다시 나타나는 경우는 제외
    assume(secret not in template and secret not in prefix and "***" not in secret)
    out = mask(prefix + "\n" + line)
    assert secret not in out


@given(st.text(max_size=200))
def test_mask_is_idempotent(text: str) -> None:
    once = mask(text)
    assert mask(once) == once
