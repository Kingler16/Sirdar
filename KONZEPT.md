# Sirdar — KI-gestützter Trainings-Coach (Konzept & Plan)

> **Arbeitstitel: „Sirdar"** (der leitende Sherpa einer Expedition, der plant & führt — finale Namenswahl noch offen, siehe §11).
> Stand: 2026-06-29

---

## 1. Vision in einem Satz

Ein **selbst-gehosteter, quelloffener KI-Trainings-Coach**, der — wie Velora für Aktien — komplett über das **eigene Claude-Abo (CLI, 0 € API-Kosten)** läuft, personalisierte Trainingspläne für **Rennrad + Krafttraining** (erweiterbar) erstellt und diese **täglich an Gesundheit, Wetter und Terminkalender anpasst**.

## 2. Leitprinzipien (aus den Antworten festgezurrt)

| Entscheidung | Festlegung |
|---|---|
| **Verteilungsmodell** | Open Source, **self-hosted, eine Instanz = ein User** (wie Velora). Kein Multi-Tenant, keine Cloud-Pflicht. |
| **KI-Backend** | **Claude Code CLI** (`claude --print`, eigenes Abo) — Provider-Wrapper kapselt das, API-Pfad optional nachrüstbar. |
| **Hosting** | **RockPi / Raspberry Pi** (always-on), wie Velora. |
| **„Flexibel"** | = **konfigurierbar**: jeder User aktiviert seine Sportarten, Ziele, Datenquellen, Autonomie-Grad selbst. |
| **Datenquellen** | **Alle als optionale Plug-ins**: Garmin (offizielle API), Strava, Datei-Import (FIT/GPX), Apple Health (Kurzbefehl-Push). User wählt. |
| **Sprache** | Zweisprachig **DE/EN** (i18n wie Velora). |
| **KI-Autonomie** | **Einstellbar**: (i) Plan automatisch anpassen + informieren, oder (ii) Vorschläge zur Bestätigung. |
| **Coach-Chat** | Ja — ausschließlich über die **Web-App** (kein Telegram). |
| **Ernährung** | Vorerst **nicht** (späterer Ausbau). |
| **Routenplanung** | **Ja**, sehr erwünscht (OpenRouteService, kostenlos). |
| **Top-3 MVP-Features** | 1) Trainingsplan mit **Strecke/Übungen**, 2) **Ziele**, 3) **Health-Overview**. |

## 3. Tech-Stack (= Velora-Stack, ~70 % wiederverwendbar)

- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **Frontend:** Jinja2 + HTMX + Vendor-JS-Charts (Chart.js) — **kein Node-Build**, als **PWA** (Service Worker, Web Push, Homescreen-Install)
- **Daten:** **SQLite** (Zeitreihen: Workouts, Health, Plan, Chat) + **JSON-Files** (Profil, Ziele, Settings — menschenlesbar & von Claude direkt schreibbar)
- **KI:** Claude Code CLI via `subprocess` (Wrapper aus Velora: `claude.py` für Pläne, `claude_stream.py` für Chat)
- **Scheduling/Deploy:** systemd + cron auf dem RockPi
- **i18n:** DE/EN
- **Integrationen:** Garmin API, Strava API, FIT/GPX-Parser, Apple-Health-Shortcut-Endpoint, Open-Meteo (Wetter), CalDAV/.ics (Kalender), OpenRouteService (Routen)

### Direkt aus Velora portierbar
`claude.py` (CLI-Wrapper inkl. OAuth-Token-Handling & File-Lock) · `claude_stream.py` (Streaming-Chat) · `config_loader.py` · FastAPI-/Jinja2-/PWA-Grundgerüst · i18n-Framework · Memory-/JSON-Pattern (atomic writes, Backups) · `setup.py`-Wizard · Cron/systemd-Deploy-Skripte.

## 4. System-Architektur

