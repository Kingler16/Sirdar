# 🏔️ Sirdar

> Your AI training expedition leader. A self-hosted, open-source AI coach that builds and adapts your training plans — running entirely on **your own Claude subscription** (zero API cost).

*A **Sirdar** is the lead Sherpa of a Himalayan expedition — the one who plans the route, reads the conditions, and guides the team to the summit. That's exactly what this app does for your training.*

---

## What it does

Sirdar creates personalized training plans for **road cycling** and **strength training** (more sports planned) and **adapts them daily** to your health, the weather, and your calendar:

- 🧠 **AI plans, zero API cost** — uses the [Claude Code CLI](https://claude.com/claude-code) via your own Claude subscription, like a local brain. No pay-per-use API keys.
- 🚴 **Cycling** — FTP-based zones, TSS, and a Fitness/Fatigue/Form model (CTL/ATL/TSB) with a ramp-rate guardrail.
- 🏋️ **Strength** — volume- and RIR/RPE-based progression, periodization, deloads.
- 🔀 **Concurrent training** — schedules cycling + strength together intelligently (minimizing the interference effect).
- 🚦 **Daily auto-regulation** — reads HRV trend, resting HR, sleep & how you feel → green/yellow/red → adjusts today's session instead of blindly following the plan.
- 🌦️ **Context-aware** — fits sessions around your calendar and the weather (indoor vs. outdoor, headwind-out/tailwind-home routing).
- 💬 **Coach chat** — talk to your coach right in the web app.
- 🔌 **Pluggable data** — file import (FIT/GPX), Garmin, Strava, Apple Health (Shortcut) — enable what you have.
- 📱 **PWA** — installable on your phone, works on web. Bilingual (DE/EN).

## Status

🚧 **Early development.** See [`KONZEPT.md`](KONZEPT.md) for the full concept & roadmap.

## Architecture (at a glance)

Self-hosted, **one instance = one user** (like its sibling project, a personal stock advisor). Built to run on a Raspberry Pi / RockPi.

- **Backend:** Python · FastAPI
- **Frontend:** Jinja2 + HTMX + Chart.js (no Node build) · PWA
- **Storage:** SQLite + JSON files (human-readable, no DB server)
- **AI:** Claude Code CLI (`claude --print`) as a subprocess
- **Scheduling:** systemd + cron

## Roadmap

| Phase | Scope |
|---|---|
| **0** | Foundation: project skeleton, Claude CLI wrapper, FastAPI/PWA shell, SQLite schema |
| **1 (MVP)** | Onboarding · Goals · FIT/GPX import · Health Overview · AI plan generation (bike + strength) |
| **2** | Weather (Open-Meteo) · Calendar (CalDAV) · daily readiness auto-regulation · Coach chat |
| **3** | Garmin / Strava / Apple-Health plugins · route planning (OpenRouteService) |
| **4** | Periodization view · setup wizard · deploy scripts · docs → public release |

## License

[MIT](LICENSE)

---

> ⚠️ **A note on data sources:** Some integrations have their own terms. In particular, Strava's API terms restrict AI/ML use of its data and require a paid tier from mid-2026. Each self-hoster decides which plugins to enable on their own instance. Recommended default: **Garmin + file import**.
