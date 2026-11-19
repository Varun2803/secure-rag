import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database.postgres import check_database_connection
from app.database.qdrant import check_qdrant_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    return {"status": "healthy", "application": get_settings().app_name}


def _service_health(name: str, healthy: bool) -> JSONResponse:
    if not healthy:
        logger.warning("%s health check failed", name)
    return JSONResponse(status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
                        content={"status": "healthy" if healthy else "unavailable", "service": name})


@router.get("/database")
def database_health() -> JSONResponse:
    return _service_health("database", check_database_connection())


@router.get("/vector-db")
def vector_db_health() -> JSONResponse:
    return _service_health("vector-db", check_qdrant_connection())
