import os
import sys
import uuid

# Ensure repository root and backend directory are on sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_current_dir)
for _p in (_repo_root, _current_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask, g, jsonify, request
from flask_cors import CORS

from backend.config import settings
from backend.controllers.ai_controller import ai_controller
from backend.controllers.auth_controller import auth_controller
from backend.controllers.health_controller import health_controller
from backend.controllers.post_controller import post_controller
from backend.services.auth_service import login_required, rate_limit
from backend.services.exceptions import AppException
from backend.services.logger import get_logger, setup_logging

logger = get_logger("app_factory")


# Module-level worker tracking
_worker_started = False


def start_embedded_worker():
    global _worker_started
    if _worker_started:
        return
    # In development with debug=True, werkzeug runs two processes: watcher and worker.
    # WERKZEUG_RUN_MAIN is only set in the actual worker process that serves requests.
    if settings.FLASK_ENV == "development" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    # Don't run in pytest or testing environments
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("TESTING") == "true":
        return

    import threading
    from backend.workers.publisher_worker import run_worker_loop

    _worker_started = True
    worker_thread = threading.Thread(
        target=run_worker_loop,
        daemon=True,
        name="EmbeddedPublisherWorker",
    )
    worker_thread.start()
    logger.info("embedded_publisher_worker_daemon_thread_started")


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory for LinkedIn Post Scheduler backend."""
    setup_logging(log_level="DEBUG" if settings.FLASK_ENV == "development" else "INFO")

    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.SECRET_KEY

    if test_config:
        app.config.update(test_config)

    # 1. Configure CORS
    CORS(
        app,
        origins=[settings.FRONTEND_URL],
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Request-ID"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    )

    # 2. Request Correlation & Context Middleware
    @app.before_request
    def before_request():
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.request_id = request_id

    @app.after_request
    def after_request(response):
        req_id = getattr(request, "request_id", None)
        if req_id:
            response.headers["X-Request-ID"] = req_id
        return response

    # 3. Direct Route Registrations (URL -> Controller)
    # Health check
    @app.route("/api/v1/health", methods=["GET"])
    def get_health():
        data, status_code = health_controller.check_health()
        return jsonify(data), status_code

    # Auth routes
    @app.route("/api/v1/auth/linkedin/login", methods=["GET"])
    def linkedin_login():
        resp, _ = auth_controller.initiate_linkedin_auth()
        return resp

    @app.route("/api/v1/auth/dev-login", methods=["GET", "POST"])
    def dev_login():
        return auth_controller.dev_login()

    @app.route("/api/v1/auth/quick-login", methods=["GET", "POST"])
    def quick_login():
        return auth_controller.quick_login()

    @app.route("/api/v1/auth/signup", methods=["POST"])
    def signup():
        return auth_controller.signup()

    @app.route("/api/v1/auth/signin", methods=["POST"])
    def signin():
        return auth_controller.signin()

    @app.route("/api/v1/auth/linkedin/callback", methods=["GET"])
    def linkedin_callback():
        return auth_controller.handle_linkedin_callback()

    @app.route("/api/v1/auth/me", methods=["GET"])
    @login_required
    def get_me():
        profile = auth_controller.get_current_user_profile(g.user_id)
        return jsonify({"data": profile})

    @app.route("/api/v1/auth/logout", methods=["POST"])
    def logout():
        return auth_controller.logout()

    @app.route("/api/v1/auth/linkedin/disconnect", methods=["POST"])
    @login_required
    def disconnect_linkedin():
        result = auth_controller.disconnect_linkedin(g.user_id)
        return jsonify({"data": result})

    # Post routes
    @app.route("/api/v1/posts", methods=["POST"])
    @login_required
    def create_post():
        return post_controller.create_post(g.user_id)

    @app.route("/api/v1/posts", methods=["GET"])
    @login_required
    def list_posts():
        return post_controller.list_posts(g.user_id)

    @app.route("/api/v1/posts/metrics", methods=["GET"])
    @login_required
    def get_metrics():
        return post_controller.get_metrics(g.user_id)

    @app.route("/api/v1/posts/suggested-topics", methods=["GET"])
    @login_required
    def get_suggested_topics():
        return post_controller.get_suggested_topics(g.user_id)

    @app.route("/api/v1/posts/<int:post_id>", methods=["GET"])
    @login_required
    def get_post(post_id: int):
        return post_controller.get_post(g.user_id, post_id)

    @app.route("/api/v1/posts/<int:post_id>", methods=["PUT"])
    @login_required
    def update_post(post_id: int):
        return post_controller.update_post(g.user_id, post_id)

    @app.route("/api/v1/posts/<int:post_id>/cancel", methods=["POST"])
    @login_required
    def cancel_post(post_id: int):
        return post_controller.cancel_post(g.user_id, post_id)

    @app.route("/api/v1/posts/<int:post_id>/attempts", methods=["GET"])
    @login_required
    def get_attempts(post_id: int):
        return post_controller.get_attempts(g.user_id, post_id)

    # AI routes
    @app.route("/api/v1/ai/generate-post", methods=["POST"])
    @login_required
    @rate_limit(max_requests=15, window_seconds=60)
    def generate_post():
        return ai_controller.generate_post()

    @app.route("/api/v1/ai/rewrite-post", methods=["POST"])
    @login_required
    @rate_limit(max_requests=25, window_seconds=60)
    def rewrite_post():
        return ai_controller.rewrite_post()

    # 4. Centralized Error Handlers
    @app.errorhandler(AppException)
    def handle_app_exception(error: AppException):
        # Unauthenticated session probe on /api/v1/auth/me is normal and expected when user visits site logged out
        if error.status_code == 401 and request.path == "/api/v1/auth/me":
            logger.info(
                "auth_session_check_unauthenticated",
                code=error.code,
                path=request.path,
            )
        else:
            logger.warn(
                "app_exception_handled",
                code=error.code,
                message=error.message,
                status_code=error.status_code,
                path=request.path,
            )
        return jsonify(error.to_dict()), error.status_code

    @app.errorhandler(404)
    def handle_404(error):
        return (
            jsonify(
                {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"Endpoint not found: {request.method} {request.path}",
                    }
                }
            ),
            404,
        )

    @app.errorhandler(405)
    def handle_405(error):
        return (
            jsonify(
                {
                    "error": {
                        "code": "METHOD_NOT_ALLOWED",
                        "message": f"Method {request.method} is not allowed for {request.path}",
                    }
                }
            ),
            405,
        )

    @app.errorhandler(500)
    def handle_500(error):
        logger.error("internal_server_error", exc_info=True, path=request.path)
        return (
            jsonify(
                {
                    "error": {
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "An unexpected error occurred. Please try again later.",
                    }
                }
            ),
            500,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_exception(error: Exception):
        logger.error("unhandled_exception", error=str(error), exc_info=True, path=request.path)
        return (
            jsonify(
                {
                    "error": {
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "An unexpected server error occurred.",
                    }
                }
            ),
            500,
        )

    # Start background scheduler daemon thread automatically
    start_embedded_worker()

    return app


if __name__ == "__main__":
    app_instance = create_app()
    app_instance.run(host="0.0.0.0", port=5000, debug=settings.FLASK_ENV == "development")
