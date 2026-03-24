#!/usr/bin/env python3
"""
Fetch evaluation data from S3 parquet and produce data/leaderboard.json.

Source: s3://impermanent-benchmark/v0.1.0/gh-archive/evaluations/evaluation_results.parquet

Outputs: data/leaderboard.json
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import boto3
import pandas as pd

S3_BUCKET = "impermanent-benchmark"
S3_KEY = "v0.1.0/gh-archive/evaluations/evaluation_results.parquet"


def fetch_parquet():
    """Download parquet from S3 and return a DataFrame."""
    print(f"Fetching s3://{S3_BUCKET}/{S3_KEY} ...")
    s3 = boto3.client("s3")
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        s3.download_file(S3_BUCKET, S3_KEY, tmp.name)
        df = pd.read_parquet(tmp.name)
    print(f"  {len(df)} rows, {len(df.columns)} columns")
    return df


def normalize_cutoffs(df):
    """Normalize cutoff strings so '2026-01-01' becomes '2026-01-01-00'."""
    df["cutoff"] = df["cutoff"].apply(lambda c: c + "-00" if len(c) == 10 else c)
    return df


def ensure_sparsity_level(df):
    """Ensure sparsity_level column exists; missing column or NaN → 'low'."""
    if "sparsity_level" not in df.columns:
        out = df.copy()
        out["sparsity_level"] = "low"
        return out
    out = df.copy()
    out["sparsity_level"] = out["sparsity_level"].fillna("low").astype(str)
    return out


def build_records(df, metric_name):
    """Convert rows for a given metric into the leaderboard record format."""
    subset = df[df["metric"] == metric_name]
    records = []
    group_cols = ["subdataset", "frequency", "sparsity_level", "cutoff"]

    for (sub, freq, sparsity, cut), grp in subset.groupby(group_cols):
        values = {}
        for _, row in grp.iterrows():
            v = row["value"]
            if pd.isna(v) or abs(v) >= 1e6:
                values[row["model_alias"]] = None if pd.isna(v) else 0
            else:
                values[row["model_alias"]] = round(v, 3)
        records.append({
            "subdataset": sub,
            "frequency": freq,
            "sparsity_level": sparsity,
            "cutoff": cut,
            "values": values,
        })

    records.sort(
        key=lambda e: (e["subdataset"], e["frequency"], e["sparsity_level"], e["cutoff"])
    )
    return records


def compute_summary(mase_records, crps_records, models):
    """Compute summary stats: average metric and average rank per model."""

    def avg_metric_and_rank(records, models):
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
            "_combined": combined_rank,
        })

    summary.sort(key=lambda x: x["_combined"])

    medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
    for i, s in enumerate(summary):
        if i < 3:
            s["model"] = f"{medals[i]} {s['model']}"
        del s["_combined"]

    return summary


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    output_path = os.path.join(repo_root, "data", "leaderboard.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    df = fetch_parquet()
    df = normalize_cutoffs(df)
    df = ensure_sparsity_level(df)

    # Derive dimensions from data
    models = sorted(df["model_alias"].unique().tolist())

    # Filter to last 3 months
    all_cutoffs = sorted(df["cutoff"].unique().tolist())
    if all_cutoffs:
        latest_parts = all_cutoffs[-1].split("-")
        latest_date = datetime(int(latest_parts[0]), int(latest_parts[1]), int(latest_parts[2]))
        cutoff_threshold = latest_date - timedelta(days=90)

        def is_within_3_months(cutoff_str):
            parts = cutoff_str.split("-")
            return datetime(int(parts[0]), int(parts[1]), int(parts[2])) >= cutoff_threshold

        before = len(df)
        df = df[df["cutoff"].apply(is_within_3_months)]
        print(f"  Filtered to last 3 months (since {cutoff_threshold.strftime('%Y-%m-%d')}): {before} → {len(df)} rows")

    # Build records
    mase_records = build_records(df, "mase")
    crps_records = build_records(df, "scaled_crps")

    # Extract dimensions from filtered data
    all_records = mase_records + crps_records
    cutoffs = sorted(set(r["cutoff"] for r in all_records))
    subdatasets = sorted(set(r["subdataset"] for r in all_records))
    frequencies = sorted(set(r["frequency"] for r in all_records))
    def ordered_sparsity_levels(levels: set) -> list:
        preferred = ["low", "medium", "high"]
        as_set = {str(x) for x in levels}
        out = [x for x in preferred if x in as_set]
        out.extend(sorted(as_set - set(out)))
        return out

    sparsity_levels = ordered_sparsity_levels(
        {r["sparsity_level"] for r in all_records}
    )

    summary = compute_summary(mase_records, crps_records, models)

    data = {
        "models": models,
        "cutoffs": cutoffs,
        "subdatasets": subdatasets,
        "frequencies": frequencies,
        "sparsity_levels": sparsity_levels,
        "mase": mase_records,
        "crps": crps_records,
        "summary": summary,
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
