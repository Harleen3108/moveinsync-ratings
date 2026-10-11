"""Review Intelligence Dashboard (Streamlit). Reads the files the pipeline already wrote; computes nothing new
except the keyword highlighting in the investigation tab.

Run (project root, venv active, after collect/clean/classify/analyze/validate/prioritize/monitor):
    streamlit run src/dashboard.py

Tabs
  1. Overview            rating baseline, trends, replies, themes, versions, monitor status
  2. Review investigation pick a 1-2 star review: themes, owner, severity, the exact words that triggered each
                          theme, and the measured accuracy of that theme's rule
  3. Recommended actions  the ranked fixes (written by the analyst in priorities.yaml, not generated)
"""
import html
import json
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PROC = ROOT / "data" / "processed"

st.set_page_config(page_title="Review Intelligence Dashboard", layout="wide")
INK, RED, BLUE, GREY = "#1f2d3d", "#c0392b", "#2c6fbb", "#9aa5b1"


# ----------------------------------------------------------------------------- loading
@st.cache_data(ttl=600)   # re-read the files every 10 minutes so a pipeline re-run shows up
def load_csv(name):
    p = REPORTS / name
    return pd.read_csv(p) if p.exists() else None


@st.cache_data(ttl=600)
def load_reviews():
    p = PROC / "reviews_labeled.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["at", "repliedAt"], dtype={"version_mm": str, "version": str})
    df["content"] = df["content"].fillna("").astype(str)
    return df


@st.cache_data(ttl=600)
def load_yaml(name):
    with open(ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def need(df, what):
    if df is None:
        st.warning(f"{what} not found. Run the pipeline first (see README).")
        st.stop()


reviews = load_reviews()
need(reviews, "data/processed/reviews_labeled.csv")
themes_cfg = load_yaml("themes.yaml")
themes = themes_cfg["themes"]
prio_cfg = load_yaml("priorities.yaml")
val = load_csv("validation_report.csv")
prio = load_csv("priority_table.csv")
monthly = load_csv("monthly_trend.csv")
versions = load_csv("version_trend.csv")
replies = load_csv("reply_rate_by_year.csv")
flags = load_csv("monitor_flags.csv")
summ_path = PROC / "clean_summary.json"
clean_summary = json.loads(summ_path.read_text()) if summ_path.exists() else {}

unb = reviews[reviews["in_newest_all"]]
newest = unb["at"].max()

# ----------------------------------------------------------------------------- sidebar
st.sidebar.title("Review Intelligence")
st.sidebar.caption("com.moveinsync.ets (employee commute app)")
st.sidebar.markdown(f"**Newest review in data:** {newest.date()}")
st.sidebar.markdown(f"**Unbiased sample:** {len(unb):,} written reviews")
age = (pd.Timestamp.now().normalize() - newest.normalize()).days
if age > 7:
    st.sidebar.error(f"Data is {age} days old. Re-run collect.py --refresh before trusting these numbers.")
st.sidebar.info("Written reviews are not ratings: 1 star is 6.4% of ratings but about 27% of written reviews. "
                "Use this for themes and trends, not as the app's rating.")

tab1, tab2, tab3 = st.tabs(["Overview", "Review investigation", "Recommended actions"])

# ----------------------------------------------------------------------------- tab 1
with tab1:
    st.header("Overview")
    hist = {int(k): v for k, v in prio_cfg["rating_histogram"].items()}
    total = sum(hist.values())
    mean = sum(k * v for k, v in hist.items()) / total
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Average rating (observed 10 Oct 2026)", f"{mean:.2f}")
    c2.metric("Ratings", f"{total:,}")
    c3.metric("1-2 star share of ratings", f"{(hist[1] + hist[2]) / total * 100:.1f}%")
    rec = unb[unb["at"] > newest - pd.Timedelta(days=90)]
    prev = unb[(unb["at"] > newest - pd.Timedelta(days=180)) & (unb["at"] <= newest - pd.Timedelta(days=90))]
    c4.metric("1-star share of written reviews, last 90 days",
              f"{(rec['score'] == 1).mean() * 100:.1f}%",
              f"{((rec['score'] == 1).mean() - (prev['score'] == 1).mean()) * 100:+.1f} pts vs previous 90 days")

    if flags is not None:
        lv = flags["level"].tolist()
        status = "ALERT" if "ALERT" in lv else ("WATCH" if "WATCH" in lv else ("STALE" if "STALE" in lv else "OK"))
        with st.expander(f"Weekly monitor status: {status}", expanded=status != "OK"):
            st.dataframe(flags[["level", "check", "detail"]], hide_index=True, width="stretch")
    else:
        st.info("Run `python src/monitor.py` to see the weekly status here.")

    a, b = st.columns(2)
    with a:
        hdf = pd.DataFrame({"stars": [str(k) for k in sorted(hist)], "ratings": [hist[k] for k in sorted(hist)]})
        fig = px.bar(hdf, x="stars", y="ratings", title="Ratings by star (observed on the listing)")
        fig.update_traces(marker_color=BLUE)
        fig.update_layout(height=320, margin=dict(t=50, b=10))
        st.plotly_chart(fig, width="stretch")
    with b:
        if monthly is not None:
            m = monthly.tail(25)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=m["month"], y=m["hi"] * 100, line=dict(width=0), showlegend=False,
                                     hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=m["month"], y=m["lo"] * 100, fill="tonexty", line=dict(width=0),
                                     fillcolor="rgba(192,57,43,0.15)", name="95% interval", hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=m["month"], y=m["share_1star"] * 100, mode="lines+markers",
                                     line=dict(color=RED), name="1-star share"))
            fig.update_layout(title="1-star share of written reviews by month", height=320,
                              yaxis_title="% of written reviews", margin=dict(t=50, b=10), showlegend=False)
            fig.update_yaxes(rangemode="tozero")
            st.plotly_chart(fig, width="stretch")
            st.caption("Most recent month is partial. Months with fewer than 100 reviews are noisy.")

    a, b = st.columns(2)
    with a:
        if replies is not None:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=replies["year"], y=replies["1-2 star"], mode="lines+markers",
                                     name="1-2 star reviews", line=dict(color=RED)))
            fig.add_trace(go.Scatter(x=replies["year"], y=replies["all"], mode="lines+markers",
                                     name="all written reviews", line=dict(color=GREY)))
            fig.update_layout(title="Developer reply rate by year of review", height=320,
                              yaxis_title="% with a reply", yaxis_range=[0, 105], margin=dict(t=50, b=10),
                              legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig, width="stretch")
    with b:
        if val is not None:
            v = val.dropna(subset=["adjusted"]).sort_values("adjusted")
            n_neg = int(((unb["score"] <= 2) & unb["has_text"]).sum())
            v["share"] = v["adjusted"] / n_neg * 100
            names = {t["code"]: t["label"] for t in themes}
            v["label"] = v["code"].map(names)
            fig = px.bar(v, x="share", y="label", orientation="h", hover_data=["adjusted", "precision"],
                         title=f"Complaint themes: % of {n_neg:,} 1-2 star reviews (precision-adjusted)")
            fig.update_traces(marker_color=INK)
            fig.update_layout(height=420, xaxis_title="% of 1-2 star written reviews", yaxis_title=None,
                              margin=dict(t=50, b=10, l=10))
            st.plotly_chart(fig, width="stretch")
            st.caption("A review can name several themes, so shares do not add up to 100%.")
    if versions is not None:
        vv = versions[versions["enough_n"]]
        fig = go.Figure(go.Bar(x=vv["version_mm"], y=vv["share_1star"] * 100, marker_color=INK,
                               error_y=dict(type="data", symmetric=False,
                                            array=(vv["hi"] - vv["share_1star"]) * 100,
                                            arrayminus=(vv["share_1star"] - vv["lo"]) * 100),
                               customdata=vv["n"], hovertemplate="version %{x}<br>%{y:.1f}% 1-star "
                                                                  "(n=%{customdata})<extra></extra>"))
        fig.update_layout(title="1-star share by app version the reviewer used (versions with 150+ reviews, 95% interval)",
                          height=340, yaxis_title="% of written reviews", xaxis_title="app version (major.minor)",
                          xaxis=dict(type="category"), margin=dict(t=50, b=10))
        st.plotly_chart(fig, width="stretch")