```
                ┌────────────────────── RockPi (always-on, 1 User) ──────────────────────┐
                │                                                                          │
 PWA / Browser ─▶  FastAPI                          cron/systemd                           │
 (Plan, Health, │   ├─ /today  /week   ◀── liest ── plan.json + training.db               │
  Chat, Ziele,  │   ├─ /health          ◀── liest ── health-Metriken + Load (CTL/ATL/TSB) │
  Settings)     │   ├─ /goals                                                              │
                │   ├─ /chat ──▶ claude_stream.py  (Coach-Chat, --session-id)              │
                │   ├─ /api/health/push ◀── iOS-Kurzbefehl (HRV/Schlaf/Workouts, optional) │
                │   └─ /api/strava|garmin/callback (OAuth, optional)                       │
                │                                                                          │
                │  collect.py  (täglich, früh)                                             │
                │   ├─ [Plug-in] Garmin / Strava / FIT-Import → workouts                   │
                │   ├─ [Plug-in] Apple-Health-Push           → health_metrics             │
                │   ├─ Open-Meteo  (Wetter-Forecast)                                       │
                │   └─ CalDAV/.ics (freie Slots/Termine)                                   │
                │          │                                                               │
                │  compute.py → CTL/ATL/TSB, FTP-Schätzung, Readiness-Score (grün/gelb/rot)│
                │          │                                                               │
                │  plan_agent.py → Kontext-JSON → claude --print (System: „Coach")         │
                │          ├─▶ plan.json / adjustments.json + Begründung                   │
                │          └─▶ Web Push („Heute VO2max → bei Regen auf Indoor verschoben") │
                └──────────────────────────────────────────────────────────────────────────┘
```

## 5. Datenmodell (Entwurf)

### JSON-Config (`config/`)
- **`profile.json`** — Name, Alter, Geschlecht, Größe, Gewicht, Sprache, Einheiten
- **`sports.json`** — pro aktivierter Sportart:
  - *cycling*: FTP (+ Testdatum), HFmax, Zonenmodell, Indoor/Outdoor, Powermeter ja/nein
  - *strength*: Erfahrungslevel, Hauptübungen + Arbeitsgewichte/1RM, Equipment (Studio/Homegym), bevorzugter Split
- **`goals.json`** — Liste: `{id, typ, sportart, ziel-/baseline-wert, event_datum, priorität: primary|maintenance, horizont}`
- **`settings.json`** — aktivierte Integrationen + Keys/Tokens, **Autonomie-Grad**, Zeitplan, Claude-OAuth-Token, Locale

### SQLite (`training.db`)
- **`workouts`** — `(id, datum, sportart, typ, dauer, distanz, np, if_, tss, avg_hr, quelle, raw_ref, notiz)` (`if_` statt `if`, da SQL-Keyword)
- **`strength_logs`** — `(workout_id, übung, satz, wdh, gewicht, rir)`
- **`health_metrics`** — `(datum, hrv, rhr, schlaf_h, schlaf_qualität, gewicht, vo2max, soreness, stimmung, readiness_score, quelle)`
- **`load`** — `(datum, ctl, atl, tsb)` (abgeleitet)
- **`plan_days`** — `(datum, geplantes_workout(json), status, phase, woche_typ, anpassungsgrund)`
- **`chat`** — `(id, ts, rolle, inhalt, session_id)`

### Workout-Bibliothek (`config/workout_library.json` / Code)
Parametrisierte Bausteine, z. B. *Sweet Spot* `{zone: 88-94% FTP, intervalle: 3×15min, pause: 5min, ziel_tss: …}`, *VO2max* `{106-120% FTP, 5×4min, 1:1}`, Kraft-Templates pro Split.

## 6. Das KI-„Gehirn" (Sportwissenschaft → Logik)

**Mentales Modell:** *Belastung → Erholung → Anpassung.* Drei Hebel (Frequenz, Volumen, Intensität) — nie mehrere gleichzeitig hochfahren.

### 6.0 Plan-Workflow pro Sportart: Ist-Zustand → Gespräch → konkrete Vorgaben
Der Plan entsteht **konversationell**, nicht als Blackbox:
1. **Ist-Zustand erfassen** (Onboarding, pro Sportart):
   - **Kraft:** verfügbare **Geräte**, aktuelle **Trainingsfrequenz**, aktuelle **Übungen** mit **Sätzen/Wdh./Gewicht**.
   - **Rad:** aktuelle **Wochenstunden/-km**, FTP, typische Ausfahrten, Indoor/Outdoor, Powermeter.
   - **Andere Sportarten:** analog, sportartgerecht.
