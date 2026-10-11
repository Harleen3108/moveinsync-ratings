# Walkthrough script and interview answers

Numbers below come from data collected 10 Oct 2026. If you re-run the pipeline, update them.

## Three-minute walkthrough (about 450 words, spoken)

**0:00 The question.**
"MoveInSync's employee app is rated 4.64 from about 115,000 ratings, and the case says that is lower than it should
be. I asked two things: what are users actually complaining about, and what should an engineering team do first,
using only evidence I can show."

**0:25 The data, and its limits.**
"I collected public Play Store reviews on 10 October. For rates and trends I used only the newest 15,000 written
reviews of all star ratings, so the mix isn't skewed. I pulled extra 1 and 2 star reviews only to find examples.
One caution: written reviews lean negative. One star is 6.4% of ratings but about 27% of written reviews, so I use
reviews to compare themes and trends, never as the rating itself."

**1:00 How I tagged complaints, and how I checked it.**
"I defined 16 complaint themes with plain keyword rules, each tied to a stage of the commute and a likely owner.
Keyword rules can be wrong, so I measured them. I labelled 343 random reviews in three rounds, fixed the rules where
they failed, and re-checked on fresh reviews. Precision is 75 to 100% for most themes, 50% for late-cab complaints.
The labels were made by the AI assistant that helped me write the rules, so the numbers are probably optimistic;
I re-labelled 30 rows myself and we agreed on 28."

**1:40 What I found.**
"First, ratings are not getting worse: the one-star share was 25.0% in the last 90 days and 24.9% before.
Second, developer replies collapsed: 97% of one and two star reviews got a reply in 2023, 1% in 2026.
Third, the specific complaints are booking and cancel problems, the app not opening, forced logouts, slowness and
OTP. Late cabs and driver behaviour exist but look like operations problems. Crash complaints also rose from
about 5% to 13% of negative reviews since August, which my weekly monitor flags independently."

**2:15 What I recommend.**
"Tomorrow: check Play Console for crashes in releases since August. Then fix the booking flow, then forced logins.
Restart replies to negative reviews. Each fix has an owner, a metric and a way to check it."

**2:40 What it's worth, and what it isn't.**
"Even if every one and two star became a five, the average only reaches 4.92. My scenarios put the top three fixes
at roughly plus 0.01 to plus 0.05. I'd say that's a scenario, not a promise."

**2:55 Repeatable.**
"One command re-collects, re-tags and runs a monitor that says OK, WATCH or ALERT, and refuses to report stale data.
With internal data I'd confirm each cause in crash logs, booking funnels, auth and OTP logs before building."

## Questions you should be ready for

**How was the data collected?**
google-play-scraper 1.2.7, newest-first, in three jobs. 21,765 raw rows became 16,546 unique reviews after removing
duplicate review ids. It is an unofficial library, so it may miss reviews; that is a stated limit.

**Is the sample representative?**
Of written reviews, yes for the unbiased 15,000 (newest, all stars). Of all users, no: only about a fifth of ratings
carry text and unhappy users write more. I treat it as a map of complaints, not a measure of satisfaction.

**Why keyword rules and not an LLM?**
They are transparent, free, deterministic and anyone can re-run them. An LLM would need an API key, vary between runs
and be harder to defend. Cost is lower recall (about 30% of unmatched reviews still name a theme), which I measured.

**How did you validate the categories?**
Three rounds of random samples, 8 reviews per theme plus 40 unmatched, fixed after rounds 1 and 2 and re-measured on
fresh reviews. Precision with 95% Wilson intervals; with n = 8 the intervals are wide and I say so.

**The labels were AI-made. Why trust them?**
Don't take them as independent. The same assistant wrote the rules, so the accuracy is probably high. My own
re-labelling of 30 rows agreed on 28. A second human labeller on more rows is the next step.

**How is the priority score calculated?**
Reach (% of 1-2 star reviews, after the precision adjustment) x severity (1-3) x fixability (0-1) / effort (1-5).
Severity, fixability and effort are my judgement and are in priorities.yaml. I changed all of them at random 5,000
times: booking stayed in the top three 92% of the time, crashes 88%, login 59%.

**Booking ranks first, so why do crashes come first?**
Checking crashes takes hours in Play Console, the reviews show a recent rise, and an app that won't open blocks
every journey. Booking is the first thing to build, not the first thing to check.

**Why is the rating gain so small?**
91.8% of ratings are already 4 or 5 stars. Only the 7.1% that are 1 or 2 stars can move the average, and the
ceiling is 4.92. The bigger gain is fewer failed commutes, which reviews can't measure.

**How would you prove a root cause?**
Reviews say what users felt, not why. For crashes: Play Console clusters by device, OS and version. For booking:
funnel drop-off and API failures. For logins: token expiry and refresh logs around the 2025 spikes. For OTP: SMS
delivery reports by operator. For tracking: GPS ping age per vendor.

**How would you measure success after a fix?**
Crash-free sessions, booking completion, forced sign-outs per user, OTP delivery rate, GPS ping age, plus the share
of new 1-2 star reviews naming that theme, in a staged rollout (10%, 50%, 100%) with before and after.

**Did you test many things at once?**
Yes, 16 themes. I only flag a theme when it moves 5+ points with p < 0.01, and I call a flag a place to look, not proof.

**What's unexplained?**
The sample holds 1,165 two-star reviews against 837 two-star ratings on the listing. I couldn't explain it, so I don't
compare those two numbers. I also don't know why replies stopped; I only know they did.

**How would this run reliably every week?**
`collect.py --refresh`, then clean, classify, analyze, monitor. The monitor exits with an error if the newest review
is older than 7 days. Scheduling with GitHub Actions or cron is not built, and Play Store access from hosted runners is
unverified.

**What would you do with MoveInSync's own data?**
Replace guesses with measurements: real crash clusters, booking funnels, auth and OTP logs, trip logs with scheduled vs
actual pickup times by vendor. I'd re-rank the fixes with measured reach and effort from the engineering team.

**What would you do differently with more time?**
A second human labeller on a larger sample, a larger validation set per theme, tracking review text in Hindi/Hinglish
properly, and a controlled check of whether replying to reviews changes ratings.
