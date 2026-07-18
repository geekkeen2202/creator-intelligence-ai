from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunUsage:
    input_tokens: int
    output_tokens: int
    cost: float


def extract_usage(result: Any) -> RunUsage:
    """Best-effort token/cost extraction from an Agno RunOutput — some
    providers/models don't populate cost, so this always returns zeros
    rather than raising (a script must still get provenance rows; §8 rule 9
    requires the columns to exist, not that cost is always non-zero).
    """
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return RunUsage(input_tokens=0, output_tokens=0, cost=0.0)
    return RunUsage(
        input_tokens=getattr(metrics, "input_tokens", None) or 0,
        output_tokens=getattr(metrics, "output_tokens", None) or 0,
        cost=getattr(metrics, "cost", None) or 0.0,
    )
