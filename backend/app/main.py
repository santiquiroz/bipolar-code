import asyncio
import base64
import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import proxy, models, usage, chat as chat_router
from app.api import settings as settings_router
from app.api import providers as providers_router
from app.core.logging import setup_logging, get_logger
from app.core.config import get_settings

setup_logging()
log = get_logger(__name__)

_REFRESH_MARGIN = 120   # refresh when less than 2 min remain
_RETRY_ON_ERROR = 60    # retry after 1 min on failure
_DEFAULT_INTERVAL = 1500  # fallback if token has no exp claim


def _token_expires_in(token: str) -> float:
    """Decode JWT exp claim. Returns seconds until expiry, or 0 if expired/invalid."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        padding = 4 - len(parts[1]) % 4
        payload = json.loads(base64.b64decode(parts[1] + "=" * padding))
        return max(0.0, payload.get("exp", 0) - time.time())
    except Exception:
        return 0.0


async def _copilot_token_refresh_loop():
    await asyncio.sleep(10)  # let app finish starting up
    log.info("copilot_auto_refresh_loop_started")
    while True:
        wait = _DEFAULT_INTERVAL
        try:
            from app.services import providers_service
            active = providers_service.get_active_provider()
            if active and active.id == "copilot" and os.environ.get("GITHUB_OAUTH_TOKEN"):
                expires_in = _token_expires_in(os.environ.get("COPILOT_SESSION_TOKEN", ""))
                if expires_in < _REFRESH_MARGIN:
                    result = await providers_service.refresh_copilot_token()
                    log.info("copilot_token_auto_refreshed", token_length=result["token_length"])
                else:
                    wait = max(60, expires_in - _REFRESH_MARGIN)
                    log.debug("copilot_token_valid", expires_in=int(expires_in), next_check_s=int(wait))
            else:
                log.debug("copilot_auto_refresh_skipped", active_provider=active.id if active else None)
        except Exception as e:
            log.warning("copilot_token_auto_refresh_failed", error=str(e))
            wait = _RETRY_ON_ERROR
        await asyncio.sleep(wait)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_copilot_token_refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Bipolar Code",
        description="LiteLLM Proxy Manager API",
        version="0.2.0",
        lifespan=lifespan,
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
    app.include_router(providers_router.router, prefix="/api")
    app.include_router(chat_router.router, prefix="/api")

    @app.get("/api/health")
    async def health():
        log.info("health_check")
        return {"status": "ok", "version": "0.2.0"}

    # Servir frontend compilado si existe (producción / binario PyInstaller)
    import pathlib
    dist_dir = pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"
    if dist_dir.exists():
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class SPAStaticFiles(StaticFiles):
            """StaticFiles con fallback SPA: cualquier 404 devuelve index.html."""
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as ex:
                    if ex.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="spa")

    log.info("app_created", env=settings.env)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
