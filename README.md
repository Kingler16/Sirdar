<div align="center">

<img src="docs/logo.png" alt="Sirdar" width="120" height="120" />

# Sirdar

### AI training that adapts to you.

A self-hosted, open-source AI training coach for **road cycling + strength** that builds your plan and re-tunes it every day — running entirely on **your own Claude subscription**, for **0 € in AI costs**.

[![License: MIT](https://img.shields.io/badge/License-MIT-CC6A4E.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3E9E89.svg)](https://www.python.org/)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-5FA47E.svg)](#contributing)
[![Status: early development](https://img.shields.io/badge/status-early%20development-E0A33E.svg)](#roadmap)

</div>

---

## What it does

Sirdar is a personal coach that lives on your own hardware. It builds a periodized training plan for **road cycling** and **strength training** (more sports planned), then **adapts it daily** to your readiness, the weather, and your calendar — recovering a missed session, softening a hard day after a bad night's sleep, or moving a ride indoors when it's raining.

The "brain" is the [Claude Code CLI](https://claude.com/claude-code) driven through **your existing Claude Max/Pro subscription**. No metered API keys, no per-token billing — **0 € in AI costs**. One instance serves one user and runs happily on a Raspberry Pi or RockPi.

## Features

| | Feature | What you get |
|---|---|---|
| 🚴 | **Cycling load model** | FTP-based zones, per-session TSS, and a Fitness/Fatigue/Form model (CTL · ATL · TSB) |
| 🏋️ | **Strength progression** | Volume and RIR/RPE-driven progression with prescribed weight × sets × reps |
| 🔀 | **Concurrent training** | Schedules bike + strength together to minimize the interference effect |
| 🚦 | **Readiness traffic light** | HRV trend, resting HR, sleep and a subjective check → 🟢 / 🟡 / 🔴 |
| 💬 | **Conversational AI coach** | Talk to your coach in the web app — the plan is built *with* you, not handed down |
| 📥 | **FIT / GPX import** | Drop activity files in the browser or a watch folder; deduplicated automatically |
| 🔌 | **Pluggable data** | Garmin · Strava · Apple Health · file import — enable only what you have |
| 🗺️ | **Route planning** *(planned)* | Wind-aware routes via OpenRouteService (headwind out, tailwind home) |
| 📱 | **PWA** | Installable on your phone, works offline-friendly, no app store |
| 🌍 | **DE / EN** | Fully bilingual interface |
| 🥧 | **RockPi / Pi deploy** | Always-on, low-power, runs from systemd + cron |

## The training brain

This is the part that makes Sirdar a *coach* and not a spreadsheet. The AI works inside hard sport-science guardrails (see [`KONZEPT.md`](KONZEPT.md) §6):

- **Cycling normalization** — every ride is anchored to your **FTP**: power zones and a **TSS** score per session.
- **Load steering** — a rolling **CTL / ATL / TSB** model (fitness, fatigue, form) with a **ramp-rate guardrail of ≤ +3–5 CTL/week** so you build without digging a hole.
- **Concurrent-training rules** — leg-heavy strength is spaced away from key bike sessions; *bundle the hard days, bundle the easy days*, sequence by your primary goal, and protect recovery days.
- **Readiness auto-regulation** — every morning the plan reacts to your body:
  - 🟢 **Green** — train as prescribed.
  - 🟡 **Yellow** — soften the hard one (VO2max → Sweet Spot, strength RPE −1).
  - 🔴 **Red** — swap the key session for recovery and rebalance the week.
- **Principles** — *shift, don't skip · when in doubt, reduce · preserve the block · adherence beats the theoretical optimum.*

## Quick start

> **Prerequisites:** Python **3.11+** and the [Claude Code CLI](https://claude.com/claude-code) authenticated with a **Claude Max or Pro** subscription. No paid API key required.

```bash
# 1. Clone
git clone https://github.com/Kingler16/Sirdar.git
cd Sirdar

# 2. Create a virtualenv and install
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure (copy the examples, then edit)
cp config/settings.example.json config/settings.json
cp config/profile.example.json  config/profile.json

# 4. Run the web app
python -m src.main web
```

Open **http://localhost:8000** and walk through onboarding (your sports, goals, and current numbers) right in the browser. Import a FIT/GPX file from `/import`, then ask the coach to build your first plan.

Other CLI modes: `python -m src.main collect` (ingest data) · `python -m src.main plan` (regenerate the plan).

### How Claude is used (zero API cost)

Sirdar never calls a metered API. Instead it shells out to the Claude Code CLI you already pay for:

```
context.json  ──▶  claude --print  (system prompt: "experienced cycling + strength coach")  ──▶  plan.json
```

Your Claude Max/Pro subscription is the only "AI bill" — the same login you use in your terminal. The CLI wrapper handles the OAuth token and file-locking; an optional Anthropic-API provider can be added later if you prefer.

## Architecture

Self-hosted, **one instance = one user**, built to run on a Raspberry Pi / RockPi.

```
            ┌──────────────── Raspberry Pi / RockPi (always-on, 1 user) ─────────────────┐
            │                                                                              │
 PWA ───────▶  FastAPI  ──reads──▶  plan.json + training.db                                │
 (Today,    │   ├─ /today /week                                                            │
  Health,   │   ├─ /health   ◀── CTL · ATL · TSB · readiness                               │
  Chat,     │   ├─ /chat ───▶ Claude CLI (streaming coach chat)                            │
  Goals)    │   └─ /import   ◀── FIT / GPX upload                                          │
            │                                                                              │
            │  collect.py (cron, daily)                                                    │
            │    ├─ [plugin] Garmin / Strava / file import  ─▶ workouts                    │
            │    ├─ [plugin] Apple Health push              ─▶ health_metrics              │
            │    ├─ Open-Meteo (weather)   └─ CalDAV (free calendar slots)                 │
            │              │                                                               │
            │  compute  ─▶ FTP / TSS · CTL/ATL/TSB · readiness score                       │
            │              │                                                               │
            │  plan_agent ─▶ context JSON ─▶ claude --print ─▶ plan.json + "why"           │
            └──────────────────────────────────────────────────────────────────────────────┘
```

- **Backend:** Python 3.11+ · FastAPI · Uvicorn
- **Frontend:** Jinja2 + HTMX + Chart.js — no Node build · PWA (service worker, web push, home-screen install)
- **Storage:** SQLite (time series) + JSON files (human-readable profile / goals / settings)
- **AI:** Claude Code CLI (`claude --print`) via subprocess
- **Scheduling / deploy:** systemd + cron

## Data sources

Every integration is an optional plugin under `src/data/sources/`; you enable what you have in `settings.json`.

| Source | Provides | Cost / caveat | Status |
|---|---|---|---|
| **File import (FIT / GPX)** | Activities, power, HR, GPS | Free, no risk | ✅ Available |
| **Open-Meteo** | Hourly wind / rain / temp | Free, no key | Phase 2 |
| **CalDAV / .ics** | Calendar → free slots | Free | Phase 2 |
| **Apple Health (iOS Shortcut)** | HRV, sleep, RHR, workouts | Free, per-user setup | Phase 3 |
| **Garmin (official API)** | Activities + HRV / sleep | OAuth, "business" application | Phase 3 |
| **Strava** | Activities, GPS, power | ⚠️ paid tier from 2026-06-30 **and** AI/ML use restricted by their terms | Phase 3 (your call) |
| **OpenRouteService** | Routes + elevation | Free (daily quota) | Phase 3 |

> ⚠️ **A note on Strava.** Strava's API terms restrict AI/ML use of its data and require a paid tier from mid-2026. As an open-source plugin, the decision and legal responsibility to enable it sit with each self-hoster. **Recommended default: Garmin + file import.**

## Roadmap

| Phase | Scope | |
|---|---|---|
| **0 — Foundation** | Project skeleton, Claude CLI wrapper, FastAPI / PWA shell, SQLite schema, i18n | ✅ done |
| **1 — MVP** | Onboarding · goals · FIT/GPX import · health overview · AI plan generation (bike + strength) | ✅ done |
| **2 — Adaptive** | Weather (Open-Meteo) · calendar (CalDAV) · daily readiness auto-regulation · coach chat | 🚧 in progress |
| **3 — Data plugins** | Garmin / Strava / Apple-Health plugins · route planning (OpenRouteService) | ⏳ planned |
| **4 — Release polish** | Periodization view · setup wizard · deploy scripts · docs → public release | ⏳ planned |

See [`KONZEPT.md`](KONZEPT.md) for the full vision, data model, and sport-science rationale.

## Contributing

PRs are welcome — new data-source plugins and sport types are especially appreciated (the plugin interface lives in `src/data/sources/`). Please keep secrets out of the repo: ship `*.example.json`, never your filled-in config.

## License

[MIT](LICENSE) © 2026

---

> ⚠️ **Disclaimer.** Sirdar is an educational, hobby project. It is **not medical advice and not professional training advice**, and the AI can be wrong. Consult a doctor before starting or changing a training program. You train at your own risk.
