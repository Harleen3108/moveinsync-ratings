"""Two follow-up checks on the labelled reviews: (1) what 4-5 star reviews say, (2) the crash-complaint rise in 2026.

Usage (project root, venv active, after classify.py):
    python src/deep_dive.py

Reads   data/processed/reviews_labeled.csv
Writes  reports/praise_vs_complaint_terms.csv   share of 4-5 star vs 1-2 star written reviews using each word group
        reports/crash_monthly_2026.csv          crash-theme share by month (1-2 star reviews, and all written reviews)
        reports/crash_by_version_2026.csv       crash-theme share of 1-2 star reviews by app version and period
        reports/deep_dive_summary.json          headline numbers

Everything uses only the unbiased sample (in_newest_all) of written reviews.
Caveats printed with the results: the praise word groups are plain keyword searches that were NOT validated by hand;
the crash counts are small (tens of reviews), so they point to where to look and do not prove a cause.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"

TERMS = {
    "ease / simple / smooth": r"\b(?:easy|simple|smooth|user.?friendly|convenient|hassle.?free)\b",
    "booking / roster / schedule": r"\b(?:book|booking|schedule|roster|shift)\w*",
    "tracking / live / GPS / ETA": r"\b(?:track|tracking|live location|live|gps|eta)\b",
    "on time / punctual": r"\b(?:on.?time|punctual|timely)\b",
    "safety / SOS": r"\b(?:safe|safety|secure|sos|women)\b",
    "cab / ride / pickup / drop": r"\b(?:cab|ride|trip|pickup|pick.?up|drop)\w*",
    "driver": r"\bdriver\w*",
    "good / best / nice (generic praise)": r"\b(?:good|great|best|nice|excellent|awesome|helpful|useful|perfect)\b",
    "support / helpdesk": r"\b(?:support|help.?desk|helpdesk|customer care)\b",
    "notification / reminder": r"\b(?:notification|reminder|alert)s?\b",
}
CRASH = "t_app_crash_open"


def main() -> int:
    path = PROC / "reviews_labeled.csv"
    if not path.exists():
        print("reviews_labeled.csv not found. Run collect, clean and classify first.")
        return 2
    df = pd.read_csv(path, parse_dates=["at"], dtype={"version_mm": str})
    df["when"] = df["at"]            # 'at' clashes with pandas' DataFrame.at, so use another name
    u = df[df["in_newest_all"] & df["has_text"]].copy()
    pos, neg = u[u["score"] >= 4], u[u["score"] <= 2]
    out = {"unbiased_written_reviews": int(len(u)), "n_4_5_star": int(len(pos)), "n_1_2_star": int(len(neg)),
           "median_words_4_5": float(pos["word_count"].median()), "median_words_1_2": float(neg["word_count"].median()),
           "pct_4_5_star_3_words_or_fewer": round(float((pos["word_count"] <= 3).mean() * 100), 1)}

    # ---- 1. praise vs complaint wording
    rows = []
    for name, pat in TERMS.items():
        rows.append({"word_group": name,
                     "pct_of_4_5_star": round(pos["content"].str.contains(pat, case=False, regex=True, na=False).mean() * 100, 1),
                     "pct_of_1_2_star": round(neg["content"].str.contains(pat, case=False, regex=True, na=False).mean() * 100, 1)})
    terms = pd.DataFrame(rows)
    REPORTS.mkdir(exist_ok=True)
    terms.to_csv(REPORTS / "praise_vs_complaint_terms.csv", index=False)
    print(f"Written reviews in the unbiased sample: {len(u):,}  (4-5 star {len(pos):,}, 1-2 star {len(neg):,})")
    print(f"Median length: {out['median_words_4_5']:.0f} words (4-5 star) vs {out['median_words_1_2']:.0f} (1-2 star); "
          f"{out['pct_4_5_star_3_words_or_fewer']}% of 4-5 star reviews are 3 words or fewer.\n")
    print(terms.to_string(index=False))
    print("\nNote: simple keyword groups, not hand-validated. Positive reviews are short, so they say little about what users value.")

    # ---- 2. crash rise in 2026
    y = u[u["when"] >= "2026-01-01"].copy()
    y["month"] = y["when"].dt.to_period("M").astype(str)
    allm = y.groupby("month").agg(all_written=(CRASH, "size"), all_crash=(CRASH, "sum"))
    negm = y[y["score"] <= 2].groupby("month").agg(neg_written=(CRASH, "size"), neg_crash=(CRASH, "sum"))
    m = allm.join(negm)
    m["pct_of_all_written"] = (m["all_crash"] / m["all_written"] * 100).round(1)
    m["pct_of_1_2_star"] = (m["neg_crash"] / m["neg_written"] * 100).round(1)
    m.reset_index().to_csv(REPORTS / "crash_monthly_2026.csv", index=False)
    print("\nCrash-theme share by month, 2026 (keyword rule, precision measured at 100% on a small sample):")
    print(m[["all_written", "all_crash", "pct_of_all_written", "neg_written", "neg_crash", "pct_of_1_2_star"]].to_string())

    n26 = y[y["score"] <= 2].copy()
    n26["period"] = np.where(n26["when"] >= "2026-08-01", "Aug-Oct 2026", "Jan-Jul 2026")
    g = n26.groupby(["period", "version_mm"]).agg(n_1_2_star=(CRASH, "size"), crash=(CRASH, "sum")).reset_index()
    g["pct"] = (g["crash"] / g["n_1_2_star"] * 100).round(1)
    g = g[g["n_1_2_star"] >= 20].sort_values(["period", "n_1_2_star"], ascending=[True, False])
    g.to_csv(REPORTS / "crash_by_version_2026.csv", index=False)
    print("\nCrash share of 1-2 star reviews by version (versions with 20+ such reviews in that period):")
    print(g.to_string(index=False))
    per = n26.groupby("period")[CRASH].agg(["sum", "size"])
    out["crash_reviews_1_2_star"] = {k: [int(v["sum"]), int(v["size"])] for k, v in per.iterrows()}
    first = u[u["when"] >= "2026-06-01"].groupby("version_mm")["when"].min().sort_values()
    out["first_review_date_by_version_2026"] = {k: str(v.date()) for k, v in first.items() if k.startswith("12.1")}
    print("\nCrash reviews / all 1-2 star reviews per period:", out["crash_reviews_1_2_star"])
    print("Counts are small. This points to where to look (releases from about 12.16); it does not prove a cause.")

    (REPORTS / "deep_dive_summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote tables to {REPORTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
