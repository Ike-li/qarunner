"""Real ID generator using UUID4."""

from __future__ import annotations

import uuid


class UuidIds:
    """Generates unique identifiers using UUID4."""

    def new_id(self) -> str:
        return str(uuid.uuid4())
