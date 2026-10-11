"""Trends, version cuts, developer-reply analysis and theme trends, with charts.

Usage (project root, venv active):
    python src/analyze.py

Reads   data/processed/reviews_labeled.csv  (made by clean.py then classify.py)
Writes  reports/monthly_trend.csv, version_trend.csv, reply_summary.csv, theme_trend.csv,
        reports/analysis_summary.json, reports/charts/*.png

Rules that keep the numbers honest
  * Every RATE (share of 1-star, theme share, etc.) uses ONLY the unbiased sample (in_newest_all =
    the newest reviews of all star ratings). The 1-star / 2-star oversampling jobs are never used for rates.
  * These are WRITTEN reviews, not all ratings. Unhappy users write more, so the 1-star share here is
    far higher than among all ratings. Use it for TRENDS and MIX, never as "the app's rating".
  * Every proportion has a 95% Wilson interval; cells with small n are flagged, not hidden.
"""
import json
import math
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"

MIN_N_MONTH = 100       # months below this are flagged 'low_n'
MIN_N_VERSION = 150     # versions below this are not charted
RECENT_DAYS = 90


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def two_prop_z(k1, n1, k2, n2):
    """Two-sided z-test for a difference of two proportions. Returns (diff, p_value)."""
    if min(n1, n2) == 0:
        return (np.nan, np.nan)
    p1, p2, p = k1 / n1, k2 / n2, (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (p1 - p2, 1.0)
    z = (p1 - p2) / se
    return (p1 - p2, math.erfc(abs(z) / math.sqrt(2)))


def ver_key(v):
    m = re.match(r"^(\d+)\.(\d+)$", str(v))
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def style(ax, title, ylabel):
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)


