"""Shared pagination response shape.

Every paginated list endpoint returns this wrapper instead of a bare
array, so the frontend always knows the total row count (needed to
render page numbers / disable next-page) regardless of how many rows
came back in this page.
"""
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
