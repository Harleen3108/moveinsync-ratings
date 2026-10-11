"""Extra charts used in the written report. Reads the tables the pipeline already wrote; computes nothing new.

Usage (project root, venv active, after prioritize.py and deep_dive.py):
    python src/report_charts.py

Reads   reports/crash_monthly_2026.csv, reports/priority_table.csv, reports/praise_vs_complaint_terms.csv
Writes  reports/charts/crash_monthly_2026.png
        reports/charts/theme_share.png
        reports/charts/praise_vs_complaint.png
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"
INK, RED, BLUE, GREY = "#1f2d3d", "#c0392b", "#2c6fbb", "#b8c0c8"


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=9)


def crash_chart():
    p = REPORTS / "crash_monthly_2026.csv"
    if not p.exists():
        print(f"missing {p.name}; run deep_dive.py first")
        return
    d = pd.read_csv(p)
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.bar(d["month"], d["pct_of_all_written"], color=[RED if m >= "2026-08" else GREY for m in d["month"]])
    for x, pct, n in zip(d["month"], d["pct_of_all_written"], d["all_crash"]):
        ax.text(x, pct + 0.15, f"{n}", ha="center", fontsize=8, color=INK)
    ax.set_ylabel("% of all written reviews")
    ax.set_title("Reviews about the app not opening or crashing, by month (2026)\n"
                 "numbers above bars = number of such reviews; red = Aug-Oct", loc="left", fontsize=10,
                 fontweight="bold")
    ax.tick_params(axis="x", labelrotation=45)
    style(ax)
    fig.tight_layout()
    fig.savefig(CHARTS / "crash_monthly_2026.png", dpi=170)
    plt.close(fig)


def theme_chart():
    p = REPORTS / "priority_table.csv"
    if not p.exists():
        print(f"missing {p.name}; run prioritize.py first")
        return
    d = pd.read_csv(p).sort_values("reach_pct")
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.barh(d["label"], d["reach_pct"], color=np.where(d["fixability"] >= 0.5, INK, GREY))
    for y, v in enumerate(d["reach_pct"]):
        ax.text(v + 0.2, y, f"{v:.1f}%", va="center", fontsize=8)
    ax.set_xlabel("% of 1-2 star written reviews")
    fig.suptitle("What low-rated reviews complain about", x=0.01, ha="left", fontsize=11, fontweight="bold")
    ax.set_title("dark = mostly an app fix, grey = mostly not; keyword matches x measured precision",
                 loc="right", fontsize=8, color="#555")
    style(ax)
    fig.tight_layout()
    fig.savefig(CHARTS / "theme_share.png", dpi=170)
    plt.close(fig)


def praise_chart():
    p = REPORTS / "praise_vs_complaint_terms.csv"
    if not p.exists():
        print(f"missing {p.name}; run deep_dive.py first")
        return
    d = pd.read_csv(p).iloc[::-1]
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(8, 4.4))
    ax.barh(y + 0.2, d["pct_of_1_2_star"], height=0.38, color=RED, label="1-2 star reviews")
    ax.barh(y - 0.2, d["pct_of_4_5_star"], height=0.38, color=BLUE, label="4-5 star reviews")
    ax.set_yticks(y)
    ax.set_yticklabels(d["word_group"], fontsize=9)
    ax.set_xlabel("% of reviews in that group using these words")
    ax.set_title("What happy and unhappy reviewers write about", loc="left", fontsize=10, fontweight="bold")
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    style(ax)
    fig.tight_layout()
    fig.savefig(CHARTS / "praise_vs_complaint.png", dpi=170)
    plt.close(fig)


def main() -> int:
    CHARTS.mkdir(parents=True, exist_ok=True)
    crash_chart()
    theme_chart()
    praise_chart()
    print(f"Wrote charts to {CHARTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
