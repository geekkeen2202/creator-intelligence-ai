"""Events this module emits (see ARCHITECTURE.md §5)."""

from app.shared.events import (
    SCRIPT_GENERATED,
    SCRIPT_OUTCOME_LINKED,
    SCRIPT_PUBLISHED,
    SCRIPT_RATED,
)

__all__ = ["SCRIPT_GENERATED", "SCRIPT_RATED", "SCRIPT_PUBLISHED", "SCRIPT_OUTCOME_LINKED"]
