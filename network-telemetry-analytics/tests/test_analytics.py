from datetime import UTC, datetime

import pytest

from app.analytics import BUCKETS, WINDOWS, _filters, _validate_choice


def test_supported_analytics_intervals_are_mapped_to_safe_sql_values() -> None:
    assert _validate_choice("5m", BUCKETS, "bucket") == "5 minutes"
    assert _validate_choice("1h", WINDOWS, "window") == "1 hour"


def test_unsupported_interval_is_rejected_before_query_execution() -> None:
    with pytest.raises(ValueError, match="Unsupported bucket"):
        _validate_choice("drop table", BUCKETS, "bucket")


def test_invalid_time_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="start must be earlier"):
        _filters(
            source=None,
            destination=None,
            start=datetime(2026, 7, 30, tzinfo=UTC),
            end=datetime(2026, 7, 29, tzinfo=UTC),
        )