def main() -> int:
    path = PROC / "reviews_labeled.csv"
    if not path.exists():
        print("reviews_labeled.csv not found. Run clean.py and classify.py first.")
        return 1
    df = pd.read_csv(path, parse_dates=["at", "repliedAt"])
    df["content"] = df["content"].fillna("").astype(str)
    CHARTS.mkdir(parents=True, exist_ok=True)

    u = df[df["in_newest_all"]].copy()                      # unbiased sample, all stars
    newest = u["at"].max()
    oldest = u["at"].min()
    print(f"Unbiased sample: {len(u):,} written reviews, {oldest.date()} to {newest.date()}")
    summary = {"unbiased_n": int(len(u)), "first_review": str(oldest.date()),
               "last_review": str(newest.date())}

    # ---------------------------------------------------------------- 1. monthly trend
    u["is1"] = (u["score"] == 1)
    u["is12"] = (u["score"] <= 2)
    g = u.groupby("month")
    mt = pd.DataFrame({"n": g.size(), "n_1star": g["is1"].sum(), "mean_score": g["score"].mean()})
    mt["share_1star"] = mt["n_1star"] / mt["n"]
    ci = mt.apply(lambda r: wilson(int(r["n_1star"]), int(r["n"])), axis=1)
    mt["lo"] = [c[0] for c in ci]
    mt["hi"] = [c[1] for c in ci]
    mt["low_n"] = mt["n"] < MIN_N_MONTH
    # the newest (partial) month is flagged too
    mt = mt.reset_index().sort_values("month")
    mt.to_csv(REPORTS / "monthly_trend.csv", index=False, float_format="%.4f")

    last24 = mt.tail(25)
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(last24))
    ax.plot(x, last24["share_1star"] * 100, marker="o", color="#c0392b", lw=1.8)
    ax.fill_between(x, last24["lo"] * 100, last24["hi"] * 100, color="#c0392b", alpha=0.15)
    flagged = last24["low_n"].to_numpy()
    ax.scatter(x[flagged], (last24["share_1star"] * 100).to_numpy()[flagged], s=70,
               facecolors="white", edgecolors="#c0392b", zorder=3, label=f"n < {MIN_N_MONTH} (partial or sparse)")
    ax.set_xticks(x[::2])
    ax.set_xticklabels(last24["month"].astype(str).to_numpy()[::2], rotation=45, ha="right")
    ax.set_ylim(0, None)
    style(ax, "1-star share of written reviews, by month (band = 95% interval)", "% of written reviews")
    if flagged.any():
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(CHARTS / "monthly_1star_share.png", dpi=150)
    plt.close(fig)

    # ---------------------------------------------------------------- 2. recent vs previous window
    cut1 = newest - pd.Timedelta(days=RECENT_DAYS)
    cut0 = cut1 - pd.Timedelta(days=RECENT_DAYS)
    rec = u[u["at"] > cut1]
    prev = u[(u["at"] > cut0) & (u["at"] <= cut1)]
    d, p = two_prop_z(int(rec["is1"].sum()), len(rec), int(prev["is1"].sum()), len(prev))
    summary["recent_vs_previous_90d"] = {
        "recent_n": int(len(rec)), "recent_1star_pct": round(rec["is1"].mean() * 100, 1),
        "previous_n": int(len(prev)), "previous_1star_pct": round(prev["is1"].mean() * 100, 1),
        "diff_pct_points": round(d * 100, 1), "p_value": round(p, 3)}
    print(f"\nLast {RECENT_DAYS}d vs previous {RECENT_DAYS}d: 1-star share "
          f"{rec['is1'].mean()*100:.1f}% (n={len(rec)}) vs {prev['is1'].mean()*100:.1f}% (n={len(prev)}); "
          f"diff {d*100:+.1f} pts, p={p:.3f}")

    # ---------------------------------------------------------------- 3. app versions
    # reviewCreatedVersion = the version the reviewer had when writing (version_mm = major.minor)
    v = u[u["version_mm"].astype(str) != "unknown"].copy()
    vt = v.groupby("version_mm").agg(n=("score", "size"), n_1star=("is1", "sum"),
                                     first=("at", "min"), last=("at", "max")).reset_index()
    vt["share_1star"] = vt["n_1star"] / vt["n"]
    ci = vt.apply(lambda r: wilson(int(r["n_1star"]), int(r["n"])), axis=1)
    vt["lo"] = [c[0] for c in ci]
    vt["hi"] = [c[1] for c in ci]
    vt["enough_n"] = vt["n"] >= MIN_N_VERSION
    vt["_k"] = vt["version_mm"].map(ver_key)
    vt = vt.sort_values("_k").drop(columns="_k")
    vt.to_csv(REPORTS / "version_trend.csv", index=False, float_format="%.4f")
    vc = vt[vt["enough_n"]]
    if len(vc):
        fig, ax = plt.subplots(figsize=(9, 4))
        x = np.arange(len(vc))
        err = np.vstack([(vc["share_1star"] - vc["lo"]) * 100, (vc["hi"] - vc["share_1star"]) * 100])
        ax.bar(x, vc["share_1star"] * 100, color="#34495e", alpha=0.85)
        ax.errorbar(x, vc["share_1star"] * 100, yerr=err, fmt="none", ecolor="black", capsize=3, lw=1)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{a}\n(n={b})" for a, b in zip(vc["version_mm"], vc["n"])], fontsize=8)
        style(ax, f"1-star share by app version the reviewer used (versions with n >= {MIN_N_VERSION})",
              "% of written reviews")
        fig.tight_layout()
        fig.savefig(CHARTS / "version_1star_share.png", dpi=150)
        plt.close(fig)
    summary["versions_with_enough_n"] = int(vt["enough_n"].sum())

    # ---------------------------------------------------------------- 4. developer replies
    rep_rows = []
    for label, sub in [("all", u)] + [(f"{s}-star", u[u["score"] == s]) for s in (1, 2, 3, 4, 5)]:
        rd = sub.loc[sub["has_reply"], "reply_days"].dropna()
        rep_rows.append({"group": label, "n": len(sub), "replied": int(sub["has_reply"].sum()),
                         "reply_rate_pct": round(sub["has_reply"].mean() * 100, 1),
                         "median_reply_days": round(rd.median(), 1) if len(rd) else None,
                         "p90_reply_days": round(rd.quantile(0.9), 1) if len(rd) else None})
    rs = pd.DataFrame(rep_rows)
    # reply rate by year x star (does the reply rate stay stable over time?)
    u["year"] = u["at"].dt.year
    ys = u.pivot_table(index="year", columns="score", values="has_reply", aggfunc="mean") * 100
    ys["1-2 star"] = u[u["score"] <= 2].groupby("year")["has_reply"].mean() * 100
    ys["all"] = u.groupby("year")["has_reply"].mean() * 100
    yn = u[u["score"] <= 2].groupby("year").size().rename("n_1_2_star")
    ys = ys.round(1).join(yn).reset_index()
    ys.columns = [str(c) for c in ys.columns]
    rs.to_csv(REPORTS / "reply_summary.csv", index=False)
    ys.to_csv(REPORTS / "reply_rate_by_year.csv", index=False)
    print("Developer replies (unbiased sample):")
    print(rs.to_string(index=False))
    print("\nReply rate (%) by year of review and star rating:")
    print(ys.to_string(index=False))
    neg = u[u["score"] <= 2].copy()

    # does a reply carry the same template? (transparent check, no judgement on quality)
    rp = df.loc[df["has_reply"] & df["in_newest_all"], "replyContent"].fillna("").astype(str)
    top = rp.str.slice(0, 80).value_counts().head(3)
    summary["top_reply_openers"] = {k: int(c) for k, c in top.items()}
    summary["reply_rate_negative_pct_all_years"] = round(neg["has_reply"].mean() * 100, 1)
    summary["reply_rate_1_2_star_by_year_pct"] = {int(r["year"]): r["1-2 star"] for _, r in ys.iterrows()}

    fig, ax = plt.subplots(figsize=(8, 3.8))
    ax.plot(ys["year"], ys["1-2 star"], marker="o", color="#c0392b", label="1-2 star reviews")
    ax.plot(ys["year"], ys["all"], marker="o", color="#7f8c8d", label="all written reviews")
    ax.set_ylim(0, 105)
    style(ax, "Share of written reviews that got a developer reply, by year of review", "% replied")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(CHARTS / "reply_rate_by_year.png", dpi=150)
    plt.close(fig)

    # ---------------------------------------------------------------- 5. theme trends (1-2 star, text)
    with open(ROOT / "themes.yaml", encoding="utf-8") as f:
        themes = yaml.safe_load(f)["themes"]
    tcols = {t["id"]: f"t_{t['id']}" for t in themes}
    code_of = {t["id"]: t["code"] for t in themes}
    ng = neg[neg["has_text"]].copy()
    ng["half"] = ng["at"].dt.year.astype(str) + "-H" + np.where(ng["at"].dt.month <= 6, "1", "2")
    ng["quarter"] = ng["at"].dt.to_period("Q").astype(str)
    halves = sorted(ng["half"].unique())[-4:]
    rows = []
    for tid, col in tcols.items():
        for h in halves:
            sub = ng[ng["half"] == h]
            k = int(sub[col].sum())
            lo, hi = wilson(k, len(sub))
            rows.append({"theme": tid, "code": code_of[tid], "period": h, "n_neg_reviews": len(sub),
                         "matched": k, "share_pct": round(k / len(sub) * 100, 1) if len(sub) else None,
                         "lo_pct": round(lo * 100, 1), "hi_pct": round(hi * 100, 1)})
    tt = pd.DataFrame(rows)
    tt.to_csv(REPORTS / "theme_trend.csv", index=False)

    # last 6 months vs the 6 months before, per theme (keyword matches; see validation for precision)
    c1 = newest - pd.Timedelta(days=182)
    c0 = c1 - pd.Timedelta(days=182)
    r6, p6 = ng[ng["at"] > c1], ng[(ng["at"] > c0) & (ng["at"] <= c1)]
    ch = []
    for tid, col in tcols.items():
        k1, k0 = int(r6[col].sum()), int(p6[col].sum())
        dd, pv = two_prop_z(k1, len(r6), k0, len(p6))
        ch.append({"theme": tid, "code": code_of[tid], "recent_pct": round(k1 / len(r6) * 100, 1),
                   "previous_pct": round(k0 / len(p6) * 100, 1), "diff_pts": round(dd * 100, 1),
                   "p_value": round(pv, 3), "flag": "CHANGED" if pv < 0.01 else ""})
    ch = pd.DataFrame(ch).sort_values("recent_pct", ascending=False)
    ch.to_csv(REPORTS / "theme_change_6m.csv", index=False)
    print(f"\nTheme share of 1-2 star reviews: last 6 months (n={len(r6)}) vs prior 6 months (n={len(p6)}):")
    print(ch.to_string(index=False))
    print("(16 themes are tested at once; with p<0.01 expect ~0-1 false alarms. Treat 'CHANGED' as a "
          "lead to investigate, not proof.)")
    summary["theme_window_n"] = {"recent": int(len(r6)), "previous": int(len(p6))}

    top6 = ch.head(6)["theme"].tolist()
    q = ng[ng["at"] >= newest - pd.Timedelta(days=730)]
    fig, ax = plt.subplots(figsize=(9, 4))
    qs = sorted(q["quarter"].unique())
    for tid in top6:
        ys = [q.loc[q["quarter"] == s, tcols[tid]].mean() * 100 for s in qs]
        ax.plot(qs, ys, marker="o", lw=1.5, label=code_of[tid])
    ax.set_xticks(range(len(qs)))
    ax.set_xticklabels(qs, rotation=45, ha="right")
    style(ax, "Share of 1-2 star reviews mentioning each theme (keyword match), by quarter",
          "% of 1-2 star reviews")
    ax.legend(frameon=False, ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(CHARTS / "theme_trend_quarterly.png", dpi=150)
    plt.close(fig)

    # ---------------------------------------------------------------- 6. update-mention check
    um = u.copy()
    um["period"] = np.where(um["at"] > cut1, "recent", "earlier")
    summary["mentions_update_pct_neg"] = round(neg["mentions_update"].mean() * 100, 1)

    with open(REPORTS / "analysis_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote tables and charts to {REPORTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
