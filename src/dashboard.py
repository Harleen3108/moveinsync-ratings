"""Review Intelligence Dashboard (Streamlit). Reads the files the pipeline already wrote; computes nothing new
except the keyword highlighting in the investigation tab and a few headline sentences built from those files.

Run (project root, venv active, after collect/clean/classify/analyze/validate/prioritize/monitor/deep_dive):
    streamlit run src/dashboard.py

Tabs
  1. Overview             one-paragraph answer, headline numbers, trends, replies, themes, versions, what happy users say
  2. Review investigation browse written reviews (low ratings first, any star rating available), see the topic(s),
                          owner, the exact words that triggered each topic and the measured accuracy of that rule
  3. Recommended actions  the ranked fixes as cards (written by the analyst in priorities.yaml, not generated)
"""
import html
import json
import random
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
praise = load_csv("praise_vs_complaint_terms.csv")
summ_path = PROC / "clean_summary.json"
clean_summary = json.loads(summ_path.read_text()) if summ_path.exists() else {}

unb = reviews[reviews["in_newest_all"]]
newest = unb["at"].max()
age = (pd.Timestamp.now().normalize() - newest.normalize()).days
theme_by_code = {t["code"]: t for t in themes}


def stars_text(n):
    return f"{int(n)}/5 ★"


# ----------------------------------------------------------------------------- sidebar
st.sidebar.title("Review Intelligence")
st.sidebar.caption("com.moveinsync.ets (employee commute app)")
st.sidebar.markdown(f"**Newest review in data:** {newest.date()}")
st.sidebar.markdown(f"**Sample used for trends:** {len(unb):,} newest reviews, all star ratings")
_h = {int(k): v for k, v in prio_cfg["rating_histogram"].items()}
_mean = sum(k * v for k, v in _h.items()) / sum(_h.values())
st.sidebar.info("People who write reviews are more likely to be unhappy. Use these reviews to see what goes wrong "
                "and whether it is getting worse. For the app's actual rating, use the "
                f"{_mean:.2f} from all ratings.")
st.sidebar.caption("Focus is on low ratings (1-2 stars), where the problems are. Other star ratings are available "
                   "in the Review investigation tab.")

if age > 7:
    st.error(f"The data is {age} days old, so the numbers below may not be current. "
             "Run the weekly refresh (see README) before relying on them.")

with st.expander("How to read this dashboard (plain-language glossary)"):
    st.markdown(
        "- **Topic / theme**: a type of complaint, found by keyword rules (for example login, booking, crashes). "
        "A review can have several topics, or none.\n"
        "- **Precision**: when the rule says a review has a topic, how often a person agreed, measured on a "
        "hand-checked sample. Labels were made by an AI assistant, so treat precision as optimistic.\n"
        "- **Share of 1-2 star reviews**: how many low-rated written reviews mention the topic (adjusted by precision).\n"
        "- **Owner**: which team would normally act first. It is not proof of the cause.\n"
        "- **Severity, fixability, effort**: analyst judgement (not measured), used to rank fixes.\n"
        "- **Priority score**: share x severity x fixability / effort. Higher means fix sooner.\n"
        "- **Stays in top 3**: how often a topic stays in the top 3 when those judgements are changed at random "
        "5,000 times. High means the ranking is not fragile.\n"
        "- **Weekly monitor**: OK (nothing unusual), WATCH (something moved, take a look), ALERT (a clear worsening), "
        "STALE (data too old to report).")

tab1, tab2, tab3 = st.tabs(["Overview", "Review investigation", "Recommended actions"])

