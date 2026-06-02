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

    # Anthropic vision — when set, the PFD/Vendor extractors prefer
    # Claude vision over OCR. Falls back to OCR if unset or on failure.
    ANTHROPIC_API_KEY: str = ""
    VISION_MODEL: str = "claude-sonnet-4-5-20250929"
    # Cap pages we send to vision per PDF (cost + latency control). Set
    # high enough that typical vendor data sheets (2-10 pages) and PFD
    # bundles (5-20 pages) are captured in full. Set to 0 for unlimited.
    VISION_MAX_PAGES: int = 50
    # Render DPI for the page image we send to vision. Higher = more
    # readable small text, but slower + larger images. 400 is a good
    # default for A1 engineering drawings.
    VISION_RENDER_DPI: int = 400
    # Geometric tiling grid sent with each page (no semantic crops). The
    # full page is ALWAYS sent as an overview image; tiles are extra
    # high-resolution slices used to read small text. Set both to 1 to
    # disable tiling (overview only).
    VISION_TILE_COLS: int = 3
    VISION_TILE_ROWS: int = 2

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def ms_scopes(self) -> List[str]:
        return [s.strip() for s in self.MS_SCOPES.split(",") if s.strip()]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
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
