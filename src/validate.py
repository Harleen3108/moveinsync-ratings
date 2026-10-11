"""Measure the real accuracy of the keyword rules from labelled samples.

Usage (project root, venv active):
    python src/validate.py                                       # your own round-1 labels
    python src/validate.py --labels data/validation/manual_labels_ai.csv
    python src/validate.py --labels data/validation/manual_labels_ai.csv data/validation/manual_labels_r2_ai.csv

Several label files = several rounds, oldest first. A label file named *_rN* (r2, r3...) is
paired with sample_key_rN.csv, any other with sample_key.csv. For each theme the precision comes from the
LATEST round that sampled it (rules were changed between rounds; the fresh sample measures the
current rules). The 'unmatched' miss-rate also comes from the latest round that has one.

Reads   label file(s), matching sample_key file(s), reports/theme_summary.csv, themes.yaml
Writes  reports/validation_report.csv

What the numbers mean
  precision  : of reviews the rule matched (random draw), the share whose label contains that theme.
               n is small per theme, so a 95% Wilson interval is shown. Do not over-read tiny n.
  adjusted   : matched reviews x precision (low/high use the interval). This is the number to quote
               in the report instead of the raw keyword count.
  miss rate  : of reviews NO rule matched (random draw), the share that actually had a real theme.
Caveat: if the labeller is also the author of the rules (an AI that wrote them, or you), the
numbers are optimistic. Report who labelled and how many rows a second person re-checked.
"""
import argparse
import math
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
VAL = ROOT / "data" / "validation"
REPORTS = ROOT / "reports"


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def parse(cell) -> set:
    if pd.isna(cell):
        return set()
    return {t for t in re.split(r"[|,;/\s]+", str(cell).upper()) if t}


def load_round(path: Path, valid: set):
    m = re.search(r"_r(\d+)", path.name)
    key_name = f"sample_key_r{m.group(1)}.csv" if m else "sample_key.csv"
    lab = pd.read_csv(path, encoding="utf-8-sig")
    key = pd.read_csv(VAL / key_name)
    d = lab.merge(key, on="sample_id", how="inner")
    d["true"] = d["true_codes"].map(parse)
    n_total, n_done = len(d), int(d["true_codes"].notna().sum())
    if n_done < 0.9 * n_total:
        raise SystemExit(f"{path.name}: only {n_done}/{n_total} rows labelled. Finish labelling first.")
    bad = sorted({c for s in d["true"] for c in s if c not in valid})
    if bad:
        raise SystemExit(f"{path.name}: unknown codes {bad}\nValid: {sorted(valid)}")
    return d[d["true_codes"].notna()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", nargs="+", default=[str(VAL / "manual_labels.csv")],
                    help="one or more label CSVs, oldest round first")
    args = ap.parse_args()
    with open(ROOT / "themes.yaml", encoding="utf-8") as f:
        themes = yaml.safe_load(f)["themes"]
    valid = {t["code"] for t in themes} | {"NONE"}

    rounds = []
    for i, p in enumerate(args.labels, 1):
        d = load_round(Path(p), valid)
        rounds.append((i, Path(p).name, d))
        print(f"Round {i}: {Path(p).name} ({len(d)} labelled rows)")

    summ = pd.read_csv(REPORTS / "theme_summary.csv").set_index("theme")
    rows = []
    for t in themes:
        code = t["code"]
        used = None
        for i, name, d in reversed(rounds):               # latest round first
            s = d[d["why_sampled"].str.contains(f"precision:{code}(?:;|$)", regex=True)]
            if len(s):
                used = (i, s)
                break
        matched = int(summ.loc[t["id"], "reviews"])
        if used is None:
            rows.append({"code": code, "theme": t["id"], "matched_by_rule": matched, "round": None,
                         "checked_n": 0})
            continue
        i, s = used
        n = len(s)
        k = int(s["true"].map(lambda x: code in x).sum())
        lo, hi = wilson(k, n)
        rows.append({
            "code": code, "theme": t["id"], "matched_by_rule": matched, "round": i, "checked_n": n,
            "correct": k, "precision": round(k / n, 2),
            "prec_lo": round(lo, 2), "prec_hi": round(hi, 2),
            "adjusted": round(matched * k / n),
            "adjusted_lo": round(matched * lo), "adjusted_hi": round(matched * hi),
        })
    rep = pd.DataFrame(rows).sort_values("adjusted", ascending=False)
    REPORTS.mkdir(exist_ok=True)
    rep.to_csv(REPORTS / "validation_report.csv", index=False)
    print(rep.to_string(index=False))

    for i, name, d in reversed(rounds):
        um = d[d["why_sampled"].str.contains("unmatched")]
        if len(um):
            real = um["true"].map(lambda x: bool(x - {"NONE"}))
            lo, hi = wilson(int(real.sum()), len(um))
            print(f"\nUnmatched sample (round {i}): {len(um)} reviews; {int(real.sum())} "
                  f"({real.mean()*100:.0f}%, 95% CI {lo*100:.0f}-{hi*100:.0f}%) actually contained a "
                  f"real theme the rules missed.")
            missed = pd.Series([c for s in um["true"] for c in s if c != "NONE"]).value_counts()
            if len(missed):
                print("Missed themes in that sample:", missed.to_dict())
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
