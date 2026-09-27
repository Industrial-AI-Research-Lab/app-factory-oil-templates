#!/usr/bin/env python3
"""Seed a throwaway local model DB for Planning MCP level-3 checks.

Creates ``res_model`` / ``time_model`` (the exact tables
``src/stairs_storage/adapter.py`` reads) and inserts declarative JSON
``planning-model/v1`` rows. Numbers are made up: this validates plumbing
and contracts, NOT estimate quality (that needs backend-trained models).

Usage:
    pip install psycopg2-binary
    DEMO_DSN="postgresql://plan:plan123@127.0.0.1:5432/models" python seed_demo_db.py

Work names must match the demo CSV given to schedule_project.
"""

from __future__ import annotations

import json
import os
import sys

DSN = os.environ.get("DEMO_DSN", "postgresql://plan:plan123@127.0.0.1:5432/models")

# (name, measurement, low, mid, high counts, days_per_unit low, mid, high).
# Keep in sync with demo_project.csv. Distinct numbers per work so that
# mixed-up rows are visible in the resulting schedule.
# Category '' covers plain rows; extra categories cover granular_unit rows
# (model lookup is exact on category, no fallback — like the backend).
WORKS = (
    ("Site preparation", "m2", (2, 4, 6), (0.2, 0.5, 1.0), ("",)),
    ("Foundation pouring", "m3", (1, 2, 3), (0.5, 1.0, 2.0), ("",)),
    ("Walls erection", "m2", (3, 5, 8), (0.3, 0.6, 1.2), ("",)),
    ("Roofing", "m2", (2, 3, 5), (0.4, 0.8, 1.5), ("",)),
    ("Finishing works", "m2", (4, 6, 10), (0.1, 0.25, 0.5), ("",)),
    # Granular (etalon) names override activity names in model lookup
    # (graph_adapter._model_name_str prefers enriched fields).
    ("Finishing", "m2", (4, 6, 10), (0.1, 0.25, 0.5), ("civil",)),
)


def main() -> None:
    try:
        import psycopg2
    except ImportError:
        print("need psycopg2-binary: pip install psycopg2-binary", file=sys.stderr)
        raise SystemExit(2)
    ddl_res = (
        "CREATE TABLE IF NOT EXISTS res_model (id SERIAL PRIMARY KEY, model_type VARCHAR, "
        "name VARCHAR, data BYTEA, measurement_type VARCHAR, category VARCHAR)"
    )
    ddl_time = (
        "CREATE TABLE IF NOT EXISTS time_model (id SERIAL PRIMARY KEY, model_type VARCHAR, "
        "name VARCHAR, data BYTEA, measurement_type VARCHAR, category VARCHAR)"
    )
    conn = psycopg2.connect(DSN)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(ddl_res)
                cur.execute(ddl_time)
                for name, meas, counts, days, categories in WORKS:
                    blobs = (
                        ("res_model", {"schema": "planning-model/v1", "resources": [{"name": "crew", "counts": list(counts)}]}),
                        ("time_model", {"schema": "planning-model/v1", "days_per_unit": list(days)}),
                    )
                    for table, blob in blobs:
                        for category in categories:
                            cur.execute(
                                f"DELETE FROM {table} WHERE model_type='standard' AND name=%s "
                                "AND measurement_type=%s AND category=%s",
                                (name, meas, category),
                            )
                            cur.execute(
                                f"INSERT INTO {table} (model_type, name, data, measurement_type, category) "
                                "VALUES ('standard', %s, %s, %s, %s)",
                                (name, json.dumps(blob).encode("utf-8"), meas, category),
                            )
    finally:
        conn.close()
    print(f"seeded {len(WORKS)} works (res+perf) at {DSN.split('@')[-1]}")


if __name__ == "__main__":
    main()
