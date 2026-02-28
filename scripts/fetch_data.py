#!/usr/bin/env python3
"""
Fetch MASE and SCALED_CRPS data from the Impermanent Leaderboard HuggingFace Space.

Request 1: GET /config -> extract MASE dataframe from components
Request 2: POST /gradio_api/call/build_table with "scaled_crps" -> SSE poll for CRPS data

Outputs: data/leaderboard.json
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

BASE_URL = "https://timecopilot-impermanentleaderboard.hf.space"

MODELS = [
    "AutoARIMA", "AutoCES", "AutoETS", "Chronos",
    "DynamicOptimizedTheta", "HistoricAverage", "Moirai",
    "Prophet", "SeasonalNaive", "TiRex", "TimesFM", "ZeroModel"
]


def fetch_mase_data():
    """Fetch MASE data from the /config endpoint."""
    print("Fetching MASE data from /config...")
    resp = requests.get(f"{BASE_URL}/config", timeout=60)
    resp.raise_for_status()
    config = resp.json()

    # Find the MASE dataframe — it's the one with model columns (15 cols),
    # not the summary table (5 cols)
    dataframe = None
    for comp in config.get("components", []):
        comp_type = comp.get("component") or comp.get("type")
        if comp_type == "dataframe":
            val = comp.get("props", {}).get("value")
            if val and "headers" in val and "data" in val:
                # The MASE data table has 15 columns (subdataset, frequency, cutoff + 12 models)
                if len(val["headers"]) >= 15:
                    dataframe = val
                    break

    if not dataframe:
        print("ERROR: Could not find dataframe in /config response", file=sys.stderr)
        sys.exit(1)

    headers = dataframe["headers"]
    rows = dataframe["data"]
    print(f"  Found {len(rows)} MASE rows with {len(headers)} columns")
    return headers, rows


def fetch_crps_data():
    """Fetch SCALED_CRPS data via the Gradio queue API (two-step: submit -> poll)."""
    print("Fetching SCALED_CRPS data via /gradio_api/call/build_table...")

    # Step 1: Submit
    payload = {
        "data": [
            "scaled_crps",
            "All",
            "All",
            MODELS
        ]
    }
    resp = requests.post(
        f"{BASE_URL}/gradio_api/call/build_table",
        json=payload,
        timeout=60
    )
    resp.raise_for_status()
    event_id = resp.json().get("event_id")
    if not event_id:
        print("ERROR: No event_id returned", file=sys.stderr)
        sys.exit(1)
    print(f"  Got event_id: {event_id}")

    # Step 2: Poll for result (SSE stream)
    time.sleep(1)  # Brief pause before polling
    result_url = f"{BASE_URL}/gradio_api/call/build_table/{event_id}"

    for attempt in range(10):
        resp = requests.get(result_url, timeout=60, stream=True)
        resp.raise_for_status()

        content = resp.text
        # Parse SSE: look for "event: complete" followed by "data: ..."
        for line in content.split("\n"):
            if line.startswith("data: "):
                data_str = line[6:]
                try:
                    result = json.loads(data_str)
                    if isinstance(result, list) and len(result) > 0:
                        df = result[0]
                        if "headers" in df and "data" in df:
                            print(f"  Found {len(df['data'])} CRPS rows")
                            return df["headers"], df["data"]
                except json.JSONDecodeError:
                    continue

        print(f"  Attempt {attempt + 1}/10: waiting for result...")
        time.sleep(3)

    print("ERROR: Timed out waiting for CRPS data", file=sys.stderr)
    sys.exit(1)


def parse_rows(headers, raw_rows):
    """Convert raw table rows into structured records."""
    # headers: ["subdataset", "frequency", "cutoff", "AutoARIMA", ...]
    model_cols = headers[3:]  # model names start at index 3
    records = []

    for row in raw_rows:
        subdataset = row[0]
        frequency = row[1]
        cutoff = row[2]
        values = {}
        for i, model in enumerate(model_cols):
            val = row[3 + i]
            values[model] = val  # can be float or None

        records.append({
            "subdataset": subdataset,
            "frequency": frequency,
            "cutoff": cutoff,
            "values": values
        })

    return records, model_cols


def compute_summary(mase_records, crps_records, models):
    """Compute summary stats: average metric and average rank per model."""
    def avg_metric_and_rank(records, models):
        # Per-model: collect all values
        model_vals = {m: [] for m in models}
        for r in records:
            for m in models:
                v = r["values"].get(m)
                if v is not None:
                    model_vals[m].append(v)

        avg_metric = {}
        for m in models:
            vals = model_vals[m]
            avg_metric[m] = sum(vals) / len(vals) if vals else None

        # Compute average rank across all rows
        rank_accum = {m: 0 for m in models}
        rank_count = {m: 0 for m in models}

        for r in records:
            vals = [(m, r["values"].get(m)) for m in models]
            vals = [(m, v) for m, v in vals if v is not None]
            vals.sort(key=lambda x: x[1])
            for rank_idx, (m, _) in enumerate(vals):
                rank_accum[m] += rank_idx + 1
                rank_count[m] += 1

        avg_rank = {}
        for m in models:
            avg_rank[m] = rank_accum[m] / rank_count[m] if rank_count[m] > 0 else None

        return avg_metric, avg_rank

    mase_avg, mase_rank = avg_metric_and_rank(mase_records, models)
    crps_avg, crps_rank = avg_metric_and_rank(crps_records, models)

    # Build summary sorted by a combined score (avg of MASE rank + CRPS rank)
    summary = []
    for m in models:
        combined = 0
        count = 0
        if mase_rank[m] is not None:
            combined += mase_rank[m]
            count += 1
        if crps_rank[m] is not None:
            combined += crps_rank[m]
            count += 1
        combined_rank = combined / count if count > 0 else 999

        summary.append({
            "model": m,
            "avg_mase": round(mase_avg[m], 3) if mase_avg[m] is not None else None,
            "avg_crps": round(crps_avg[m], 3) if crps_avg[m] is not None else None,
            "rank_mase": round(mase_rank[m], 3) if mase_rank[m] is not None else None,
            "rank_crps": round(crps_rank[m], 3) if crps_rank[m] is not None else None,
            "_combined": combined_rank
        })

    summary.sort(key=lambda x: x["_combined"])

    # Add medal emojis to top 3
    medals = ["\U0001f947", "\U0001f948", "\U0001f949"]  # gold, silver, bronze
    for i, s in enumerate(summary):
        if i < 3:
            s["model"] = f"{medals[i]} {s['model']}"
        del s["_combined"]

    return summary


def main():
    # Determine output path relative to repo root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    output_path = os.path.join(repo_root, "data", "leaderboard.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Fetch both datasets
    mase_headers, mase_raw = fetch_mase_data()
    crps_headers, crps_raw = fetch_crps_data()

    # Parse into structured records
    mase_records, mase_models = parse_rows(mase_headers, mase_raw)
    crps_records, crps_models = parse_rows(crps_headers, crps_raw)

    # Use MODELS constant as authoritative list
    models = MODELS

    # Filter to last 3 months only
    all_cutoffs = sorted(set(r["cutoff"] for r in mase_records + crps_records))
    if all_cutoffs:
        # Parse the latest cutoff date: "2026-02-12-00" → datetime
        latest_parts = all_cutoffs[-1].split("-")
        latest_date = datetime(int(latest_parts[0]), int(latest_parts[1]), int(latest_parts[2]))
        cutoff_threshold = latest_date - timedelta(days=90)

        def is_within_3_months(cutoff_str):
            parts = cutoff_str.split("-")
            dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
            return dt >= cutoff_threshold

        before_mase = len(mase_records)
        before_crps = len(crps_records)
        mase_records = [r for r in mase_records if is_within_3_months(r["cutoff"])]
        crps_records = [r for r in crps_records if is_within_3_months(r["cutoff"])]
        print(f"  Filtered to last 3 months (since {cutoff_threshold.strftime('%Y-%m-%d')}):")
        print(f"    MASE: {before_mase} → {len(mase_records)} rows")
        print(f"    CRPS: {before_crps} → {len(crps_records)} rows")

    # Extract unique cutoffs, subdatasets, frequencies
    all_records = mase_records + crps_records
    cutoffs = sorted(set(r["cutoff"] for r in all_records))
    subdatasets = sorted(set(r["subdataset"] for r in all_records))
    frequencies = sorted(set(r["frequency"] for r in all_records))

    # Compute summary
    summary = compute_summary(mase_records, crps_records, models)

    # Build final DATA object
    data = {
        "models": models,
        "cutoffs": cutoffs,
        "subdatasets": subdatasets,
        "frequencies": frequencies,
        "mase": mase_records,
        "crps": crps_records,
        "summary": summary
    }

    with open(output_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    file_size = os.path.getsize(output_path)
    print(f"\nSaved to {output_path} ({file_size:,} bytes)")
    print(f"  Models: {len(models)}")
    print(f"  Cutoffs: {len(cutoffs)}")
    print(f"  MASE rows: {len(mase_records)}")
    print(f"  CRPS rows: {len(crps_records)}")


if __name__ == "__main__":
    main()
