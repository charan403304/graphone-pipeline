from datetime import datetime, timedelta, timezone

from src.utils.date_utils import (
    is_new_since_last_run,
    normalize_publication_date,
    parse_relative_date,
)

REF = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def test_structured_date_within_24h_is_fresh():
    result = normalize_publication_date(
        structured_meta="2026-08-28T02:00:00Z",
        visible_relative_text=None,
        reference_time=REF,
    )
    assert result.confidence == "structured"
    assert result.is_fresh_24h is True


def test_structured_date_older_than_24h_is_stale():
    result = normalize_publication_date(
        structured_meta="2026-08-25T02:00:00Z",
        visible_relative_text=None,
        reference_time=REF,
    )
    assert result.is_fresh_24h is False


def test_relative_hours_ago():
    dt = parse_relative_date("2 hours ago", REF)
    assert dt == REF - timedelta(hours=2)


def test_relative_days_ago_stale():
    result = normalize_publication_date(
        structured_meta=None,
        visible_relative_text="3 days ago",
        reference_time=REF,
    )
    assert result.confidence == "relative"
    assert result.is_fresh_24h is False


def test_relative_word_aliases():
    assert parse_relative_date("just now", REF) == REF - timedelta(seconds=30)
    assert parse_relative_date("Yesterday", REF) is not None


def test_short_form_relative():
    dt = parse_relative_date("5h", REF)
    assert dt == REF - timedelta(hours=5)


def test_no_signal_returns_none_confidence():
    result = normalize_publication_date(
        structured_meta=None, visible_relative_text=None, reference_time=REF
    )
    assert result.confidence == "none"
    assert result.iso_timestamp is None
    assert result.is_fresh_24h is None


def test_malformed_structured_falls_through_to_relative():
    result = normalize_publication_date(
        structured_meta="not-a-real-date",
        visible_relative_text="1 hour ago",
        reference_time=REF,
    )
    assert result.confidence == "relative"


def test_heuristic_new_content():
    seen = {"https://x.com/a": "hash1"}
    assert is_new_since_last_run("https://x.com/a", "hash2", seen) is True   # changed
    assert is_new_since_last_run("https://x.com/a", "hash1", seen) is False  # unchanged
    assert is_new_since_last_run("https://x.com/b", "hash3", seen) is True   # never seen
