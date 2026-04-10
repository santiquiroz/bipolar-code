from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import proxy, models, usage
from app.api import settings as settings_router
from app.core.logging import setup_logging, get_logger
from app.core.config import get_settings

setup_logging()
log = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Bipolar Code",
        description="LiteLLM Proxy Manager API",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(proxy.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(usage.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")

    @app.get("/api/health")
    async def health():
        log.info("health_check")
        return {"status": "ok", "version": "0.1.0"}

    log.info("app_created", env=settings.env)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
