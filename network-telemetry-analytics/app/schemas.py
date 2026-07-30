import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyString = Annotated[str, Field(min_length=1, max_length=255)]


class TelemetryCreate(BaseModel):
    observed_at: datetime
    server: Annotated[str, Field(min_length=1, max_length=128)]
    source: NonEmptyString
    destination: NonEmptyString
    protocol: Annotated[str, Field(min_length=1, max_length=16)] = "tcp"
    latency_ms: Annotated[float, Field(ge=0, le=1_000_000)]
    packet_loss_pct: Annotated[float, Field(ge=0, le=100)]
    throughput_mbps: Annotated[float, Field(ge=0, le=10_000_000)]
    jitter_ms: Annotated[float | None, Field(ge=0, le=1_000_000)] = None
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("server", "source", "destination")
    @classmethod
    def strip_required_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("protocol")
    @classmethod
    def normalize_protocol(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def limit_tags(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 25:
            raise ValueError("at most 25 tags are allowed")
        if any(len(key) > 64 or len(tag_value) > 256 for key, tag_value in value.items()):
            raise ValueError("tag keys must be <= 64 chars and values <= 256 chars")
        return value

    @model_validator(mode="after")
    def timestamp_must_be_timezone_aware(self) -> "TelemetryCreate":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone offset")
        return self


class TelemetryRead(TelemetryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class BatchIngestRequest(BaseModel):
    records: Annotated[list[TelemetryCreate], Field(min_length=1, max_length=1_000)]


class BatchIngestResponse(BaseModel):
    inserted: int
    records: list[TelemetryRead]


class TrendPoint(BaseModel):
    bucket_start: datetime
    sample_count: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    avg_packet_loss_pct: float | None
    avg_throughput_mbps: float | None


class TrendResponse(BaseModel):
    bucket: str
    points: list[TrendPoint]
    filters: dict[str, Any]


class AnomalyRecord(BaseModel):
    id: uuid.UUID
    observed_at: datetime
    server: str
    source: str
    destination: str
    latency_ms: float
    packet_loss_pct: float
    throughput_mbps: float
    rolling_avg_latency_ms: float | None
    rolling_stddev_latency_ms: float | None
    latency_z_score: float | None
    reason: str


class AnomalyResponse(BaseModel):
    window: str
    z_score_threshold: float
    packet_loss_threshold: float
    anomalies: list[AnomalyRecord]


class SummaryResponse(BaseModel):
    sample_count: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    avg_packet_loss_pct: float | None
    peak_throughput_mbps: float | None
    high_latency_samples: int
    packet_loss_incidents: int
    filters: dict[str, Any]