# ----------------------------------------------------------------------------- tab 1
with tab1:
    st.header("Overview")
    hist = {int(k): v for k, v in prio_cfg["rating_histogram"].items()}
    total = sum(hist.values())
    mean = sum(k * v for k, v in hist.items()) / total
    rec = unb[unb["at"] > newest - pd.Timedelta(days=90)]
    prev = unb[(unb["at"] > newest - pd.Timedelta(days=180)) & (unb["at"] <= newest - pd.Timedelta(days=90))]
    s_rec, s_prev = (rec["score"] == 1).mean() * 100, (prev["score"] == 1).mean() * 100
    diff = s_rec - s_prev
    trend_word = ("about the same as" if abs(diff) < 2 else ("higher than" if diff > 0 else "lower than"))

    # --- the answer in a few lines
    status, flagged = "OK", []
    if flags is not None:
        lv = flags["level"].tolist()
        status = ("STALE" if "STALE" in lv else "ALERT" if "ALERT" in lv else "WATCH" if "WATCH" in lv else "OK")
        for _, f in flags[flags["level"].isin(["ALERT", "WATCH"])].iterrows():
            chk = str(f["check"])
            if chk.startswith("theme "):
                t = theme_by_code.get(chk.split(" ", 1)[1])
                flagged.append(f"more complaints about {t['label'].lower()}" if t else chk)
            elif chk == "replies":
                flagged.append("few developer replies to low ratings")
            elif chk.startswith("version"):
                flagged.append(f"app {chk} looks worse than the rest")
            elif chk == "1-star share":
                flagged.append("1-star reviews are rising")
            else:
                flagged.append(chk)
    with st.container(border=True):
        st.markdown(
            f"- **Rating:** {mean:.2f} from {total:,} ratings ({(hist[4] + hist[5]) / total * 100:.1f}% are 4-5 stars; "
            f"only {(hist[1] + hist[2]) / total * 100:.1f}% are 1-2 stars).\n"
            f"- **Direction:** the 1-star share of written reviews in the last 90 days is {s_rec:.1f}%, "
            f"{trend_word} the previous 90 days ({s_prev:.1f}%).\n"
            f"- **Weekly monitor: {status}.** "
            + (("Worth a look: " + "; ".join(flagged) + ".") if flagged else "Nothing unusual flagged.") + "\n"
            + (f"- **Highest-priority fix by score:** {prio.iloc[0]['code']}, {prio.iloc[0]['label'].lower()} "
               f"(owner: {prio.iloc[0]['team']}). See the Recommended actions tab." if prio is not None else ""))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Average rating (observed 10 Oct 2026)", f"{mean:.2f}")
    c2.metric("Ratings", f"{total:,}")
    c3.metric("1-2 star share of ratings", f"{(hist[1] + hist[2]) / total * 100:.1f}%")
    c4.metric("1-star share of written reviews, last 90 days", f"{s_rec:.1f}%",
              f"{diff:+.1f} pts vs previous 90 days", delta_color="inverse")

    if flags is not None:
        with st.expander(f"Weekly monitor details: {status}", expanded=status not in ("OK",)):
            st.dataframe(flags[["level", "check", "detail"]], hide_index=True, width="stretch")
    else:
        st.info("Run `python src/monitor.py` to see the weekly status here.")

    a, b = st.columns(2)
    with a:
        hdf = pd.DataFrame({"stars": [str(k) for k in sorted(hist)], "ratings": [hist[k] for k in sorted(hist)]})
        fig = px.bar(hdf, x="stars", y="ratings", title="Ratings by star (observed on the Play Store listing)")
        fig.update_traces(marker_color=[RED, RED, GREY, BLUE, BLUE])
        fig.update_layout(height=320, margin=dict(t=50, b=10))
        st.plotly_chart(fig, width="stretch")
        st.caption("Red bars are the low ratings that this analysis focuses on.")
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
            fig.update_layout(title="Share of written reviews that are 1 star, by month", height=320,
                              yaxis_title="% of written reviews", margin=dict(t=50, b=10), showlegend=False)
            fig.update_yaxes(rangemode="tozero")
            st.plotly_chart(fig, width="stretch")
            st.caption("Shaded band = plausible range given the number of reviews. The latest month is partial, "
                       "and months with few reviews are noisy.")

    a, b = st.columns(2)
    with a:
        if replies is not None:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=replies["year"], y=replies["1-2 star"], mode="lines+markers",
                                     name="1-2 star reviews", line=dict(color=RED)))
            fig.add_trace(go.Scatter(x=replies["year"], y=replies["all"], mode="lines+markers",
                                     name="all written reviews", line=dict(color=GREY)))
            fig.update_layout(title="How often the developer replied, by year of the review", height=320,
                              yaxis_title="% with a reply", yaxis_range=[0, 105], margin=dict(t=50, b=10),
                              legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig, width="stretch")
            peak = replies.loc[replies["1-2 star"].idxmax()]
            last = replies.iloc[-1]
            st.caption(f"Replies to 1-2 star reviews: {peak['1-2 star']:.0f}% in {int(peak['year'])}, "
                       f"{last['1-2 star']:.0f}% in {int(last['year'])}. The data cannot say why it changed.")
    with b:
        if val is not None:
            v = val.dropna(subset=["adjusted"]).sort_values("adjusted")
            n_neg = int(((unb["score"] <= 2) & unb["has_text"]).sum())
            v["share"] = v["adjusted"] / n_neg * 100
            names = {t["code"]: t["label"] for t in themes}
            v["label"] = v["code"].map(names)
            fig = px.bar(v, x="share", y="label", orientation="h", hover_data=["adjusted", "precision"],
                         title=f"What low-rated reviews complain about ({n_neg:,} reviews rated 1-2 stars)")
            fig.update_traces(marker_color=INK)
            fig.update_layout(height=420, xaxis_title="% of 1-2 star written reviews", yaxis_title=None,
                              margin=dict(t=50, b=10, l=10))
            st.plotly_chart(fig, width="stretch")
            st.caption("A review can name several topics, so the bars do not add up to 100%. "
                       "Counts are keyword matches adjusted by measured precision.")
    if versions is not None:
        vv = versions[versions["enough_n"]]
        fig = go.Figure(go.Bar(x=vv["version_mm"], y=vv["share_1star"] * 100, marker_color=INK,
                               error_y=dict(type="data", symmetric=False,
                                            array=(vv["hi"] - vv["share_1star"]) * 100,
                                            arrayminus=(vv["share_1star"] - vv["lo"]) * 100),
                               customdata=vv["n"], hovertemplate="version %{x}<br>%{y:.1f}% 1-star "
                                                                  "(n=%{customdata})<extra></extra>"))
        fig.update_layout(title="Share of 1-star reviews, by the app version the reviewer used",
                          height=340, yaxis_title="% of written reviews", xaxis_title="app version (major.minor)",
                          xaxis=dict(type="category"), margin=dict(t=50, b=10))
        st.plotly_chart(fig, width="stretch")
        st.caption("Only versions with 150+ reviews. Whiskers show the plausible range; bars whose whiskers overlap "
                   "are not clearly different.")

    if praise is not None:
        st.subheader("What happy and unhappy reviewers talk about")
        long = praise.melt(id_vars="word_group", var_name="group", value_name="pct")
        long["group"] = long["group"].map({"pct_of_4_5_star": "4-5 star reviews", "pct_of_1_2_star": "1-2 star reviews"})
        fig = px.bar(long, x="pct", y="word_group", color="group", orientation="h", barmode="group",
                     color_discrete_map={"4-5 star reviews": BLUE, "1-2 star reviews": RED},
                     title="Share of reviews using each group of words")
        fig.update_layout(height=460, xaxis_title="% of reviews in that group", yaxis_title=None,
                          margin=dict(t=50, b=10), legend_title_text="")
        st.plotly_chart(fig, width="stretch")
        st.caption("Simple keyword groups, not hand-validated. 4-5 star reviews are usually only a few words long, "
                   "so they say little about what users value.")

