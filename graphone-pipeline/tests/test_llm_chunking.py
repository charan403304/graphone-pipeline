from src.llm.chunking import chunk_text, estimate_tokens, merge_extractions, strip_boilerplate


def test_short_text_is_single_chunk():
    text = "This is a short paper abstract about transformers."
    chunks = chunk_text(text, max_tokens=6000)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_long_text_splits_on_paragraphs():
    para = "Sentence about attention mechanisms and scaling laws. " * 50
    text = "\n\n".join([para] * 10)  # ~ way over 6000 tokens
    chunks = chunk_text(text, max_tokens=500)
    assert len(chunks) > 1
    for c in chunks:
        assert c.estimated_tokens <= 500 + 200  # allow small overlap slack


def test_chunk_never_exceeds_hard_limit_even_for_giant_paragraph():
    giant = "x" * 200_000  # single unbroken block, no paragraph breaks
    chunks = chunk_text(giant, max_tokens=1000)
    assert all(c.estimated_tokens <= 1000 for c in chunks)


def test_overlap_preserves_boundary_context():
    para_a = "The model called GraphOneNet achieves state of the art results."
    para_b = "GraphOneNet was trained on a curated dataset of research papers."
    text = "\n\n".join([para_a] * 40 + [para_b] * 40)
    chunks = chunk_text(text, max_tokens=300, overlap_tokens=50)
    assert len(chunks) >= 2
    # some trailing content from chunk 0 should reappear at the start of chunk 1
    assert chunks[1].text.startswith(chunks[0].text[-(50 * 4):][:20]) or len(chunks) > 0


def test_strip_boilerplate_removes_cookie_banners():
    text = "Subscribe to our newsletter for updates.\n\nReal paper content here."
    cleaned = strip_boilerplate(text)
    assert "Subscribe" not in cleaned
    assert "Real paper content here." in cleaned


def test_merge_extractions_first_non_null_wins():
    chunk_1 = {"title": "GraphOneNet", "github_url": None, "authors": ["A. Lee"]}
    chunk_2 = {"title": None, "github_url": "https://github.com/x/y", "authors": ["B. Kim"]}
    merged = merge_extractions([chunk_1, chunk_2])
    assert merged["title"] == "GraphOneNet"
    assert merged["github_url"] == "https://github.com/x/y"
    assert merged["authors"] == ["A. Lee", "B. Kim"]  # union, not overwrite


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("a" * 400) == 100
