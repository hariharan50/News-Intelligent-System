from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.service import AgentError
from app.api import (
    account,
    admin,
    agent,
    alerts,
    auth,
    billing,
    dev,
    feed,
    health,
    meta,
    profiles,
    stream,
)
from app.auth.service import AuthError
from app.billing.plans import PlanLimitError
from app.billing.service import BillingError
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.monitoring import init_monitoring
from app.db.session import engine
from app.profiles.service import ProfileError

settings = get_settings()
configure_logging(settings.log_level)
init_monitoring("api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await redis_client.aclose()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    # API docs are for development; production exposes them only if we decide to later.
    docs_url=None if settings.is_production else "/api/docs",
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AuthError)
@app.exception_handler(ProfileError)
@app.exception_handler(BillingError)
@app.exception_handler(AgentError)
async def domain_error(
    _: Request, exc: AuthError | ProfileError | BillingError | AgentError
) -> JSONResponse:
    """Domain errors become {"detail": {"code", "message"}}, like the API's HTTPExceptions."""
    return JSONResponse(
        status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(PlanLimitError)
async def plan_limit(_: Request, exc: PlanLimitError) -> JSONResponse:
    """403 with the plan that would allow it, so the app can offer the upgrade."""
    return JSONResponse(
        status_code=403,
        content={"detail": {"code": exc.code, "message": exc.message, "needs": exc.needs}},
    )


api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(profiles.router)
api_router.include_router(feed.router)
api_router.include_router(meta.router)
api_router.include_router(stream.router)
api_router.include_router(alerts.router)
api_router.include_router(admin.router)
api_router.include_router(billing.router)
api_router.include_router(agent.router)
if settings.environment in ("local", "test"):
    api_router.include_router(dev.router)
app.include_router(api_router)
