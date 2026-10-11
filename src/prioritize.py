"""Rank the complaint themes and estimate (as scenarios) what fixing them could do to the rating.

Usage (project root, venv active):
    python src/prioritize.py

Reads   reports/validation_report.csv (precision-adjusted counts), themes.yaml (severity, owner, stage),
        priorities.yaml (histogram, effort, fixability, fix text), reports/theme_change_6m.csv (optional)
Writes  reports/priority_table.csv, reports/priority_robustness.csv, reports/impact_scenarios.csv,
        reports/charts/priority_score.png

SCORE (transparent, no machine learning)
    reach      = precision-adjusted number of 1-2 star written reviews with the theme / all such reviews (%)
    score      = reach x severity x fixability / effort
  * reach       OBSERVED + CALCULATED (keyword matches x measured precision)
  * severity    ASSUMPTION (1-3, themes.yaml)
  * fixability  ASSUMPTION (0-1, priorities.yaml): chance an app change can remove most of the complaint
  * effort      ASSUMPTION (1-5, priorities.yaml)
Then the ranking is stress-tested: severity, fixability, effort and reach are all randomly perturbed
5,000 times and we count how often each theme stays in the top 1 / top 3.

IMPACT MODEL (scenario-based, NOT a forecast)
    new_mean = (sum of stars - moved_users x old_stars + moved_users x new_stars) / total_ratings
    moved_users = (1-2 star ratings) x reach_share x effectiveness
  Key assumptions (all unverified): A1 reasons given in written 1-2 star reviews are representative of
  all 1-2 star raters; A2 'effectiveness' share of affected users stop rating 1-2 stars; A3 they rate
  'new_score' instead. The ceiling (every 1 and 2 star becomes 5) is shown for context.
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"
N_SIM = 5000
SEED = 2026


def main() -> int:
    for need in ("validation_report.csv", "theme_summary.csv"):
        if not (REPORTS / need).exists():
            print(f"reports/{need} missing. Run classify.py and validate.py first.")
            return 1
    with open(ROOT / "themes.yaml", encoding="utf-8") as f:
        themes = {t["id"]: t for t in yaml.safe_load(f)["themes"]}
    with open(ROOT / "priorities.yaml", encoding="utf-8") as f:
        pr = yaml.safe_load(f)

    vr = pd.read_csv(REPORTS / "validation_report.csv").set_index("theme")
    ts = pd.read_csv(REPORTS / "theme_summary.csv").set_index("theme")
    lab = pd.read_csv(ROOT / "data" / "processed" / "reviews_labeled.csv",
                      usecols=["in_newest_all", "score", "has_text"])
    n_neg = int((lab["in_newest_all"] & (lab["score"] <= 2) & lab["has_text"]).sum())   # 4,619

    rows = []
    for tid, t in themes.items():
        p = pr["themes"][tid]
        adj, lo, hi = vr.loc[tid, ["adjusted", "adjusted_lo", "adjusted_hi"]]
        rows.append({
            "theme": tid, "code": t["code"], "label": t["label"], "stage": t["stage"],
            "matched_by_rule": int(vr.loc[tid, "matched_by_rule"]), "precision": vr.loc[tid, "precision"],
            "adjusted_reviews": int(adj), "adj_lo": int(lo), "adj_hi": int(hi),
            "reach_pct": round(adj / n_neg * 100, 1),
            "reach_lo_pct": round(lo / n_neg * 100, 1), "reach_hi_pct": round(hi / n_neg * 100, 1),
            "severity": t["severity"], "fixability": p["fixability"], "effort": p["effort"],
            "team": p["team"], "fix": p["fix"], "metric": p["metric"], "validate": p["validate"],
            "evidence_needed": p["evidence_needed"],
        })
    df = pd.DataFrame(rows)
    df["score"] = (df["reach_pct"] * df["severity"] * df["fixability"] / df["effort"]).round(2)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)

    # ---------------------------------------------------------------- robustness
    rng = np.random.default_rng(SEED)
    base = df.copy()
    top1 = np.zeros(len(base))
    top3 = np.zeros(len(base))
    for _ in range(N_SIM):
        reach = rng.uniform(base["reach_lo_pct"], base["reach_hi_pct"])
        sev = np.clip(base["severity"] + rng.choice([-1, 0, 0, 1], len(base)), 1, 3)
        fix = np.clip(base["fixability"] + rng.uniform(-0.15, 0.15, len(base)), 0.05, 1.0)
        eff = np.clip(base["effort"] * rng.uniform(0.67, 1.5, len(base)), 1, 8)
        sc = reach * sev * fix / eff
        order = np.argsort(-sc)
        top1[order[0]] += 1
        top3[order[:3]] += 1
    df["pct_sims_top1"] = (top1 / N_SIM * 100).round(0)
    df["pct_sims_top3"] = (top3 / N_SIM * 100).round(0)

    out_cols = ["rank", "code", "theme", "label", "stage", "matched_by_rule", "precision",
                "adjusted_reviews", "adj_lo", "adj_hi", "reach_pct", "severity", "fixability", "effort",
                "score", "pct_sims_top1", "pct_sims_top3", "team", "fix", "metric", "validate",
                "evidence_needed"]
    REPORTS.mkdir(exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    df[out_cols].to_csv(REPORTS / "priority_table.csv", index=False)
    df[["rank", "code", "score", "pct_sims_top1", "pct_sims_top3"]].to_csv(
        REPORTS / "priority_robustness.csv", index=False)

    print(f"1-2 star written reviews with text in the unbiased sample: {n_neg}\n")
    print(df[["rank", "code", "reach_pct", "severity", "fixability", "effort", "score",
              "pct_sims_top1", "pct_sims_top3"]].to_string(index=False))

    # ---------------------------------------------------------------- impact scenarios
    h = {int(k): v for k, v in pr["rating_histogram"].items()}
    total = sum(h.values())
    stars = sum(k * v for k, v in h.items())
    mean0 = stars / total
    low_n = h[1] + h[2]
    low_stars = h[1] * 1 + h[2] * 2
    old_avg_low = low_stars / low_n
    ceiling = (stars - low_stars + 5 * low_n) / total
    print(f"\nRating histogram (observed 10 Oct 2026): n={total:,}, mean={mean0:.4f}; "
          f"1-2 star ratings = {low_n:,} ({low_n/total*100:.1f}%)")
    print(f"Ceiling (every 1-2 star becomes 5): {ceiling:.3f}  (+{ceiling-mean0:.3f})  -- not a forecast")

    top3 = df.head(3)["code"].tolist()
    groups = {f"#1 {df.iloc[0]['code']}": [df.iloc[0]["theme"]],
              f"top 3 ({'+'.join(top3)})": df.head(3)["theme"].tolist()}
    srows = []
    for gname, tids in groups.items():
        # themes overlap (a review can have two themes) so summing reach overstates; cap at the union
        # share estimated as 1 - prod(1 - share), which assumes independence (a stated approximation)
        shares = [df.loc[df["theme"] == t, "reach_pct"].iloc[0] / 100 for t in tids]
        reach = 1 - np.prod([1 - s for s in shares])
        for eff in pr["scenarios"]["effectiveness"]:
            for ns in pr["scenarios"]["new_score"]:
                moved = low_n * reach * eff
                new_mean = (stars - moved * old_avg_low + moved * ns) / total
                srows.append({"fix_group": gname, "reach_pct_of_1_2star": round(reach * 100, 1),
                              "effectiveness": eff, "new_score_of_movers": ns,
                              "moved_ratings": int(round(moved)),
                              "new_mean": round(new_mean, 3), "delta": round(new_mean - mean0, 3)})
    sc = pd.DataFrame(srows)
    sc.to_csv(REPORTS / "impact_scenarios.csv", index=False)
    print("\nScenario impact on the displayed average rating (assumption-driven):")
    for g, sub in sc.groupby("fix_group", sort=False):
        piv = sub.pivot(index="effectiveness", columns="new_score_of_movers", values="delta")
        print(f"\n{g}   reach = {sub['reach_pct_of_1_2star'].iloc[0]}% of 1-2 star raters")
        print("  rows: effectiveness; columns: what movers rate instead")
        print(piv.round(3).to_string())

    # ---------------------------------------------------------------- chart
    fig, ax = plt.subplots(figsize=(8, 5))
    d = df.iloc[::-1]
    ax.barh(d["code"], d["score"], color=np.where(d["fixability"] >= 0.5, "#34495e", "#bdc3c7"))
    ax.set_xlabel("priority score  (reach % x severity x fixability / effort)")
    ax.set_title("Priority score by theme (grey = mostly not an app fix)", loc="left",
                 fontsize=11, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(CHARTS / "priority_score.png", dpi=150)
    plt.close(fig)
    print(f"\nWrote {REPORTS / 'priority_table.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
