from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.db import get_engine
from backend.services.logger import get_logger

logger = get_logger("health_controller")


class HealthController:
    @staticmethod
    def check_health() -> tuple[dict[str, Any], int]:
        db_status = "unhealthy"
        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1;"))
            db_status = "healthy"
        except Exception as e:
            logger.error("health_check_db_failure", error=str(e))
            db_status = f"unhealthy: {str(e)}"

        status_code = 200 if db_status == "healthy" else 503
        data = {
            "status": "ok" if db_status == "healthy" else "degraded",
            "timestamp": datetime.now(UTC).isoformat(),
            "services": {
                "database": db_status,
                "api": "healthy",
            },
            "version": "1.0.0",
        }
        return data, status_code


health_controller = HealthController()
