"""Deterministic channel -> canonical niche classification.

Pure, offline, no external calls and no LLM cost — this runs once per channel
(when CHANNEL_ANALYZED fires), never per trending refresh, so it doesn't scale
with ingestion frequency or user traffic. Keeps the canonical niche set the
only thing ingestion volume depends on (see app/modules/trending/niches.py).
"""

import re

from app.modules.trending.niches import NICHE_MAP

_FALLBACK_NICHE = "general"
_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def classify_niche(keywords: list[str]) -> tuple[str, float]:
    """Scores channel keywords/metadata against each niche's keyword list.

    Returns (niche, confidence) — confidence is the fraction of niche keywords
    matched by the input. Falls back to ("general", 0.0) when nothing matches
    or no keywords were supplied (e.g. Voice DNA not populated yet).
    """
    input_tokens: set[str] = set()
    for keyword in keywords:
        input_tokens |= tokenize(keyword)
    if not input_tokens:
        return _FALLBACK_NICHE, 0.0

    best_niche = _FALLBACK_NICHE
    best_score = 0.0
    for niche, config in NICHE_MAP.items():
        if niche == _FALLBACK_NICHE:
            continue
        niche_tokens: set[str] = set()
        for keyword in config.keywords:
            niche_tokens |= tokenize(keyword)
        if not niche_tokens:
            continue
        overlap = len(input_tokens & niche_tokens)
        if overlap == 0:
            continue
        score = overlap / len(niche_tokens)
        if score > best_score:
            best_score = score
            best_niche = niche

    return best_niche, best_score
