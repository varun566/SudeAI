"""Create telemetry records table.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-29 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("server", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("packet_loss_pct", sa.Float(), nullable=False),
        sa.Column("throughput_mbps", sa.Float(), nullable=False),
        sa.Column("jitter_ms", sa.Float(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_telemetry_latency_nonnegative"),
        sa.CheckConstraint(
            "packet_loss_pct >= 0 AND packet_loss_pct <= 100",
            name="ck_telemetry_packet_loss_range",
        ),
        sa.CheckConstraint("throughput_mbps >= 0", name="ck_telemetry_throughput_nonnegative"),
        sa.CheckConstraint(
            "jitter_ms IS NULL OR jitter_ms >= 0", name="ck_telemetry_jitter_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_telemetry_observed_at", "telemetry_records", ["observed_at"])
    op.create_index(
        "ix_telemetry_path_observed", "telemetry_records", ["source", "destination", "observed_at"]
    )
    op.create_index("ix_telemetry_server_observed", "telemetry_records", ["server", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_telemetry_server_observed", table_name="telemetry_records")
    op.drop_index("ix_telemetry_path_observed", table_name="telemetry_records")
    op.drop_index("ix_telemetry_observed_at", table_name="telemetry_records")
    op.drop_table("telemetry_records")
