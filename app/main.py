from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin import setup_admin
from app.api.v1.router import api_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure storage directory exists at startup
    _ = settings.storage_path
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        debug=settings.APP_DEBUG,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_PREFIX)

    # Database browser for superusers, at /admin.
    setup_admin(app)

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "app": settings.APP_NAME,
            "env": settings.APP_ENV,
            "docs": "/docs",
            "api": settings.API_PREFIX,
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
