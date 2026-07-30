# Network Telemetry Analytics Service

A production-style FastAPI service for ingesting latency, packet loss, throughput, and jitter measurements; storing them in PostgreSQL; and exposing SQL-backed trend and anomaly analytics. It includes local Docker Compose observability and Minikube-ready Kubernetes resources.

## What is included

- FastAPI API with OpenAPI documentation at `/docs`
- PostgreSQL persistence, Alembic migration, integrity constraints, and analytics indexes
- Single and batch telemetry ingestion (up to 1,000 records per request)
- SQL trend aggregation with `date_bin`, p95 latency, and grouped network-path filters
- SQL rolling-window anomaly detection using PostgreSQL window functions and z-scores
- Prometheus metrics for request rate, latency, errors, and ingestion outcomes
- Pre-provisioned Grafana dashboard
- Docker Compose development stack and Minikube manifests with probes, resources, rolling updates, and a migration Job

## Architecture

```text
Telemetry agents --> FastAPI --> PostgreSQL
                       |             |
                       v             v
                  Prometheus      Analytics API
                       |
                       v
                    Grafana
```

## Quick start with Docker Compose

```bash
cd network-telemetry-analytics
docker compose up --build
```

The API migration runs before Uvicorn starts. Open:

- API docs: <http://localhost:8000/docs>
- Prometheus: <http://localhost:9090>
- Grafana: <http://localhost:3000> (`admin` / `admin` for local use)

Load a representative dataset in a second terminal:

```bash
python3 scripts/generate_sample_data.py --count 180
```

Stop the stack with `docker compose down`. Add `-v` only when you intentionally want to remove local database and monitoring data.

## API usage

Create one measurement:

```bash
curl -X POST http://localhost:8000/api/v1/telemetry \
  -H 'Content-Type: application/json' \
  -d '{
    "observed_at":"2026-07-29T12:00:00Z",
    "server":"edge-1",
    "source":"us-east-1",
    "destination":"eu-west-1",
    "protocol":"tcp",
    "latency_ms":42.8,
    "packet_loss_pct":0.2,
    "throughput_mbps":920.4,
    "jitter_ms":2.1,
    "tags":{"provider":"isp-a","environment":"staging"}
  }'
```

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/telemetry` | Persist one validated telemetry record. |
| `POST /api/v1/telemetry/batch` | Persist 1–1,000 records atomically. |
| `GET /api/v1/telemetry` | Retrieve raw records with source/destination/time filters. |
| `GET /api/v1/analytics/trends` | Bucketed latency, packet-loss, and throughput trends. |
| `GET /api/v1/analytics/anomalies` | Latency z-score and packet-loss anomaly flags. |
| `GET /api/v1/analytics/summary` | Aggregate service-quality summary. |
| `GET /health/live` / `GET /health/ready` | Kubernetes liveness and database readiness. |
| `GET /metrics` | Prometheus metrics. |

Example analytics requests:

```bash
curl 'http://localhost:8000/api/v1/analytics/trends?source=us-east-1&bucket=5m'
curl 'http://localhost:8000/api/v1/analytics/anomalies?window=15m&z_score_threshold=3'
curl 'http://localhost:8000/api/v1/analytics/summary?high_latency_threshold=200'
```

## Analytics behavior

Trends are grouped with PostgreSQL `date_bin` and include average/p95 latency, average packet loss, and average throughput. Supported buckets are `1m`, `5m`, `15m`, `1h`, and `1d`.

Anomaly detection partitions records by `source` and `destination`, then calculates a rolling average and sample standard deviation over `5m`, `15m`, `1h`, or `6h`. The current record is excluded from its baseline. A record is flagged when its absolute latency z-score meets the threshold (default `3.0`) or its packet loss meets the configured threshold (default `2%`). Three prior samples are required before latency is evaluated. This makes the outcome inspectable through returned baseline values rather than an opaque score.

## Run without Docker

Install Python 3.12+, create an environment, then install the project:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

You need a reachable PostgreSQL 16+ database matching `DATABASE_URL`. PostgreSQL 14+ is required for the `date_bin` trend query.

## Kubernetes / Minikube deployment

The manifests use a two-replica API Deployment with zero-unavailable rolling updates, readiness/liveness probes, CPU/memory requests and limits, a PostgreSQL StatefulSet, and an explicit migration Job.

```bash
minikube start
eval $(minikube docker-env)
docker build -t network-telemetry-api:latest .

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml -f k8s/secret.yaml -f k8s/postgres.yaml
kubectl -n telemetry rollout status statefulset/telemetry-postgres --timeout=120s

kubectl apply -f k8s/migration-job.yaml
kubectl -n telemetry wait --for=condition=complete job/telemetry-migrate --timeout=120s

kubectl apply -f k8s/api.yaml
kubectl -n telemetry rollout status deployment/telemetry-api --timeout=120s
kubectl -n telemetry port-forward service/telemetry-api 8000:8000
```

In a production-like environment, replace `k8s/secret.yaml` with externally managed credentials before deployment. To roll out a new locally built image, rebuild it and run `kubectl -n telemetry rollout restart deployment/telemetry-api`.

### Kubernetes monitoring

Install the Prometheus Operator stack, then apply the included `ServiceMonitor` (the label matches the Helm release name used below):

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace
kubectl apply -f k8s/servicemonitor.yaml
```

The Grafana dashboard is at `monitoring/grafana/dashboards/network-telemetry-overview.json`. Import it in the Grafana UI or provision it through the chart's dashboard sidecar. Its panels cover request rate by status, p95 API request duration, 5xx rate, and ingestion/failure rate.

## Verification and quality checks

```bash
ruff check .
pytest
docker compose config --quiet
kubectl apply --dry-run=client -f k8s/namespace.yaml -f k8s/configmap.yaml -f k8s/secret.yaml -f k8s/postgres.yaml -f k8s/migration-job.yaml -f k8s/api.yaml
```

## Project structure

```text
app/           FastAPI routes, models, SQL analytics, metrics, and configuration
migrations/    Alembic schema history
k8s/           Minikube-ready API, database, migration, and scrape manifests
monitoring/    Prometheus config and Grafana provisioning/dashboard assets
scripts/       Sample telemetry generator
tests/         API contract and validation tests
```
