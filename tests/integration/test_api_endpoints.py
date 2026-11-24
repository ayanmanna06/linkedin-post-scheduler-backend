from datetime import UTC, datetime, timedelta


def test_health_check_endpoint(unauthenticated_client):
    resp = unauthenticated_client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] in ("ok", "degraded")
    assert data["version"] == "1.0.0"


def test_auth_me_unauthorized(unauthenticated_client):
    resp = unauthenticated_client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["error"]["code"] == "UNAUTHORIZED"


def test_auth_me_authorized(client, test_user):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["display_name"] == test_user.display_name
    assert data["connection"]["connected"] is True


def test_create_post_validation_error_past_date(client):
    past_time = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "content": "This post is in the past.",
        "scheduled_at": past_time,
        "timezone": "Asia/Kolkata",
    }
    resp = client.post("/api/v1/posts", json=payload)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"]["code"] == "VALIDATION_ERROR"


def test_create_post_success(client):
    future_time = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "content": "Excited to share an architectural breakthrough in scalable workers!",
        "scheduled_at": future_time,
        "timezone": "Asia/Kolkata",
        "source": "manual",
    }
    resp = client.post("/api/v1/posts", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert data["content"] == payload["content"]
    assert data["status"] == "scheduled"
    assert data["id"] is not None


def test_list_and_filter_posts(client):
    resp = client.get("/api/v1/posts")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "items" in data
    assert "pagination" in data


def test_cancel_post_flow(client):
    # 1. Create a scheduled post
    future_time = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    create_resp = client.post(
        "/api/v1/posts",
        json={
            "content": "Post to be cancelled",
            "scheduled_at": future_time,
            "timezone": "Asia/Kolkata",
        },
    )
    assert create_resp.status_code == 201
    post_id = create_resp.get_json()["data"]["id"]

    # 2. Cancel it
    cancel_resp = client.post(f"/api/v1/posts/{post_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.get_json()["data"]["status"] == "cancelled"

    # 3. Cancelling again should raise ConflictError (409)
    retry_cancel = client.post(f"/api/v1/posts/{post_id}/cancel")
    assert retry_cancel.status_code == 409
