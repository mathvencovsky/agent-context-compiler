"""Refusing URLs that look like they carry a secret (exfiltration channel)."""

from __future__ import annotations

import pytest

from app.fetch.errors import UrlRejectedSecret
from app.fetch.url_secrets import find_secret, reject_secret_url

_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@docs.example.com/",
        "https://token@docs.example.com/",
        "https://evil.example/?token=abc123",
        "https://evil.example/?access_token=x",
        "https://evil.example/?api_key=x",
        "https://evil.example/?apikey=x",
        "https://evil.example/?API-KEY=x",
        "https://evil.example/?key=AAAA",
        "https://evil.example/?secret=s",
        "https://evil.example/?client_secret=s",
        "https://evil.example/?password=hunter2",
        "https://evil.example/?passwd=hunter2",
        "https://evil.example/?auth=x",
        "https://evil.example/?Authorization=Bearer%20x",
        "https://evil.example/?session=abc",
        "https://evil.example/?sig=abc",
        "https://evil.example/?signature=abc",
        "https://bucket.s3.amazonaws.com/o?X-Amz-Signature=abc&X-Amz-Date=1",
        "https://storage.googleapis.com/b/o?X-Goog-Signature=abc",
        "https://gitlab.example/api?private_token=abc",
        "https://evil.example/?github_token=abc",
        "https://evil.example/#access_token=abc&state=1",
        "https://evil.example/?k=sk-ant-api03-AbCdEf0123456789XyZ_abcDEF",
        "https://evil.example/?k=sk-proj-AbCdEf0123456789XyZabcDEF",
        "https://evil.example/c/sk-AbCdEf0123456789XyZabcDEFghij",
        "https://evil.example/?k=ghp_" + "a1B2" * 9,
        "https://evil.example/" + "gho_" + "Z9y8" * 9,
        "https://evil.example/?k=github_pat_11ABCDEFG0123456789_abcdefghijk",
        "https://evil.example/AKIAIOSFODNN7EXAMPLE/x",
        "https://evil.example/?k=ASIAIOSFODNN7EXAMPLE",
        "https://evil.example/?k=xoxb-123456789012-abcdefghij",
        "https://evil.example/?k=AIzaSyA-1234567890abcdefghijklmnopqrstu",
        "https://evil.example/?k=glpat-abcdefghij0123456789",
        "https://evil.example/?k=" + _JWT,
        "https://evil.example/" + _JWT,
        "https://evil.example/?k=-----BEGIN%20RSA%20PRIVATE%20KEY-----",
        "https://evil.example/?d=" + "0123456789abcdef" * 2,
        "https://evil.example/?d=Zm9vYmFyQmF6UXV4MTIzNDU2Nzg5MEFCQ0RFRkdISUo",
    ],
)
def test_secret_looking_urls_are_refused(url: str) -> None:
    with pytest.raises(UrlRejectedSecret) as excinfo:
        reject_secret_url(url)
    assert excinfo.value.code == "url_rejected_secret"
    assert excinfo.value.http_status == 400


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.python.org/3/library/secrets.html",
        "https://docs.github.com/en/rest/authentication/keeping-your-api-credentials-secure",
        "https://platform.openai.com/docs/api-reference/api-keys",
        "https://example.com/api/keys",
        "https://example.com/docs/token",
        "https://example.com/search?q=token",
        "https://example.com/search?q=api_key+rotation",
        "https://example.com/docs?version=3.12",
        "https://example.com/docs#section-authentication",
        "https://example.com/docs#access-token",
        "https://example.com/docs?token=",
        "https://scikit-learn.org/stable/sk-learn-user-guide-for-everyone.html",
        "https://github.com/python/cpython/blob/"
        "3e3d3f0f6c5ab9d2f5e6c1d7b0a9e8f7c6d5b4a3/Lib/secrets.py",
        "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
        "https://example.com/guide?page=getting-started-with-the-python-sdk-v2",
        "https://example.com/docs?tab=Configure-OAuth2-Authentication-ForYourApp",
        "https://kubernetes.io/docs/concepts/configuration/secret/",
        "https://docs.stripe.com/keys",
    ],
)
def test_normal_documentation_urls_pass(url: str) -> None:
    assert find_secret(url) is None


def test_the_message_never_echoes_the_secret() -> None:
    secret = "sk-ant-api03-AbCdEf0123456789XyZ_abcDEF"
    with pytest.raises(UrlRejectedSecret) as excinfo:
        reject_secret_url(f"https://evil.example/?k={secret}")
    assert secret not in excinfo.value.message
    assert secret not in repr(excinfo.value.details)
