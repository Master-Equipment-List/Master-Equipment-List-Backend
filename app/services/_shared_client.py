"""Shared Anthropic client singleton.

A single ``Anthropic`` instance is reused across all services (vision,
pfd_field_mapper, vendor_field_mapper, pid_field_mapper) so the HTTP
connection pool is only created once and every call benefits from
keep-alive connections.
"""
from __future__ import annotations

_client = None


def get_anthropic_client():
    """Return the module-level Anthropic client, creating it on first call."""
    global _client
    if _client is None:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise RuntimeError(f"anthropic SDK not installed: {e}")
        from app.config import settings
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client
