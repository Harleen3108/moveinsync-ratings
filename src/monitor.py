"""Weekly health check on the Play Store reviews. Says OK / WATCH / ALERT and why.

Usage (project root, venv active), after the pipeline has been re-run:
    python src/monitor.py
    python src/monitor.py --now 2026-12-01      # pretend today is this date (to test the stale check)

Reads   data/processed/reviews_labeled.csv, config.yaml (thresholds), themes.yaml
Writes  reports/monitor_report.md     (human-readable status and flags)
        reports/monitor_flags.csv     (one row per check)
        reports/monitor_history.csv   (one row appended per run, so you can see the trend)
Exit    0 = OK or WATCH, 1 = ALERT, 2 = data is stale or missing (never report old data as current)

Checks (all use only the unbiased sample, in_newest_all):
  1. FRESHNESS   newest review older than stale_after_days            -> STALE (exit 2)
  2. 1-STAR      1-star share, last N days vs previous baseline days   -> ALERT if worse and p < 0.01
  3. THEMES      share of 1-2 star reviews naming each theme, recent vs baseline
                 -> WATCH if it moved >= theme_min_diff_pts and p < theme_p_value
  4. VERSIONS    an app version with enough reviews whose 1-star share is clearly above the rest -> WATCH
  5. REPLIES     share of recent 1-2 star reviews with a developer reply below the floor          -> WATCH
Every check also reports its sample size. A comparison with too few reviews says so instead of guessing.
With 16 themes tested at once, one false alarm in a while is expected: a flag is a place to look, not proof.
"""
import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"


