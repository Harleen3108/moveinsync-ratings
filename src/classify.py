"""Tag every review with themes using transparent keyword rules (themes.yaml).

Usage (project root, venv active):
    python src/classify.py              # label + summary table
    python src/classify.py --examples   # also print 4 random matches per theme (precision check)

Reads   data/processed/reviews_clean.csv, themes.yaml
Writes  data/processed/reviews_labeled.csv
        reports/theme_summary.csv

Multi-label: one review can hit several themes ("app crashes AND helpdesk is rude").
Rates are computed ONLY on the unbiased sample (in_newest_all) of 1-2 star reviews that have
text. Reviews that match no theme are reported as 'unspecified', never silently dropped.
"""
import argparse
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("classify")


def compile_rx(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", action="store_true")
    args = ap.parse_args()

    with open(ROOT / "themes.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    themes = cfg["themes"]
    rx = {t["id"]: compile_rx(t["patterns"]) for t in themes}
    generic_rx = compile_rx(cfg["generic_negative_patterns"])
    update_rx = re.compile(cfg["update_mention_pattern"], re.IGNORECASE)

    df = pd.read_csv(PROC / "reviews_clean.csv", parse_dates=["at", "repliedAt"])
    df["content"] = df["content"].fillna("").astype(str)

    for t in themes:
        df[f"t_{t['id']}"] = df["content"].map(lambda s, r=rx[t["id"]]: bool(r.search(s)))
    tcols = [f"t_{t['id']}" for t in themes]

    df["n_themes"] = df[tcols].sum(axis=1)
    df["themes"] = df[tcols].apply(lambda r: "|".join(c[2:] for c in tcols if r[c]), axis=1)
    owner_of = {t["id"]: t["owner"] for t in themes}
    df["unspecified"] = df["has_text"] & (df["n_themes"] == 0)
    df["generic_only"] = df["unspecified"] & df["content"].map(lambda s: bool(generic_rx.search(s)))
    df["mentions_update"] = df["content"].map(lambda s: bool(update_rx.search(s)))

    df.to_csv(PROC / "reviews_labeled.csv", index=False, encoding="utf-8")

    # ---- summary on the unbiased sample, 1-2 star, with text ----
    neg = df[df["in_newest_all"] & (df["score"] <= 2) & df["has_text"]]
    n = len(neg)
    rows = []
    for t in themes:
        c = f"t_{t['id']}"
        k = int(neg[c].sum())
        rows.append({
            "theme": t["id"], "label": t["label"], "stage": t["stage"], "owner": t["owner"],
            "severity": t["severity"], "reviews": k, "pct_of_negative": round(k / n * 100, 1),
            "pct_1star_only": round(neg.loc[neg["score"] == 1, c].mean() * 100, 1),
        })
    summ = pd.DataFrame(rows).sort_values("reviews", ascending=False)
    REPORTS.mkdir(exist_ok=True)
    summ.to_csv(REPORTS / "theme_summary.csv", index=False)

    log.info("Negative (1-2 star) reviews with text in the unbiased sample: %d", n)
    print(summ[["theme", "reviews", "pct_of_negative", "severity", "owner"]].to_string(index=False))
    uns = int(neg["unspecified"].sum())
    gen = int(neg["generic_only"].sum())
    log.info("No theme matched: %d (%.1f%%) | of those, only generic abuse ('worst app'): %d",
             uns, uns / n * 100, gen)
    log.info("Average themes per negative review: %.2f | mention 'update': %.1f%%",
             neg["n_themes"].mean(), neg["mentions_update"].mean() * 100)

    if args.examples:
        for t in themes:
            sub = neg[neg[f"t_{t['id']}"]]
            print(f"\n=== {t['id']} ({len(sub)}) ===")
            for _, r in sub.sample(min(4, len(sub)), random_state=11).iterrows():
                print(f"  [{r['score']}*] {r['content'][:150]}")
        sub = neg[neg["unspecified"] & ~neg["generic_only"] & (neg["word_count"] >= 4)]
        print(f"\n=== UNSPECIFIED, not generic, >=4 words ({len(sub)}) ===")
        for _, r in sub.sample(min(15, len(sub)), random_state=11).iterrows():
            print(f"  [{r['score']}*] {r['content'][:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
