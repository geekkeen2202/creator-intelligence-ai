import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.tasks  # noqa: F401  # registers @subscribe(...) event bus handlers in this process
from app.config import get_settings
from app.modules.analytics import analytics_router
from app.modules.billing import billing_router
from app.modules.channels import channels_router
from app.modules.notifications import notifications_router
from app.modules.scripts import scripts_router
from app.modules.thumbnails import thumbnails_router
from app.modules.trending import trending_router
from app.modules.users import users_router
from app.modules.voice_profiles import voice_profiles_router

settings = get_settings()
structlog.configure(processors=[structlog.processors.JSONRenderer()])

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn, environment=settings.environment, traces_sample_rate=0.2
    )

app = FastAPI(title="Creator Intelligence Platform API", version="0.1.0")

# "*" combined with allow_credentials=True is an invalid combination browsers
# reject outright, so origins must always be an explicit list — configured via
# CORS_ORIGINS, with a sensible local-dev default when unset.
_default_local_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
cors_origins = settings.cors_origins_list or (
    _default_local_origins if settings.environment == "local" else []
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_V1_PREFIX = "/api/v1"
for router in (
    users_router,
    channels_router,
    voice_profiles_router,
    trending_router,
    scripts_router,
    thumbnails_router,
    analytics_router,
    billing_router,
    notifications_router,
):
    app.include_router(router, prefix=API_V1_PREFIX)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
