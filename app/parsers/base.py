from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParseResult:
    parser: str
    status: str = "success"
    error: str | None = None
    pages: int | None = None
    used_ocr: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "parser": self.parser,
            "status": self.status,
            "error": self.error,
            "pages": self.pages,
            "used_ocr": self.used_ocr,
            "data": self.data,
        }
