"""Shared OpenAI client singleton.

A single ``OpenAI`` instance is reused across all services (vision,
pfd_field_mapper, vendor_field_mapper, pid_field_mapper) so the HTTP
connection pool is only created once and every call benefits from
keep-alive connections.
"""
from __future__ import annotations

_client = None


def get_openai_client():
    """Return the module-level OpenAI client, creating it on first call."""
    global _client
    if _client is None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(f"openai SDK not installed: {e}")
        from app.config import settings
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client
