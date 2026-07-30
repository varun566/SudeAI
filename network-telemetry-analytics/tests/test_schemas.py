from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import TelemetryCreate


def valid_payload() -> dict[str, object]:
    return {
        "observed_at": datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        "server": "edge-1",
        "source": "us-east-1",
        "destination": "eu-west-1",
        "latency_ms": 41.2,
        "packet_loss_pct": 0.1,
        "throughput_mbps": 850.5,
    }


def test_valid_telemetry_payload() -> None:
    payload = TelemetryCreate.model_validate(valid_payload())
    assert payload.protocol == "tcp"
    assert payload.tags == {}


def test_rejects_timezone_naive_observation() -> None:
    values = valid_payload()
    values["observed_at"] = datetime(2026, 7, 29, 12, 0)
    with pytest.raises(ValidationError, match="timezone"):
        TelemetryCreate.model_validate(values)


def test_rejects_invalid_packet_loss() -> None:
    values = valid_payload()
    values["packet_loss_pct"] = 100.01
    with pytest.raises(ValidationError):
        TelemetryCreate.model_validate(values)
