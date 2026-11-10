import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
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
from app.core.logging import clean_request_id, configure_logging, request_id_var
from app.core.monitoring import init_monitoring
from app.db.session import engine
from app.profiles.service import ProfileError

settings = get_settings()
configure_logging(settings.log_level)
init_monitoring("api")
log = logging.getLogger(__name__)


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


@app.middleware("http")
async def request_id(request: Request, call_next) -> Response:  # noqa: ANN001
    """Tag every request so its log lines can be found together.

    The id is echoed back as X-Request-ID, so a person reporting a problem can quote it
    and it can be grepped straight out of the logs.
    """
    rid = clean_request_id(request.headers.get("x-request-id"))
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


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


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything not handled above.

    Without this the response is Starlette's plain-text "Internal Server Error", which
    does not match the {"detail": {"code", "message"}} shape every other error uses, so
    the browser could only show a bare "Request failed (500)". The cause is logged with
    the request id and goes to Sentry; the body never carries it.
    """
    rid = request_id_var.get()
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "server_error",
                "message": "Something went wrong on our side. Please try again.",
                "request_id": rid,
            }
        },
        headers={"X-Request-ID": rid},
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
