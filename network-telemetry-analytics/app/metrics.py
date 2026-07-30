from time import perf_counter

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "telemetry_api_requests_total",
    "Number of HTTP requests served by the API.",
    ("method", "path", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "telemetry_api_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "path"),
)
TELEMETRY_INGESTED = Counter(
    "telemetry_records_ingested_total", "Number of telemetry records successfully persisted."
)
TELEMETRY_INGEST_FAILURES = Counter(
    "telemetry_ingest_failures_total", "Number of failed telemetry ingestion operations."
)


def instrument_app(app: FastAPI) -> None:
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.middleware("http")
    async def collect_request_metrics(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        started = perf_counter()
        status_code = 500
        path = request.url.path
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            HTTP_REQUESTS.labels(request.method, path, status_code).inc()
            HTTP_REQUEST_DURATION.labels(request.method, path).observe(perf_counter() - started)
