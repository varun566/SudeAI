from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BUCKETS = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "1h": "1 hour",
    "1d": "1 day",
}
WINDOWS = {"5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour", "6h": "6 hours"}


def _validate_choice(value: str, choices: dict[str, str], label: str) -> str:
    try:
        return choices[value]
    except KeyError as exc:
        allowed = ", ".join(choices)
        raise ValueError(f"Unsupported {label} '{value}'. Choose one of: {allowed}") from exc


def _filters(
    *, source: str | None, destination: str | None, start: datetime | None, end: datetime | None
) -> dict[str, object]:
    if start and end and start > end:
        raise ValueError("start must be earlier than end")
    return {"source": source, "destination": destination, "start": start, "end": end}


async def fetch_trends(
    session: AsyncSession,
    *,
    source: str | None,
    destination: str | None,
    start: datetime | None,
    end: datetime | None,
    bucket: str,
) -> list[dict[str, object]]:
    bucket_interval = _validate_choice(bucket, BUCKETS, "bucket")
    parameters = _filters(source=source, destination=destination, start=start, end=end)
    parameters["bucket"] = bucket_interval
    result = await session.execute(
        text(
            """
            SELECT
              date_bin(CAST(:bucket AS interval), observed_at, TIMESTAMPTZ '2000-01-01') AS bucket_start,
              COUNT(*) AS sample_count,
              AVG(latency_ms) AS avg_latency_ms,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
              AVG(packet_loss_pct) AS avg_packet_loss_pct,
              AVG(throughput_mbps) AS avg_throughput_mbps
            FROM telemetry_records
            WHERE (:source IS NULL OR source = :source)
              AND (:destination IS NULL OR destination = :destination)
              AND (:start IS NULL OR observed_at >= :start)
              AND (:end IS NULL OR observed_at <= :end)
            GROUP BY bucket_start
            ORDER BY bucket_start
            """
        ),
        parameters,
    )
    return [dict(row) for row in result.mappings().all()]


async def fetch_anomalies(
    session: AsyncSession,
    *,
    source: str | None,
    destination: str | None,
    start: datetime | None,
    end: datetime | None,
    window: str,
    z_score_threshold: float,
    packet_loss_threshold: float,
    limit: int,
) -> list[dict[str, object]]:
    rolling_window = _validate_choice(window, WINDOWS, "window")
    parameters = _filters(source=source, destination=destination, start=start, end=end)
    parameters.update(
        {
            "window": rolling_window,
            "z_score_threshold": z_score_threshold,
            "packet_loss_threshold": packet_loss_threshold,
            "limit": limit,
        }
    )
    result = await session.execute(
        text(
            """
            WITH rolling AS (
              SELECT
                id, observed_at, server, source, destination, latency_ms, packet_loss_pct, throughput_mbps,
                AVG(latency_ms) OVER network_window AS rolling_avg_latency_ms,
                STDDEV_SAMP(latency_ms) OVER network_window AS rolling_stddev_latency_ms,
                COUNT(*) OVER network_window AS rolling_sample_count
              FROM telemetry_records
              WHERE (:source IS NULL OR source = :source)
                AND (:destination IS NULL OR destination = :destination)
                AND (:start IS NULL OR observed_at >= :start)
                AND (:end IS NULL OR observed_at <= :end)
              WINDOW network_window AS (
                PARTITION BY source, destination
                ORDER BY observed_at
                RANGE BETWEEN CAST(:window AS interval) PRECEDING AND CURRENT ROW EXCLUDE CURRENT ROW
              )
            )
            SELECT
              id, observed_at, server, source, destination, latency_ms, packet_loss_pct, throughput_mbps,
              rolling_avg_latency_ms, rolling_stddev_latency_ms,
              CASE WHEN rolling_stddev_latency_ms > 0
                THEN (latency_ms - rolling_avg_latency_ms) / rolling_stddev_latency_ms
              END AS latency_z_score,
              CASE
                WHEN packet_loss_pct >= :packet_loss_threshold THEN 'packet_loss'
                WHEN rolling_stddev_latency_ms > 0
                  AND ABS((latency_ms - rolling_avg_latency_ms) / rolling_stddev_latency_ms) >= :z_score_threshold
                  THEN 'latency_z_score'
              END AS reason
            FROM rolling
            WHERE rolling_sample_count >= 3
              AND (
                packet_loss_pct >= :packet_loss_threshold
                OR (
                  rolling_stddev_latency_ms > 0
                  AND ABS((latency_ms - rolling_avg_latency_ms) / rolling_stddev_latency_ms) >= :z_score_threshold
                )
              )
            ORDER BY observed_at DESC
            LIMIT :limit
            """
        ),
        parameters,
    )
    return [dict(row) for row in result.mappings().all()]


async def fetch_summary(
    session: AsyncSession,
    *,
    source: str | None,
    destination: str | None,
    start: datetime | None,
    end: datetime | None,
    high_latency_threshold: float,
    packet_loss_threshold: float,
) -> dict[str, object]:
    parameters = _filters(source=source, destination=destination, start=start, end=end)
    parameters.update(
        {
            "high_latency_threshold": high_latency_threshold,
            "packet_loss_threshold": packet_loss_threshold,
        }
    )
    result = await session.execute(
        text(
            """
            SELECT
              COUNT(*) AS sample_count,
              AVG(latency_ms) AS avg_latency_ms,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
              AVG(packet_loss_pct) AS avg_packet_loss_pct,
              MAX(throughput_mbps) AS peak_throughput_mbps,
              COUNT(*) FILTER (WHERE latency_ms >= :high_latency_threshold) AS high_latency_samples,
              COUNT(*) FILTER (WHERE packet_loss_pct >= :packet_loss_threshold) AS packet_loss_incidents
            FROM telemetry_records
            WHERE (:source IS NULL OR source = :source)
              AND (:destination IS NULL OR destination = :destination)
              AND (:start IS NULL OR observed_at >= :start)
              AND (:end IS NULL OR observed_at <= :end)
            """
        ),
        parameters,
    )
    return dict(result.mappings().one())
