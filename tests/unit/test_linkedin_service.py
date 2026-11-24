import pytest
import requests

from backend.services.linkedin_service import (
    LinkedInAuthError,
    LinkedInService,
    LinkedInTransientError,
)


def test_publish_post_success_201(monkeypatch):
    svc = LinkedInService()

    class MockResponse:
        status_code = 201
        headers = {"x-restli-id": "urn:li:share:1234567890"}
        text = '{"id": "urn:li:share:1234567890"}'

        def json(self):
            return {"id": "urn:li:share:1234567890"}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    post_urn, meta = svc.publish_post(
        access_token="valid-token",
        author_urn="urn:li:person:sub123",
        content="Excited to announce our new system architecture!",
    )

    assert post_urn == "urn:li:share:1234567890"
    assert meta["status_code"] == 201


def test_publish_post_rate_limited_429(monkeypatch):
    svc = LinkedInService()

    class MockResponse:
        status_code = 429
        headers = {"Retry-After": "60"}
        text = "Too Many Requests"

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    with pytest.raises(LinkedInTransientError) as exc_info:
        svc.publish_post("token", "urn:li:person:sub123", "Content")

    assert exc_info.value.retry_after == 60
    assert exc_info.value.http_status == 429


def test_publish_post_server_error_500(monkeypatch):
    svc = LinkedInService()

    class MockResponse:
        status_code = 500
        headers = {}
        text = "Internal Server Error in LinkedIn REST Gateway"

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    with pytest.raises(LinkedInTransientError) as exc_info:
        svc.publish_post("token", "urn:li:person:sub123", "Content")

    assert exc_info.value.http_status == 500


def test_publish_post_auth_error_401(monkeypatch):
    svc = LinkedInService()

    class MockResponse:
        status_code = 401
        headers = {}
        text = '{"message": "Expired access token"}'

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MockResponse())

    with pytest.raises(LinkedInAuthError):
        svc.publish_post("token", "urn:li:person:sub123", "Content")


def test_publish_post_timeout(monkeypatch):
    svc = LinkedInService()

    def mock_post_timeout(*args, **kwargs):
        raise requests.Timeout("Gateway timeout connecting to LinkedIn REST endpoint")

    monkeypatch.setattr(requests, "post", mock_post_timeout)

    with pytest.raises(LinkedInTransientError) as exc_info:
        svc.publish_post("token", "urn:li:person:sub123", "Content")

    assert "timed out" in exc_info.value.message.lower()