2. **Mit der KI besprechen:** Der User diskutiert Ziel & Plan im Chat; die KI fragt nach, schlägt vor, justiert.
3. **Konkrete Vorgaben im Plan:** Jede Einheit ist **präskriptiv** —
   - **Kraft:** je Übung **Gewicht + Sätze × Wdh. + Ziel-RIR** (progressiv über die Wochen).
   - **Rad/Ausdauer:** Intervalle mit **Ziel-Watt/Zonen + Dauer/Pausen** (+ Route/Höhenprofil).
   Gewichte/Watt werden aus dem erfassten Ist-Zustand abgeleitet und progressiv gesteigert.

### 6.1 Plan-Generierung (wöchentlich / bei Bedarf)
1. **Normieren:** Rad über **FTP** (Zonen + TSS), Kraft über **Arbeitsgewichte/RIR**.
2. **Load steuern:** **CTL/ATL/TSB**-Modell, **Ramp Rate ≤ +3–5/Woche als harte Leitplanke**.
3. **Periodisierung:** Base → Build → Peak → Taper; Wochenrhythmus 3:1 (Belastung:Erholung); **60–90 % des Ausdauervolumens niedrigintensiv** als robuster Default.
4. **Concurrent Training (Rad+Kraft):** Beinkraft mit Abstand zu Schlüssel-Radeinheiten; „harte Tage bündeln, leichte bündeln"; nach **Primärziel** sequenzieren; Erholungstage schützen.
5. **An Realität anpassen:** freie Slots aus **Kalender**, Wetter (Indoor/Outdoor), verfügbares Equipment.

### 6.2 Tägliche Auto-Regulation (Readiness-Ampel)
Aus HRV-Trend (vs. individueller Baseline), RHR, Schlaf, subjektivem Check, Soreness:
- 🟢 **Grün:** Plan wie vorgesehen.
- 🟡 **Gelb:** harte Einheit abschwächen (z. B. VO2max → Sweet Spot, Kraft-RPE −1).
- 🔴 **Rot:** harte Einheit → Recovery/Ruhe, mit späterem Tag tauschen.
- 🤒 **Krankheit:** „Neck-Check"-Regel; pausieren + Wiedereinstiegs-Rampe.
- 📅 **Termin verschoben/Einheit verpasst:** **nicht stapeln** → Woche rebalancieren, Schlüsseleinheiten schützen.

Prinzipien: *verschieben statt streichen · im Zweifel reduzieren · Wochen-/Block-Integrität wahren · Adhärenz > theoretisches Optimum.*

### 6.3 Prompt-Architektur
System-Prompt = „erfahrener Rad+Kraft-Coach" mit den obigen Regeln + Daten-Integritätsvorgaben (keine Werte erfinden). Kontext = Profil + Sportarten + Ziele + letzte Workouts + Load + Readiness + Wetter + freie Kalender-Slots + aktueller Plan. Vollautomatisch (kein Copy-Paste), wie in Velora.

## 7. Datenquellen — alle als optionale Plug-ins (Adapter-Pattern)

Jede Integration ist ein eigenständiges Modul unter `src/data/sources/`, das ein gemeinsames Interface erfüllt und in `settings.json` aktiviert wird. So kann jeder User frei kombinieren.

| Quelle | Liefert | Kosten/Hürde | MVP? |
|---|---|---|---|
| **Datei-Import (FIT/GPX/Health-Export)** | Aktivitäten, Power, HF, GPS | gratis, kein Risiko | ✅ MVP-Fundament |
| **Open-Meteo** | Wetter (stündl. Wind/Regen/Temp) | gratis, kein Key | ✅ Phase 2 |
| **CalDAV / .ics** | Termine → freie Slots | gratis | ✅ Phase 2 |
| **Apple Health (iOS-Kurzbefehl)** | HRV, Schlaf, RHR, Workouts | gratis, pro User Setup | Phase 3 |
| **Garmin (offizielle API)** | Aktivitäten + HRV/Schlaf | OAuth, „business"-Antrag | Phase 3 |
| **Strava** | Aktivitäten, GPS, Power | ab 30.06.26 Abo + **KI-Verbot in Terms** ⚠️ | Phase 3 (User-Entscheidung) |
| **OpenRouteService** | Routen + Höhenprofile | gratis (Tageskontingent) | Phase 3 |

