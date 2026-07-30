import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TelemetryRecord(Base):
    __tablename__ = "telemetry_records"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_telemetry_latency_nonnegative"),
        CheckConstraint(
            "packet_loss_pct >= 0 AND packet_loss_pct <= 100",
            name="ck_telemetry_packet_loss_range",
        ),
        CheckConstraint("throughput_mbps >= 0", name="ck_telemetry_throughput_nonnegative"),
        CheckConstraint(
            "jitter_ms IS NULL OR jitter_ms >= 0", name="ck_telemetry_jitter_nonnegative"
        ),
        Index("ix_telemetry_observed_at", "observed_at"),
        Index("ix_telemetry_path_observed", "source", "destination", "observed_at"),
        Index("ix_telemetry_server_observed", "server", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    server: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="tcp")
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    packet_loss_pct: Mapped[float] = mapped_column(Float, nullable=False)
    throughput_mbps: Mapped[float] = mapped_column(Float, nullable=False)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
