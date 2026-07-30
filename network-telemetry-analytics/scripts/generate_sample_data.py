"""Send realistic baseline telemetry plus periodic anomalies to a running API."""

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen


def build_records(count: int) -> list[dict[str, object]]:
    random.seed(42)
    now = datetime.now(UTC)
    records: list[dict[str, object]] = []
    for index in range(count):
        is_anomaly = index % 17 == 0
        records.append(
            {
                "observed_at": (now - timedelta(seconds=(count - index) * 10)).isoformat(),
                "server": f"edge-{index % 3 + 1}",
                "source": "us-east-1",
                "destination": "eu-west-1" if index % 2 else "us-west-2",
                "protocol": "tcp",
                "latency_ms": round(random.gauss(42, 5) + (150 if is_anomaly else 0), 2),
                "packet_loss_pct": round(
                    4.5 if is_anomaly else max(0, random.gauss(0.15, 0.08)), 3
                ),
                "throughput_mbps": round(max(1, random.gauss(850, 60)), 2),
                "jitter_ms": round(max(0, random.gauss(2.5, 0.8)), 2),
                "tags": {"region": "test", "generator": "sample-data"},
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://localhost:8000/api/v1/telemetry/batch")
    parser.add_argument("--count", type=int, default=120)
    args = parser.parse_args()

    if not 1 <= args.count <= 1_000:
        parser.error("--count must be from 1 through 1000")
    payload = json.dumps({"records": build_records(args.count)}).encode()
    request = Request(
        args.endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - endpoint is an explicit CLI argument
        print(f"Inserted {json.load(response)['inserted']} telemetry records.")


if __name__ == "__main__":
    main()
