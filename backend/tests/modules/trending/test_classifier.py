from app.modules.trending.classifier import classify_niche


def test_classifies_matching_keywords_to_expected_niche():
    niche, confidence = classify_niche(["AI", "smartphone reviews", "gadgets"])

    assert niche == "technology"
    assert confidence > 0


def test_classifies_finance_keywords():
    niche, _ = classify_niche(["stock market analysis", "mutual funds for beginners"])

    assert niche == "finance"


def test_falls_back_to_general_when_no_keywords():
    niche, confidence = classify_niche([])

    assert niche == "general"
    assert confidence == 0.0


def test_falls_back_to_general_when_no_overlap():
    niche, confidence = classify_niche(["underwater basket weaving", "xyzzy"])

    assert niche == "general"
    assert confidence == 0.0


def test_picks_highest_scoring_niche_when_multiple_overlap():
    # "gaming" and "review" both touch tech/gaming keyword lists; heavier
    # gaming-specific overlap should win.
    niche, _ = classify_niche(["gaming", "BGMI", "esports", "game review"])

    assert niche == "gaming"
