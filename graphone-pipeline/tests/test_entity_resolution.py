from src.entity_resolution.resolver import EntityResolver


def test_exact_match():
    r = EntityResolver()
    result = r.resolve("OpenAI")
    assert result.canonical_name == "OpenAI"
    assert result.method == "exact"
    assert result.confidence == 1.0


def test_alias_match_legal_suffix():
    r = EntityResolver()
    result = r.resolve("OpenAI, Inc.")
    assert result.canonical_name == "OpenAI"
    assert result.method in ("alias",)


def test_normalized_alias_match_variant_spacing():
    r = EntityResolver()
    result = r.resolve("Open AI")
    assert result.canonical_name == "OpenAI"


def test_fuzzy_match_typo():
    r = EntityResolver()
    result = r.resolve("Anthropc PBC")  # typo'd
    assert result.canonical_name == "Anthropic"
    assert result.method == "fuzzy"
    assert result.confidence >= 0.86


def test_unrelated_name_does_not_false_positive_match():
    r = EntityResolver()
    result = r.resolve("Completely Unrelated Startup Nine Zero Zero")
    assert result.method == "unresolved"


def test_mapping_log_is_recorded():
    r = EntityResolver()
    r.resolve("OpenAI, Inc.", source_url="https://example.com/a")
    r.resolve("Totally Unknown Co", source_url="https://example.com/b")
    assert len(r.log) == 2
    assert r.log[0].raw_name == "OpenAI, Inc."
    assert r.log[0].canonical_name == "OpenAI"
    assert r.log[1].canonical_name == "(unresolved)"


def test_register_new_canonical_then_resolves_exact():
    r = EntityResolver()
    r.register_new_canonical("Brand New Startup", aliases=["BNS Inc"])
    assert r.resolve("Brand New Startup").method == "exact"
    assert r.resolve("BNS Inc").method == "alias"