# ----------------------------------------------------------------------------- tab 2
GROUPS = {"Low ratings (1-2 stars)": [1, 2], "Middle (3 stars)": [3], "High ratings (4-5 stars)": [4, 5],
          "All ratings": [1, 2, 3, 4, 5]}
with tab2:
    st.header("Review investigation")
    st.caption("Read real reviews and see how the rules tag them. Start with low ratings, or switch to any other "
               "star rating. The accuracy shown is measured on hand-labelled samples, not a probability for one review.")
    by_id = {t["id"]: t for t in themes}
    rx = {t["id"]: re.compile("|".join(f"(?:{p})" for p in t["patterns"]), re.IGNORECASE) for t in themes}
    prec = {}
    if val is not None:
        for _, r in val.iterrows():
            if pd.notna(r.get("precision")):
                prec[r["theme"]] = (r["precision"], r["prec_lo"], r["prec_hi"], int(r["checked_n"]))

    grp = st.radio("Which reviews?", list(GROUPS), horizontal=True)
    pool = unb[unb["has_text"] & unb["score"].isin(GROUPS[grp])].copy()
    if grp != "Low ratings (1-2 stars)":
        st.info("The topic rules were built and checked on 1-2 star complaints. On higher ratings a match may be "
                "praise (for example 'tracking is great'), so read those as topics, not complaints.")

    counts = {t["id"]: int(pool[f"t_{t['id']}"].sum()) for t in themes}
    n_none = int(pool["unspecified"].sum())
    opts = {f"Any topic ({len(pool):,})": None}
    for t in sorted(themes, key=lambda t: -counts[t["id"]]):
        opts[f"{t['code']}: {t['label']} ({counts[t['id']]:,})"] = t["id"]
    opts[f"No topic matched ({n_none:,})"] = "__none__"
    topic_label = st.selectbox("Topic", list(opts))
    topic = opts[topic_label]

    with st.expander("More filters (year, text)"):
        years = sorted(pool["at"].dt.year.unique(), reverse=True)
        pick_year = st.selectbox("Year", ["(any)"] + [str(y) for y in years])
        query = st.text_input("Contains text")

    v = pool
    if topic == "__none__":
        v = v[v["unspecified"]]
    elif topic:
        v = v[v[f"t_{topic}"]]
    if pick_year != "(any)":
        v = v[v["at"].dt.year == int(pick_year)]
    if query:
        v = v[v["content"].str.contains(query, case=False, regex=False)]
    v = v.sort_values("at", ascending=False)
    st.write(f"**{len(v):,}** reviews match (the newest 300 are listed).")
    v = v.head(300)
    if v.empty:
        st.info("No reviews match these choices.")
    else:
        labels = {r["reviewId"]: f"{r['at'].date()} | {stars_text(r['score'])} | {r['content'][:90]}"
                  for _, r in v.iterrows()}
        ids = list(labels)
        # one widget identity per filter combination, so a stale selection from another topic can never linger
        rkey = f"rid|{grp}|{topic}|{pick_year}|{query}"
        st.session_state["_ids"] = ids
        st.session_state["_rkey"] = rkey

        def _random_pick():
            st.session_state[st.session_state["_rkey"]] = random.choice(st.session_state["_ids"])

        st.button("Show me another example", on_click=_random_pick)
        rid = st.selectbox("Review", ids, format_func=lambda k: labels[k], key=rkey)
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
            st.markdown(f"**{stars_text(row['score'])} | {row['at'].date()} | app version "
                        f"{row['version'] if pd.notna(row['version']) else 'unknown'} | id {str(row['reviewId'])[:8]}**")
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
                if row["score"] >= 4:
                    st.success("No complaint topic matched. This is a positive review, so that is expected.")
                elif row["score"] == 3:
                    st.info("No complaint topic matched. This is a mixed (3 star) review; read it to see what the "
                            "rules do not cover.")
                else:
                    st.warning("No topic matched." + (" The review is generic, such as 'worst app'."
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
            st.caption("Owner and severity show who would normally act first, not proof of the root cause.")

# ----------------------------------------------------------------------------- tab 3
with tab3:
    st.header("Recommended actions")
    st.caption("Ranked by priority score. Written by the analyst in priorities.yaml, not generated. Each card says what to "
               "do, who owns it, what to measure, and what in MoveInSync's own data would confirm the cause.")
    need(prio, "reports/priority_table.csv")
    st.info("The score ranks what to build. Anything the weekly monitor flags (see Overview) is also worth a quick "
            "check first, because checking is cheaper than building.")
    for _, r in prio.iterrows():
        with st.container(border=True):
            top = "  |  Top 3 by score" if int(r["rank"]) <= 3 else ""
            st.markdown(f"#### #{int(r['rank'])}  {r['code']}: {r['label']}{top}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Share of 1-2 star reviews", f"{r['reach_pct']:.1f}%")
            m2.caption("Owner")
            m2.markdown(f"**{r['team']}**")
            m3.metric("Effort (1 easy - 5 hard)", f"{int(r['effort'])}")
            m4.metric("Stays in top 3", f"{r['pct_sims_top3']:.0f}%")
            st.markdown(f"**What to do:** {r['fix']}")
            st.markdown(f"**Metric to watch:** {r['metric']}")
            st.markdown(f"**Check first, in MoveInSync's own data:** {r['evidence_needed']}")
            with st.expander("How to verify it worked, and how the score is built"):
                st.markdown(f"**After the fix:** {r['validate']}")
                st.caption(f"Score {r['score']} = {r['reach_pct']:.1f}% x severity {r['severity']} x fixability "
                           f"{r['fixability']} / effort {int(r['effort'])}. Severity, fixability and effort are judgement; "
                           "change them in priorities.yaml and re-run prioritize.py.")
    with st.expander("See the numbers behind the ranking (table)"):
        show = prio[["rank", "code", "label", "reach_pct", "severity", "fixability", "effort", "score",
                     "pct_sims_top3", "team"]].rename(columns={
            "reach_pct": "% of 1-2 star reviews", "pct_sims_top3": "% of re-rankings in top 3"})
        st.dataframe(show, hide_index=True, width="stretch")
    imp = load_csv("impact_scenarios.csv")
    if imp is not None:
        with st.expander("What fixing these could do to the rating (scenarios under stated assumptions, not forecasts)"):
            st.dataframe(imp, hide_index=True, width="stretch")
