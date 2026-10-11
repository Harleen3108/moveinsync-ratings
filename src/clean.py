"""Clean and merge raw review snapshots into one analysis-ready table.

Usage (project root, venv active):
    python src/clean.py

Reads   data/raw/reviews_*.csv        every snapshot, every job
Writes  data/processed/reviews_clean.csv
        data/processed/clean_summary.json

Key ideas (defend these in the interview):
  * De-duplicate by reviewId; if a review appears in several snapshots keep the newest copy.
  * Remember WHICH reviews came from the unbiased 'newest_all' job (in_newest_all).
    The 1-star / 2-star jobs oversample complaints on purpose, so rates (e.g. "% of
    reviews that are 1-star") must be computed only on the unbiased subset.
  * Fail loudly: empty data or a stale newest review is reported, never hidden.
"""
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clean")


def load_raw() -> pd.DataFrame:
    files = sorted(RAW_DIR.glob("reviews_*.csv"))
    if not files:
        raise FileNotFoundError(f"No raw review files in {RAW_DIR}. Run collect.py first.")
    frames = []
    for f in files:
        df = pd.read_csv(f, parse_dates=["at", "repliedAt"])
        log.info("Loaded %-55s %6d rows", f.name, len(df))
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    raw = load_raw()
    n_raw = len(raw)

    # Which reviews belong to the unbiased sample (all stars, newest first)?
    all_rows = raw[raw["job"] == "newest_all"]
    in_all_ids = set(all_rows["reviewId"])
    window_start = all_rows["at"].min()

    # De-duplicate: newest snapshot wins.
    raw = raw.sort_values("collected_at_utc").drop_duplicates("reviewId", keep="last")
    n_dedup = len(raw)

    df = raw.copy()
    df["in_newest_all"] = df["reviewId"].isin(in_all_ids)
    df["in_all_window"] = df["at"] >= window_start

    # Text normalisation
    df["content"] = (df["content"].fillna("").astype(str)
                     .str.replace(r"\s+", " ", regex=True).str.strip())
    df["has_text"] = df["content"].str.len() > 0
    df["text_len"] = df["content"].str.len()
    df["word_count"] = df["content"].str.split().str.len().fillna(0).astype(int)
    df["non_latin"] = df["content"].str.contains(r"[^\x00-\x7F]", regex=True)

    # Version: review-time version, fall back to appVersion; keep major.minor for grouping
    df["version"] = df["reviewCreatedVersion"].fillna(df["appVersion"])
    df["version_mm"] = (df["version"].astype(str).str.extract(r"^(\d+\.\d+)", expand=False)
                        .fillna("unknown"))

    # Time helpers
    df["month"] = df["at"].dt.to_period("M").astype(str)
    df["week"] = df["at"].dt.strftime("%G-W%V")

    # Developer replies
    df["has_reply"] = df["replyContent"].notna()
    df["reply_days"] = (df["repliedAt"] - df["at"]).dt.total_seconds() / 86400
    df.loc[df["reply_days"] < 0, "reply_days"] = pd.NA

    # Possible copy-paste / spam: same text and score, 3+ words, seen more than once
    dup_mask = df["has_text"] & (df["word_count"] >= 3) & df.duplicated(["content", "score"], keep=False)
    df["dup_text"] = dup_mask

    df = df.sort_values("at", ascending=False).reset_index(drop=True)

    # Staleness check
    newest = df["at"].max()
    age_days = (pd.Timestamp.now() - newest).days
    stale = age_days > cfg["stale_after_days"]

    unb = df[df["in_newest_all"]]
    summary = {
        "rows_raw_files": int(n_raw),
        "rows_after_dedupe": int(n_dedup),
        "duplicates_removed": int(n_raw - n_dedup),
        "rows_with_text": int(df["has_text"].sum()),
        "rows_empty_text": int((~df["has_text"]).sum()),
        "rows_in_unbiased_sample": int(len(unb)),
        "unbiased_window_start": str(window_start),
        "newest_review": str(newest),
        "oldest_review": str(df["at"].min()),
        "newest_age_days": int(age_days),
        "stale": bool(stale),
        "unbiased_score_mix_pct": (unb["score"].value_counts(normalize=True).sort_index() * 100).round(1).to_dict(),
        "reply_rate_pct_all": round(df["has_reply"].mean() * 100, 1),
        "short_reviews_le3_words_pct": round((df["word_count"] <= 3).mean() * 100, 1),
        "non_latin_text_pct": round(df["non_latin"].mean() * 100, 1),
        "possible_duplicate_text_rows": int(df["dup_text"].sum()),
        "rows_by_star": df["score"].value_counts().sort_index().to_dict(),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "reviews_clean.csv", index=False, encoding="utf-8")
    (OUT_DIR / "clean_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    log.info("Raw rows %d -> after de-dupe %d (removed %d)", n_raw, n_dedup, n_raw - n_dedup)
    log.info("Unbiased sample: %d rows from %s onward", len(unb), window_start)
    log.info("Score mix in unbiased sample (%%): %s", summary["unbiased_score_mix_pct"])
    log.info("Developer reply rate (all rows): %s%%", summary["reply_rate_pct_all"])
    log.info("Newest review: %s (%d days old)", newest, age_days)
    if stale:
        log.warning("STALE: newest review is older than %d days. Re-run collect.py.", cfg["stale_after_days"])
    log.info("Wrote %s", OUT_DIR / "reviews_clean.csv")
    return 2 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