> ⚠️ **Strava-Hinweis im README dokumentieren:** Die Strava-Terms verbieten KI/ML-Nutzung der Daten und ab 30.06.2026 ist ein Abo nötig. Als Open-Source-Plug-in liegt die Aktivierung & rechtliche Verantwortung beim jeweiligen Self-Hoster. Empfohlener Default: **Garmin + Datei-Import**.

## 8. Features & Screens

| Screen | Inhalt | MVP |
|---|---|---|
| **Onboarding/Setup** | Sportarten, Ziele, **Ist-Zustand pro Sportart** (Kraft: Geräte + aktuelle Übungen/Gewichte; Rad: Stunden/km + FTP), Integrationen, Autonomie-Grad | ✅ |
| **Health-Overview** | Readiness-Ampel heute, HRV/RHR/Schlaf-Trends, **CTL/ATL/TSB-Chart**, Wochen-Load | ✅ |
| **Trainingsplan** | Heute + Woche, **konversationell mit der KI besprechbar**; je Einheit **präskriptiv**: **Rad → Intervalle mit Ziel-Watt/Zonen + Route & Höhenprofil**, **Kraft → Übungen mit Gewicht + Sätze × Wdh. + RIR**; Status + „Warum"-Begründung | ✅ |
| **Ziele** | Ziele definieren/tracken, Fortschritt vs. Baseline, Priorität (primary/maintenance) | ✅ |
| **Coach-Chat** | Frei mit dem Coach reden, Rückfragen, Plan-Erklärungen | Phase 2 |
| **Kalender/Periodisierung** | Phasen (Base/Build/Peak), Events, Deload-Wochen | Phase 4 |
| **Settings** | Integrationen, Sprache, Profil, FTP/Zonen, Autonomie, Zeitplan | ✅ |

## 9. Roadmap (Phasen)

- **Phase 0 — Fundament:** Repo-Setup, Velora-Kern portieren (`claude.py`, `config_loader`, FastAPI+PWA-Skelett, i18n, SQLite-Schema).
- **Phase 1 — MVP:** Onboarding + Profil/Ziele + **Datei-Import (FIT/GPX)** + **Health-Overview** + **KI-Plan-Generierung (Rad+Kraft)** + **Plan-Anzeige (Übungen/Intervalle)**. → deckt die Top-3 ab.
- **Phase 2 — Adaptiv:** Open-Meteo + CalDAV + **tägliche Readiness-Auto-Regulation** + **Coach-Chat**.
- **Phase 3 — Integrationen:** Garmin / Strava / Apple-Health-Push als Plug-ins + **Routenplanung (ORS)** mit Wetter-Logik (Gegenwind raus, Rückenwind heim).
- **Phase 4 — Open-Source-Release:** Kalender/Periodisierungs-Ansicht, `setup.py`-Wizard, Deploy-Skripte (cron/systemd), README/Docs, Lizenz, Config-Templates ohne Secrets.
- **Später:** Ernährung, weitere Sportarten, nativer Health-Connector, Anthropic-API-Provider.

## 10. Open-Source-Aspekte

- Lizenz (Vorschlag: MIT oder GPL-3.0), `README` mit Setup-Anleitung, `CONTRIBUTING`, Architektur-Doku.
- **Setup-Wizard** (`setup.py`) führt durch Sportarten/Ziele/Integrationen/Token.
- **Keine Secrets im Repo** — `settings.example.json`, Token via `claude setup-token`.
- Plug-in-Architektur dokumentiert, damit Community neue Datenquellen/Sportarten beisteuern kann.

## 11. Offene Punkte / nächste Entscheidungen

1. **Name** final wählen — Favoriten in der Sherpa/Tibet/HIF-EPAS1-Richtung: **Sirdar** ⭐, **Tenzing**, **EPAS**, **Khumbu**, **Altus**. (Domain-/Markencheck folgt nach Wahl.)
2. **Lizenz** festlegen (MIT vs. GPL).
3. **Onboarding ohne Wearable:** Default-Pfad nur mit Datei-Import + manuellem Morgen-Check sauber gestalten (nicht jeder hat HRV-Sensor).
4. Reihenfolge Phase 3: zuerst Garmin oder Routenplanung?
