"""Create the blind hand-labelling sample used to measure how accurate the keyword rules are.

Usage (project root, venv active):
    python src/make_validation_sample.py                      # round 1: all themes
    python src/make_validation_sample.py --round 2 --themes CRASH,SLOW,TRK,OPS,UI,SAFE,SUP,RATE,POL,DCON
        # round 2 = a FRESH sample (new seed, excludes round-1 reviews) for the themes whose
        # rules were changed after round 1. Re-measuring on the same rows would be optimistic,
        # because the rule fixes were written by looking at those rows' errors.

Reads   data/processed/reviews_labeled.csv, themes.yaml
Writes  data/validation/manual_labels.csv   <- YOU fill the 'true_codes' column
        data/validation/sample_key.csv      <- rule output, kept apart so labelling stays blind
        data/validation/LABELLING_GUIDE.txt

Sampling design (so the accuracy numbers are honest):
  * PRECISION sample: up to 8 random reviews that each theme's rule matched. Asking "was the rule
    right?" on a random draw of its matches estimates that theme's precision.
  * RECALL / miss sample: 40 random reviews that NO rule matched. Asking "did they contain a real
    theme?" estimates how much the rules miss.
  * Order is shuffled and rule output is hidden, so you are not nudged toward the rule's answer.
Only reviews from the unbiased sample, 1-2 stars, with text, are used.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
VAL = ROOT / "data" / "validation"
SEED = 2026
PER_THEME = 8
N_UNMATCHED = 40


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite even if labels exist")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--themes", default="", help="comma-separated codes to sample (default: all)")
    args = ap.parse_args()
    rnd = args.round
    sfx = "" if rnd == 1 else f"_r{rnd}"
    seed = SEED + (rnd - 1) * 101
    n_unmatched = N_UNMATCHED if rnd == 1 else 40

    out_path = VAL / f"manual_labels{sfx}.csv"
    if out_path.exists() and not args.force:
        old = pd.read_csv(out_path, encoding="utf-8-sig")
        if old["true_codes"].notna().any():
            print("manual_labels.csv already contains your labels. Refusing to overwrite "
                  "(use --force only if you really want to start over).")
            return 1

    with open(ROOT / "themes.yaml", encoding="utf-8") as f:
        themes = yaml.safe_load(f)["themes"]
    code_of = {t["id"]: t["code"] for t in themes}   # full map, before any --themes filter
    if args.themes:
        want = {c.strip().upper() for c in args.themes.split(",")}
        themes = [t for t in themes if t["code"] in want]

    df = pd.read_csv(PROC / "reviews_labeled.csv", parse_dates=["at"])
    df["content"] = df["content"].fillna("").astype(str)
    pool = df[df["in_newest_all"] & (df["score"] <= 2) & df["has_text"]].copy()
    for prev in VAL.glob("sample_key*.csv"):          # never re-use an already-labelled review
        if prev.name != f"sample_key{sfx}.csv":
            pool = pool[~pool["reviewId"].isin(pd.read_csv(prev)["reviewId"])]

    picked = {}  # reviewId -> list of reasons it was sampled
    for t in themes:
        sub = pool[pool[f"t_{t['id']}"]]
        for rid in sub.sample(min(PER_THEME, len(sub)), random_state=seed)["reviewId"]:
            picked.setdefault(rid, []).append(f"precision:{code_of[t['id']]}")
    unmatched = pool[pool["unspecified"]]
    for rid in unmatched.sample(min(n_unmatched, len(unmatched)), random_state=seed)["reviewId"]:
        picked.setdefault(rid, []).append("unmatched")

    s = pool[pool["reviewId"].isin(picked)].sample(frac=1, random_state=seed).reset_index(drop=True)
    s["sample_id"] = [f"{'S' if rnd == 1 else f'R{rnd}_'}{i + 1:03d}" for i in range(len(s))]
    s["rule_codes"] = s["themes"].fillna("").map(
        lambda x: "|".join(code_of[i] for i in x.split("|") if i))
    s["why_sampled"] = s["reviewId"].map(lambda r: ";".join(picked[r]))

    VAL.mkdir(parents=True, exist_ok=True)
    blind = pd.DataFrame({
        "sample_id": s["sample_id"], "score": s["score"], "date": s["at"].dt.date,
        "content": s["content"], "true_codes": "", "notes": ""})
    blind.to_csv(out_path, index=False, encoding="utf-8-sig")
    s[["sample_id", "reviewId", "rule_codes", "why_sampled"]].to_csv(
        VAL / f"sample_key{sfx}.csv", index=False, encoding="utf-8")

    legend = "\n".join(f"  {t['code']:<6} {t['label']}" for t in themes)
    (VAL / f"LABELLING_GUIDE{sfx}.txt").write_text(f"""HOW TO LABEL  (about 60-90 minutes; do it in one or two sittings)

Open data/validation/manual_labels.csv in Excel. For EACH row, read the review and type, in the
'true_codes' column, every theme that the review REALLY talks about. Separate several codes
with a vertical bar, e.g.  LOG|OTP   If it contains no specific, actionable problem, type NONE.
Do not look at sample_key.csv until you are finished (it holds the rules' answers).

Judge what the user actually says, not what you think caused it.
Examples:  "app logs me out every day"            -> LOG
           "cab was 40 min late"                  -> OPS
           "worst app ever"                       -> NONE
           "crashes and helpdesk is rude"         -> CRASH|SUP

CODES
{legend}
  NONE   no specific actionable problem (pure abuse / praise-like / unclear)

Rows: {len(s)}  ({PER_THEME} per theme to test precision + {n_unmatched} unmatched to test misses)
Save the file as CSV (keep the same name) when done, then run:  python src/validate.py
""", encoding="utf-8")
    print(f"Wrote {len(s)} rows to {out_path}")
    print("Next: label the 'true_codes' column (see data/validation/LABELLING_GUIDE.txt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