# ----------------------------------------------------------------------------- tab 2
with tab2:
    st.header("Review investigation")
    st.caption("Pick a 1 or 2 star review to see how the rules read it. Confidence here is the measured accuracy of "
               "each theme's rule on hand-labelled samples, not a probability for this one review.")
    code_of = {t["id"]: t["code"] for t in themes}
    by_id = {t["id"]: t for t in themes}
    rx = {t["id"]: re.compile("|".join(f"(?:{p})" for p in t["patterns"]), re.IGNORECASE) for t in themes}
    prec = {}
    if val is not None:
        for _, r in val.iterrows():
            if pd.notna(r.get("precision")):
                prec[r["theme"]] = (r["precision"], r["prec_lo"], r["prec_hi"], int(r["checked_n"]))

    neg = unb[(unb["score"] <= 2) & unb["has_text"]].copy()
    f1, f2, f3, f4 = st.columns([1, 1.4, 1, 1.4])
    stars = f1.multiselect("Stars", [1, 2], default=[1, 2])
    theme_opts = ["(any)"] + [f"{t['code']}: {t['label']}" for t in themes] + ["(no theme matched)"]
    pick_theme = f2.selectbox("Theme", theme_opts)
    years = sorted(neg["at"].dt.year.unique(), reverse=True)
    pick_year = f3.selectbox("Year", ["(any)"] + [str(y) for y in years])
    query = f4.text_input("Contains text")
    v = neg[neg["score"].isin(stars)]
    if pick_theme == "(no theme matched)":
        v = v[v["unspecified"]]
    elif pick_theme != "(any)":
        tid = next(t["id"] for t in themes if t["code"] == pick_theme.split(":")[0])
        v = v[v[f"t_{tid}"]]
    if pick_year != "(any)":
        v = v[v["at"].dt.year == int(pick_year)]
    if query:
        v = v[v["content"].str.contains(query, case=False, regex=False)]
    v = v.sort_values("at", ascending=False)
    st.write(f"{len(v):,} reviews match the filters (showing the newest 300).")
    v = v.head(300)
    if v.empty:
        st.info("No reviews match.")
        st.stop()
    labels = {r["reviewId"]: f"{r['at'].date()} | {int(r['score'])}* | {r['content'][:90]}"
              for _, r in v.iterrows()}
    rid = st.selectbox("Review", list(labels), format_func=lambda k: labels[k])
    row = v[v["reviewId"] == rid].iloc[0]

    left, right = st.columns([1.2, 1])
    with left:
        text = row["content"]
        spans = []
        for t in themes:
            if row[f"t_{t['id']}"]:
                spans += [(m.start(), m.end(), t["code"]) for m in rx[t["id"]].finditer(text)]
        spans.sort()
        out, pos = "", 0
        for s, e, code in spans:
            if s < pos:
                continue
            out += html.escape(text[pos:s]) + f"<mark title='{code}'>{html.escape(text[s:e])}</mark>"
            pos = e
        out += html.escape(text[pos:])
        st.markdown(f"**{int(row['score'])} star | {row['at'].date()} | app version {row['version'] if pd.notna(row['version']) else 'unknown'} | "
                    f"id {str(row['reviewId'])[:8]}**")
        st.markdown(f"<div style='padding:12px;border:1px solid #ccd;border-radius:6px;font-size:1.05rem'>{out}</div>",
                    unsafe_allow_html=True)
        if row["has_reply"]:
            st.markdown("**Developer reply:**")
            st.write(row["replyContent"])
        else:
            st.caption("No developer reply.")
    with right:
        matched = [t for t in themes if row[f"t_{t['id']}"]]
        if not matched:
            st.warning("No theme matched." + (" The review is generic abuse such as 'worst app'."
                                              if row["generic_only"] else " It may still hold a real issue "
                                                                          "the rules miss (about 30% do)."))
        for t in matched:
            p = prec.get(t["id"])
            words = sorted({m.group(0).lower() for m in rx[t["id"]].finditer(row["content"])})
            with st.container(border=True):
                st.markdown(f"**{t['code']}: {t['label']}**")
                st.write(f"Journey stage: {t['stage']}  \nLikely owner: {t['owner']}  \n"
                         f"Severity (analyst judgement, 1-3): {t['severity']}")
                st.write("Triggered by: " + ", ".join(f"`{w}`" for w in words[:8]))
                if p:
                    st.write(f"Rule accuracy measured on {p[3]} reviews: {p[0] * 100:.0f}% "
                             f"(95% range {p[1] * 100:.0f}-{p[2] * 100:.0f}%)")
        st.caption("Owner and severity are who would normally act first, not proof of root cause.")

