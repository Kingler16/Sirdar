"""Trainings-Kennzahlen für Sirdar: Normalized Power (NP), Intensity Factor (IF), TSS.

Die Formeln sind die De-facto-Standards aus dem Power-Training (Coggan/TrainingPeaks):

Normalized Power (NP)
    1. 30-Sekunden gleitender Mittelwert der Leistung (rolling average).
    2. Jeden dieser Werte hoch 4.
    3. Mittelwert dieser vierten Potenzen.
    4. Vierte Wurzel daraus.
    Quelle (verifiziert 2026-06): trainingpeaks.com (Normalized Power Help Center),
    trainerroad.com/blog/normalized-power-what-it-is-and-how-to-use-it.

Intensity Factor (IF)
    IF = NP / FTP
    (relative Intensität gegenüber der Functional Threshold Power.)

Training Stress Score (TSS)
    TSS = (Dauer_sek × NP × IF) / (FTP × 3600) × 100
    Eine Stunde exakt an der FTP (NP=FTP, IF=1.0) ergibt definitionsgemäß TSS 100.
    Quelle (verifiziert 2026-06): trainingpeaks.com/learn/articles/estimating-training-stress-score-tss,
    trainerroad.com/blog/tss-...

Designentscheidung: Alle Funktionen geben ``None`` zurück, wenn die Eingaben
keine sinnvolle Berechnung erlauben (leere Liste, FTP fehlt/≤0). So kann der
Aufrufer Workouts ohne Powermeter sauber als "keine Power-Kennzahlen" behandeln,
statt Werte zu erfinden (Daten-Integrität, KONZEPT §6.3).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Fenstergröße für den gleitenden Mittelwert der NP-Berechnung (Sekunden).
NP_WINDOW_S = 30


def normalized_power(
    power_samples: list[float] | None,
    sample_rate_s: int = 1,
) -> float | None:
    """Berechnet Normalized Power aus einer Leistungs-Zeitreihe.

    Args:
        power_samples: Leistungswerte in Watt, ein Wert pro ``sample_rate_s``
            Sekunden (FIT ``record.power``). ``None``-Werte (Lücken) zählen als 0 W.
        sample_rate_s: Abtastrate in Sekunden (1 Hz = 1, üblicher Garmin-Default).

    Returns:
        NP in Watt (float), oder ``None``, wenn die Reihe zu kurz für ein
        einziges 30s-Fenster ist bzw. leer.

    Verfahren:
        30s rolling average → ^4 → Mittelwert → 4. Wurzel.
    """
    if not power_samples or sample_rate_s <= 0:
        return None

    # Lücken (None) als 0 W behandeln — Pausen sind reale Nicht-Leistung.
    samples = [float(p) if p is not None else 0.0 for p in power_samples]

    # Anzahl Samples pro 30s-Fenster (mind. 1).
    window = max(1, round(NP_WINDOW_S / sample_rate_s))

    # Reicht die Reihe nicht für ein volles Fenster, ist NP nicht definiert.
    if len(samples) < window:
        return None

    # Gleitender 30s-Mittelwert über ein effizientes Schiebefenster.
    rolling: list[float] = []
    window_sum = sum(samples[:window])
    rolling.append(window_sum / window)
    for i in range(window, len(samples)):
        window_sum += samples[i] - samples[i - window]
        rolling.append(window_sum / window)

    # Jeden gleitenden Mittelwert hoch 4, davon der Mittelwert, dann 4. Wurzel.
    fourth_power_mean = sum(v**4 for v in rolling) / len(rolling)
    np_value = fourth_power_mean**0.25
    return round(np_value, 1)


def intensity_factor(np: float | None, ftp: float | int | None) -> float | None:
    """IF = NP / FTP. ``None``, wenn NP oder FTP fehlt bzw. FTP ≤ 0."""
    if np is None or not ftp or ftp <= 0:
        return None
    return round(np / ftp, 3)


def tss(
    duration_s: float | int | None,
    np: float | None,
    if_: float | None,
    ftp: float | int | None,
) -> float | None:
    """Training Stress Score.

    TSS = (Dauer_sek × NP × IF) / (FTP × 3600) × 100

    Returns ``None``, wenn ein benötigter Wert fehlt oder FTP/Dauer ≤ 0.
    """
    if (
        duration_s is None
        or duration_s <= 0
        or np is None
        or if_ is None
        or not ftp
        or ftp <= 0
    ):
        return None
    return round((duration_s * np * if_) / (ftp * 3600) * 100, 1)


def power_metrics(
    power_samples: list[float] | None,
    duration_s: float | int | None,
    ftp: float | int | None,
    sample_rate_s: int = 1,
) -> dict[str, float | None]:
    """Bequemer Wrapper: berechnet NP, IF und TSS in einem Rutsch.

    Gibt immer ein Dict mit den Schlüsseln ``np``, ``if_``, ``tss`` zurück;
    einzelne Werte sind ``None``, wenn nicht berechenbar (keine Power / kein FTP).
    """
    np_value = normalized_power(power_samples, sample_rate_s=sample_rate_s)
    if_value = intensity_factor(np_value, ftp)
    tss_value = tss(duration_s, np_value, if_value, ftp)
    return {"np": np_value, "if_": if_value, "tss": tss_value}