def two_prop(k1, n1, k0, n0):
    """Difference in proportions (recent - baseline) and a two-sided p-value."""
    if min(n1, n0) == 0:
        return (float("nan"), float("nan"))
    p1, p0, p = k1 / n1, k0 / n0, (k1 + k0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    if se == 0:
        return (p1 - p0, 1.0)
    return (p1 - p0, math.erfc(abs((p1 - p0) / se) / math.sqrt(2)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default=None, help="override today's date (YYYY-MM-DD), for testing")
    args = ap.parse_args()

    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    m = cfg["monitor"]
    with open(ROOT / "themes.yaml", encoding="utf-8") as f:
        themes = yaml.safe_load(f)["themes"]
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp(datetime.now(timezone.utc).date())

    path = PROC / "reviews_labeled.csv"
    if not path.exists():
        print("STALE/MISSING: reviews_labeled.csv not found. Run collect, clean and classify first.")
        return 2
    df = pd.read_csv(path, parse_dates=["at"], dtype={"version_mm": str})
    u = df[df["in_newest_all"]].copy()
    newest = u["at"].max()
    age = (now - newest.normalize()).days
    flags = []

    def add(check, level, detail, n=None):
        flags.append({"check": check, "level": level, "detail": detail, "n": n})

    # ---- 1. freshness
    if age > cfg["stale_after_days"]:
        add("freshness", "STALE", f"newest review is {newest.date()} ({age} days old; limit "
            f"{cfg['stale_after_days']}). Re-run collect.py --refresh before trusting any number.", None)
        _write(flags, newest, now, {}, "STALE")
        print(f"STALE: newest review {newest.date()} is {age} days old. Nothing below is current.")
        return 2
    add("freshness", "OK", f"newest review {newest.date()} ({age} days old)", len(u))

    # ---- 2. 1-star share
    u["is1"] = u["score"] == 1
    r_start = newest - pd.Timedelta(days=m["recent_days"])
    b_start = r_start - pd.Timedelta(days=m["baseline_days"])
    rec = u[u["at"] > r_start]
    base = u[(u["at"] > b_start) & (u["at"] <= r_start)]
    metrics = {"as_of_newest": str(newest.date()), "n_recent": len(rec), "n_baseline": len(base)}
    if len(rec) < m["min_recent_n"]:
        add("1-star share", "NOTE", f"only {len(rec)} reviews in the last {m['recent_days']} days "
            f"(need {m['min_recent_n']}); not compared", len(rec))
    else:
        d, p = two_prop(int(rec["is1"].sum()), len(rec), int(base["is1"].sum()), len(base))
        metrics.update({"share_1star_recent_pct": round(rec["is1"].mean() * 100, 1),
                        "share_1star_baseline_pct": round(base["is1"].mean() * 100, 1)})
        txt = (f"{rec['is1'].mean()*100:.1f}% in the last {m['recent_days']} days (n={len(rec)}) vs "
               f"{base['is1'].mean()*100:.1f}% in the previous {m['baseline_days']} (n={len(base)}); "
               f"diff {d*100:+.1f} pts, p={p:.3f}")
        add("1-star share", "ALERT" if (d > 0 and p < 0.01) else "OK", txt, len(rec))

    # ---- 3. themes (1-2 star reviews with text)
    neg = u[(u["score"] <= 2) & u["has_text"]]
    t1 = newest - pd.Timedelta(days=m["theme_recent_days"])
    t0 = t1 - pd.Timedelta(days=m["theme_baseline_days"])
    tr, tb = neg[neg["at"] > t1], neg[(neg["at"] > t0) & (neg["at"] <= t1)]
    if len(tr) < m["min_theme_recent_n"]:
        add("themes", "NOTE", f"only {len(tr)} 1-2 star reviews with text in the last "
            f"{m['theme_recent_days']} days (need {m['min_theme_recent_n']}); themes not compared", len(tr))
    else:
        for t in themes:
            col = f"t_{t['id']}"
            k1, k0 = int(tr[col].sum()), int(tb[col].sum())
            d, p = two_prop(k1, len(tr), k0, len(tb))
            hit = abs(d) * 100 >= m["theme_min_diff_pts"] and p < m["theme_p_value"]
            if hit:
                add(f"theme {t['code']}", "WATCH" if d > 0 else "NOTE",
                    f"{t['label']}: {k1/len(tr)*100:.1f}% of recent 1-2 star reviews vs "
                    f"{k0/len(tb)*100:.1f}% before ({d*100:+.1f} pts, p={p:.4f}). Owner: {t['owner']}", len(tr))
        if not any(f["check"].startswith("theme ") for f in flags):
            add("themes", "OK", f"no theme moved by {m['theme_min_diff_pts']}+ pts with p<{m['theme_p_value']} "
                f"(recent n={len(tr)}, baseline n={len(tb)})", len(tr))

    # ---- 4. versions
    vr = rec[rec["version_mm"].astype(str) != "unknown"]
    flagged_v = False
    if len(rec) >= m["min_recent_n"]:
        for v, g in vr.groupby("version_mm"):
            if len(g) < m["version_min_n"]:
                continue
            rest = rec[rec["version_mm"] != v]
            d, p = two_prop(int(g["is1"].sum()), len(g), int(rest["is1"].sum()), len(rest))
            if d > 0 and p < 0.01:
                flagged_v = True
                add(f"version {v}", "WATCH", f"1-star share {g['is1'].mean()*100:.1f}% vs "
                    f"{rest['is1'].mean()*100:.1f}% for other versions in the last {m['recent_days']} days "
                    f"({d*100:+.1f} pts, p={p:.4f})", len(g))
        if not flagged_v:
            add("versions", "OK", "no app version with enough reviews is clearly worse than the rest", len(vr))

    # ---- 5. replies
    cutoff = newest - pd.Timedelta(days=m["reply_grace_days"])
    rn = neg[(neg["at"] > r_start) & (neg["at"] <= cutoff)]
    if len(rn) >= 30:
        rr = rn["has_reply"].mean() * 100
        metrics["reply_rate_recent_1_2star_pct"] = round(rr, 1)
        add("replies", "WATCH" if rr < m["reply_rate_floor_pct"] else "OK",
            f"{rr:.1f}% of 1-2 star reviews from the last {m['recent_days']} days (excluding the newest "
            f"{m['reply_grace_days']}) have a developer reply; floor is {m['reply_rate_floor_pct']}%", len(rn))
    else:
        add("replies", "NOTE", f"only {len(rn)} recent 1-2 star reviews; reply rate not checked", len(rn))

    levels = [f["level"] for f in flags]
    status = "ALERT" if "ALERT" in levels else ("WATCH" if "WATCH" in levels else "OK")
    _write(flags, newest, now, metrics, status)
    print(f"STATUS: {status}   (data through {newest.date()}, checked {now.date()})")
    for f in flags:
        print(f"  [{f['level']:5}] {f['check']}: {f['detail']}")
    print(f"\nWrote {REPORTS / 'monitor_report.md'}")
    return 1 if status == "ALERT" else 0


def _write(flags, newest, now, metrics, status):
    REPORTS.mkdir(exist_ok=True)
    fl = pd.DataFrame(flags)
    fl.to_csv(REPORTS / "monitor_flags.csv", index=False)
    lines = [f"# Play Store monitor: {status}", "",
             f"Checked on {now.date()}; newest review in the data: {newest.date()}.", ""]
    if status == "STALE":
        lines.append("The data is older than the allowed limit, so no trend is reported. "
                     "Run `python src/collect.py --refresh`, then clean, classify, analyze and monitor again.")
    else:
        lines += ["| Level | Check | Detail |", "| --- | --- | --- |"]
        order = {"ALERT": 0, "WATCH": 1, "NOTE": 2, "OK": 3}
        for f in sorted(flags, key=lambda x: order.get(x["level"], 9)):
            lines.append(f"| {f['level']} | {f['check']} | {f['detail']} |")
        lines += ["", "A WATCH or ALERT is a place to look, not proof of a fault: 16 themes are tested at "
                  "once and reviews are a lagging signal. Confirm with crash logs, booking funnels and "
                  "other internal data before acting."]
    (REPORTS / "monitor_report.md").write_text("\n".join(lines), encoding="utf-8")
    row = {"checked_on": str(now.date()), "newest_review": str(newest.date()), "status": status, **metrics}
    hp = REPORTS / "monitor_history.csv"
    h = pd.concat([pd.read_csv(hp), pd.DataFrame([row])], ignore_index=True) if hp.exists() \
        else pd.DataFrame([row])
    h.to_csv(hp, index=False)


if __name__ == "__main__":
    sys.exit(main())
