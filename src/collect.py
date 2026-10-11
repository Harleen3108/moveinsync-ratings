"""Collect public Play Store reviews + app metadata into dated raw snapshots.

Usage (from the project root, venv active):
    python src/collect.py --smoke    # tiny test run (200 reviews per job)
    python src/collect.py --refresh  # weekly update: only the unbiased newest_all job, newest N reviews
    python src/collect.py            # full run from config.yaml

Raw files are never overwritten: every run writes new files stamped with a UTC time.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from google_play_scraper import Sort, app, reviews

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"

# Columns we keep. userName/userImage are dropped on purpose (privacy; not needed).
KEEP = ["reviewId", "content", "score", "thumbsUpCount", "reviewCreatedVersion",
        "at", "replyContent", "repliedAt", "appVersion"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect")


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_metadata(cfg: dict, stamp: str) -> dict:
    meta = app(cfg["app_id"], lang=cfg["lang"], country=cfg["country"])
    wanted = ["title", "developer", "score", "ratings", "reviews", "histogram",
              "version", "updated", "installs", "appId"]
    snap = {k: meta.get(k) for k in wanted}
    snap["collected_at_utc"] = stamp
    path = RAW_DIR / f"app_meta_{stamp}.json"
    path.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    log.info("Saved metadata -> %s", path.name)
    return snap


def fetch_job(cfg: dict, score, max_reviews: int) -> list:
    """Page through reviews (newest first) until max_reviews or no more data."""
    out, token = [], None
    while len(out) < max_reviews:
        n = min(cfg["batch_size"], max_reviews - len(out))
        for attempt in range(cfg["retries"]):
            try:
                batch, token = reviews(
                    cfg["app_id"], lang=cfg["lang"], country=cfg["country"],
                    sort=Sort.NEWEST, count=n, filter_score_with=score,
                    continuation_token=token)
                break
            except Exception as exc:  # network / rate-limit / parse errors
                wait = 2 ** attempt
                log.warning("Fetch failed (%s). Retry %d/%d in %ds",
                            exc, attempt + 1, cfg["retries"], wait)
                time.sleep(wait)
        else:
            raise RuntimeError("Fetch failed after all retries; refusing to continue silently.")
        if not batch:
            break
        out.extend(batch)
        log.info("  fetched %d / %d", len(out), max_reviews)
        if token is None:
            break
        time.sleep(cfg["sleep_seconds"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="small test run")
    ap.add_argument("--refresh", action="store_true",
                    help="weekly mode: only the newest_all job, capped at refresh_max_reviews")
    args = ap.parse_args()

    cfg = load_config()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    meta = save_metadata(cfg, stamp)
    log.info("Listing says: score=%s ratings=%s reviews=%s version=%s",
             meta["score"], meta["ratings"], meta["reviews"], meta["version"])
    log.info("Histogram (1..5 stars): %s", meta["histogram"])

    total = 0
    jobs = [j for j in cfg["jobs"] if j["name"] == "newest_all"] if args.refresh else cfg["jobs"]
    for job in jobs:
        cap = 200 if args.smoke else job["max_reviews"]
        if args.refresh and not args.smoke:
            cap = int(cfg.get("refresh_max_reviews", 2000))
        log.info("Job %s (score=%s, max=%d)", job["name"], job["score"], cap)
        rows = fetch_job(cfg, job["score"], cap)
        if not rows:
            log.error("Job %s returned 0 rows.", job["name"])
            return 1
        df = pd.DataFrame(rows).reindex(columns=KEEP)
        df["job"] = job["name"]
        df["collected_at_utc"] = stamp
        path = RAW_DIR / f"reviews_{job['name']}_{stamp}.csv"
        df.to_csv(path, index=False, encoding="utf-8")
        total += len(df)
        newest, oldest = df["at"].max(), df["at"].min()
        age_days = (pd.Timestamp.now() - pd.Timestamp(newest)).days
        log.info("  saved %d rows -> %s | oldest=%s newest=%s", len(df), path.name, oldest, newest)
        if age_days > cfg["stale_after_days"]:
            log.warning("  STALE: newest review is %d days old", age_days)

    log.info("Done. %d rows across %d jobs. Timestamp: %s", total, len(jobs), stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
