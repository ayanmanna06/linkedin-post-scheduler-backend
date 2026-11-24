import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import Response, g, request

from backend.config import settings
from backend.services.exceptions import RateLimitError, UnauthorizedError

COOKIE_NAME = "session_token"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}


class AuthService:
    COOKIE_NAME = COOKIE_NAME
    @staticmethod
    def create_jwt_token(user_id: int) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(days=JWT_EXPIRATION_DAYS),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_jwt_token(token: str) -> int:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return int(payload["sub"])
        except jwt.ExpiredSignatureError as e:
            raise UnauthorizedError("Session expired. Please log in again.") from e
        except (jwt.InvalidTokenError, ValueError, KeyError) as e:
            raise UnauthorizedError("Invalid session credentials.") from e

    @staticmethod
    def set_auth_cookie(response: Response, token: str) -> Response:
        is_prod = settings.FLASK_ENV == "production"
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=JWT_EXPIRATION_DAYS * 24 * 3600,
            httponly=True,
            secure=is_prod,
            samesite="Lax",
            path="/",
        )
        return response

    @staticmethod
    def clear_auth_cookie(response: Response) -> Response:
        response.set_cookie(
            key=COOKIE_NAME,
            value="",
            max_age=0,
            httponly=True,
            secure=settings.FLASK_ENV == "production",
            samesite="Lax",
            path="/",
        )
        return response


auth_service = AuthService()


def login_required(f: Callable) -> Callable:
    """Decorator ensuring request contains a valid JWT session cookie or Bearer token."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]

        if not token:
            raise UnauthorizedError("Authentication required. Please connect with LinkedIn.")

        user_id = auth_service.verify_jwt_token(token)
        g.user_id = user_id
        return f(*args, **kwargs)

    return decorated_function


def rate_limit(max_requests: int = 20, window_seconds: int = 60) -> Callable:
    """Sliding window rate limiter decorator."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = getattr(g, "user_id", None)
            if not key:
                key = request.remote_addr or "anonymous"
            key = str(key)

            now = time.time()
            timestamps = _RATE_LIMIT_BUCKETS.setdefault(key, [])
            timestamps = [t for t in timestamps if now - t < window_seconds]
            _RATE_LIMIT_BUCKETS[key] = timestamps

            if len(timestamps) >= max_requests:
                raise RateLimitError(
                    f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds}s."
                )

            timestamps.append(now)
            return f(*args, **kwargs)

        return wrapped

    return decorator
