from functools import lru_cache
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings 2.6.1 silently drops some values when reading the .env
# file directly (length-sensitive parser bug). Loading via python-dotenv
# into os.environ first sidesteps that — Settings then reads from the OS
# environment which is rock-solid.
load_dotenv(override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "Master Equipment List"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    API_PREFIX: str = "/api/v1"
    # Comma-separated string in env; use the `cors_origins` property to access as list.
    CORS_ORIGINS: str = "http://localhost:3000"

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "mel_db"
    DB_ECHO: bool = False
    # Set DB_SSL=true for hosted Postgres providers (Render, Heroku, RDS w/ SSL).
    # asyncpg understands ?ssl=true; psycopg2 understands ?sslmode=require.
    DB_SSL: bool = False

    JWT_SECRET_KEY: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "ChangeMe123!"
    FIRST_ADMIN_NAME: str = "Administrator"

    MS_TENANT_ID: str = ""
    MS_CLIENT_ID: str = ""
    MS_CLIENT_SECRET: str = ""
    MS_REDIRECT_URI: str = "http://localhost:8000/api/v1/onedrive/oauth/callback"
    MS_SCOPES: str = "Files.Read.All,Files.ReadWrite.All,offline_access,User.Read"

    STORAGE_ROOT: str = "./storage"
    TESSERACT_CMD: str = ""
    POPPLER_PATH: str = ""

    # Where to send the user after the OneDrive OAuth callback finishes.
    FRONTEND_BASE_URL: str = "http://localhost:3000"

    # OpenAI vision — when set, the PFD/Vendor extractors prefer
    # GPT-4o vision over OCR. Falls back to OCR if unset or on failure.
    OPENAI_API_KEY: str = ""
    VISION_MODEL: str = "gpt-4o"
    # Cap pages we send to vision per PDF (cost + latency control). Set
    # high enough that typical vendor data sheets (2-10 pages) and PFD
    # bundles (5-20 pages) are captured in full. Set to 0 for unlimited.
    VISION_MAX_PAGES: int = 50
    # Render DPI for the page image we send to vision. Higher = more
    # readable small text, but slower + larger images. GPT-4o resizes
    # incoming images to a 2048×2048 max internally, so anything beyond
    # ~300 DPI is wasted bandwidth on typical A1 landscape pages.
    VISION_RENDER_DPI: int = 300
    # Geometric tiling grid sent with each page (no semantic crops).
    # Set both to 1 to disable tiling (overview only) — RECOMMENDED FOR
    # OPENAI. GPT-4o's `detail: "high"` mode already tiles the image
    # internally into 512×512 patches, so external tiling multiplies
    # API calls (and cost) without adding information the model uses.
    # For Claude Sonnet 4.5 (which does NOT internally tile) the old
    # 3×2 grid was helpful; for GPT-4o, 1×1 is 7× fewer API calls per
    # page with no accuracy loss on standard vendor drawings. Override
    # in .env if a particular document type needs external tiling.
    VISION_TILE_COLS: int = 1
    VISION_TILE_ROWS: int = 1

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def ms_scopes(self) -> List[str]:
        return [s.strip() for s in self.MS_SCOPES.split(",") if s.strip()]

    @property
    def database_url(self) -> str:
        # asyncpg does NOT accept ssl-mode params on the URL — we pass
        # ssl=True via connect_args in session.py when DB_SSL is true.
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def database_url_sync(self) -> str:
        suffix = "?sslmode=require" if self.DB_SSL else ""
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}{suffix}"
        )

    @property
    def storage_path(self) -> Path:
        p = Path(self.STORAGE_ROOT).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
