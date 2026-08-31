import math
from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev


def _metrics(rows, now, seconds):
    start = now - timedelta(seconds=seconds)
    in_window = [row for row in rows if start <= datetime.fromisoformat(row["observed_at"]) <= now and row["supply_apy"] is not None]
    valid = [float(row["supply_apy"]) for row in in_window if math.isfinite(float(row["supply_apy"]))]
    filtered = [float(row["supply_apy"]) for row in in_window if "anomalous_supply_apy" not in (row["quality_flags"] or "") and math.isfinite(float(row["supply_apy"]))]
    timestamps = sorted(datetime.fromisoformat(row["observed_at"]) for row in in_window)
    coverage_seconds = max(0.0, (timestamps[-1] - timestamps[0]).total_seconds()) if len(timestamps) > 1 else 0.0
    result = {
        "samples_count": len(valid), "coverage_seconds": coverage_seconds,
        "coverage_pct": min(100.0, coverage_seconds / seconds * 100.0) if seconds else 0.0,
        "raw_avg": mean(valid) if valid else None, "filtered_avg": mean(filtered) if filtered else None,
        "raw_median": median(valid) if valid else None, "filtered_median": median(filtered) if filtered else None,
        "raw_min": min(valid) if valid else None, "raw_max": max(valid) if valid else None,
        "raw_stddev": pstdev(valid) if len(valid) > 1 else 0.0 if valid else None,
        "min": min(filtered) if filtered else None, "max": max(filtered) if filtered else None,
        "stddev": pstdev(filtered) if len(filtered) > 1 else 0.0 if filtered else None,
        "filtered_samples_count": len(filtered),
    }
    return result


def supply_apy_history(rows, now=None):
    now = now or datetime.now(timezone.utc)
    latest = max((row["supply_apy"] for row in rows if row["supply_apy"] is not None), default=None)
    return {"current": latest, "24h": _metrics(rows, now, 24 * 3600), "7d": _metrics(rows, now, 7 * 24 * 3600), "30d": _metrics(rows, now, 30 * 24 * 3600)}