# ----------------------------------------------------------------------------- tab 3
with tab3:
    st.header("Recommended actions")
    st.caption("Written by the analyst in priorities.yaml, not generated. Each action names what would confirm the "
               "cause in MoveInSync's own data, and how to check the result.")
    need(prio, "reports/priority_table.csv")
    show = prio.copy()
    st.dataframe(show[["rank", "code", "label", "reach_pct" if "reach_pct" in show else "adjusted_reviews",
                       "severity", "fixability", "effort", "score", "pct_sims_top3", "team"]]
                 .rename(columns={"reach_pct": "% of 1-2 star reviews", "pct_sims_top3": "% of random re-rankings in top 3"}),
                 hide_index=True, width="stretch")
    pick = st.selectbox("Open an action", [f"{r['rank']}. {r['code']}: {r['label']}" for _, r in show.iterrows()])
    r = show[show["rank"] == int(pick.split(".")[0])].iloc[0]
    st.subheader(f"{r['code']}: {r['label']}")
    st.markdown(f"**Proposed fix:** {r['fix']}")
    st.markdown(f"**Owner:** {r['team']}  \n**Effort (1-5):** {r['effort']}  \n**Metric to monitor:** {r['metric']}")
    st.markdown(f"**How to verify:** {r['validate']}")
    st.markdown(f"**Internal data that would confirm the cause:** {r['evidence_needed']}")
    st.caption(f"Score {r['score']} = % of 1-2 star reviews x severity x fixability / effort. "
               "Severity, fixability and effort are judgement; change them in priorities.yaml and re-run prioritize.py.")
    imp = load_csv("impact_scenarios.csv")
    if imp is not None:
        with st.expander("Rating impact scenarios (assumption-driven, not forecasts)"):
            st.dataframe(imp, hide_index=True, width="stretch")
