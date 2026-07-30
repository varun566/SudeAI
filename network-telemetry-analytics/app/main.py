import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import fetch_anomalies, fetch_summary, fetch_trends
from app.config import get_settings
from app.database import close_database, get_session
from app.metrics import TELEMETRY_INGEST_FAILURES, TELEMETRY_INGESTED, instrument_app
from app.models import TelemetryRecord
from app.schemas import (
    AnomalyResponse,
    BatchIngestRequest,
    BatchIngestResponse,
    SummaryResponse,
    TelemetryCreate,
    TelemetryRead,
    TrendResponse,
)

settings = get_settings()
logging.basicConfig(
    level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("starting %s in %s", settings.app_name, settings.environment)
    yield
    await close_database()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Ingest network measurements and query SQL-powered trends and anomaly signals.",
    lifespan=lifespan,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

instrument_app(app)


def _to_read(record: TelemetryRecord) -> TelemetryRead:
    return TelemetryRead.model_validate(record)


def _analytics_error(error: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


@app.get("/", tags=["service"])
async def service_info() -> dict[str, str]:
    return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("database readiness check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        ) from exc
    return {"status": "ok", "database": "connected"}


@app.post(
    f"{settings.api_prefix}/telemetry",
    response_model=TelemetryRead,
    status_code=status.HTTP_201_CREATED,
    tags=["telemetry"],
)
async def ingest_telemetry(
    payload: TelemetryCreate, session: AsyncSession = Depends(get_session)
) -> TelemetryRead:
    record = TelemetryRecord(**payload.model_dump())
    try:
        session.add(record)
        await session.commit()
        await session.refresh(record)
    except SQLAlchemyError as exc:
        await session.rollback()
        TELEMETRY_INGEST_FAILURES.inc()
        logger.exception("failed to persist telemetry record")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="could not persist telemetry"
        ) from exc
    TELEMETRY_INGESTED.inc()
    return _to_read(record)


@app.post(
    f"{settings.api_prefix}/telemetry/batch",
    response_model=BatchIngestResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["telemetry"],
)
async def ingest_telemetry_batch(
    payload: BatchIngestRequest, session: AsyncSession = Depends(get_session)
) -> BatchIngestResponse:
    records = [TelemetryRecord(**item.model_dump()) for item in payload.records]
    try:
        session.add_all(records)
        await session.commit()
        for record in records:
            await session.refresh(record)
    except SQLAlchemyError as exc:
        await session.rollback()
        TELEMETRY_INGEST_FAILURES.inc()
        logger.exception("failed to persist telemetry batch")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="could not persist telemetry"
        ) from exc
    TELEMETRY_INGESTED.inc(len(records))
    return BatchIngestResponse(
        inserted=len(records), records=[_to_read(record) for record in records]
    )


@app.get(f"{settings.api_prefix}/telemetry", response_model=list[TelemetryRead], tags=["telemetry"])
async def list_telemetry(
    source: str | None = None,
    destination: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1_000),
    session: AsyncSession = Depends(get_session),
) -> list[TelemetryRead]:
    if start and end and start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start must be earlier than end",
        )
    query = select(TelemetryRecord).order_by(TelemetryRecord.observed_at.desc()).limit(limit)
    if source:
        query = query.where(TelemetryRecord.source == source)
    if destination:
        query = query.where(TelemetryRecord.destination == destination)
    if start:
        query = query.where(TelemetryRecord.observed_at >= start)
    if end:
        query = query.where(TelemetryRecord.observed_at <= end)
    records = (await session.scalars(query)).all()
    return [_to_read(record) for record in records]


@app.get(
    f"{settings.api_prefix}/analytics/trends", response_model=TrendResponse, tags=["analytics"]
)
async def trends(
    source: str | None = None,
    destination: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    bucket: str = Query(default="5m", description="One of: 1m, 5m, 15m, 1h, 1d"),
    session: AsyncSession = Depends(get_session),
) -> TrendResponse:
    try:
        points = await fetch_trends(
            session, source=source, destination=destination, start=start, end=end, bucket=bucket
        )
    except ValueError as exc:
        raise _analytics_error(exc) from exc
    return TrendResponse(
        bucket=bucket,
        points=points,
        filters={"source": source, "destination": destination, "start": start, "end": end},
    )


@app.get(
    f"{settings.api_prefix}/analytics/anomalies", response_model=AnomalyResponse, tags=["analytics"]
)
async def anomalies(
    source: str | None = None,
    destination: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    window: str = Query(default="15m", description="One of: 5m, 15m, 1h, 6h"),
    z_score_threshold: float = Query(default=3.0, ge=1.0, le=10.0),
    packet_loss_threshold: float = Query(default=2.0, gt=0, le=100),
    limit: int = Query(default=100, ge=1, le=1_000),
    session: AsyncSession = Depends(get_session),
) -> AnomalyResponse:
    try:
        records = await fetch_anomalies(
            session,
            source=source,
            destination=destination,
            start=start,
            end=end,
            window=window,
            z_score_threshold=z_score_threshold,
            packet_loss_threshold=packet_loss_threshold,
            limit=limit,
        )
    except ValueError as exc:
        raise _analytics_error(exc) from exc
    return AnomalyResponse(
        window=window,
        z_score_threshold=z_score_threshold,
        packet_loss_threshold=packet_loss_threshold,
        anomalies=records,
    )


@app.get(
    f"{settings.api_prefix}/analytics/summary", response_model=SummaryResponse, tags=["analytics"]
)
async def summary(
    source: str | None = None,
    destination: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    high_latency_threshold: float = Query(default=200.0, ge=0),
    packet_loss_threshold: float = Query(default=2.0, gt=0, le=100),
    session: AsyncSession = Depends(get_session),
) -> SummaryResponse:
    try:
        values = await fetch_summary(
            session,
            source=source,
            destination=destination,
            start=start,
            end=end,
            high_latency_threshold=high_latency_threshold,
            packet_loss_threshold=packet_loss_threshold,
        )
    except ValueError as exc:
        raise _analytics_error(exc) from exc
    return SummaryResponse(
        **values,
        filters={"source": source, "destination": destination, "start": start, "end": end},
    )
