"""Phase-1B Tests: Trainings-Kennzahlen (NP / IF / TSS) aus src/core/metrics.

Bekannte Referenzwerte:
  Konstante 200 W über 3600 s, FTP 250
    → NP = 200 W (konstante Leistung → 4. Wurzel des Mittels der 4. Potenzen = 200)
    → IF = 200/250 = 0.8
    → TSS = (3600 × 200 × 0.8) / (250 × 3600) × 100 = 64
  Eine Stunde exakt an der FTP (NP=FTP, IF=1.0) → TSS = 100 (Definition).
"""

import pytest

from src.core.metrics import (
    intensity_factor,
    normalized_power,
    power_metrics,
    tss,
)


# ─── Normalized Power ────────────────────────────────────────

def test_np_constant_power():
    """Konstante 200 W → NP = 200 W."""
    np = normalized_power([200.0] * 3600)
    assert np == pytest.approx(200.0, abs=0.1)


def test_np_zero_power():
    """Konstante 0 W → NP = 0."""
    assert normalized_power([0.0] * 60) == pytest.approx(0.0, abs=0.1)


def test_np_variable_power_above_average():
    """NP einer schwankenden Reihe liegt über dem arithmetischen Mittel.

    Die 4.-Potenz-Gewichtung straft Spitzen — NP > Durchschnitt bei Variabilität.
    """
    # 30 min @ 100 W, dann 30 min @ 300 W → arithm. Mittel = 200 W.
    samples = [100.0] * 1800 + [300.0] * 1800
    np = normalized_power(samples)
    arithmetic_mean = sum(samples) / len(samples)
    assert np > arithmetic_mean


def test_np_empty_returns_none():
    assert normalized_power([]) is None
    assert normalized_power(None) is None


def test_np_shorter_than_window_returns_none():
    """Weniger Samples als ein 30s-Fenster → NP nicht definiert."""
    assert normalized_power([200.0] * 10) is None


def test_np_handles_none_gaps_as_zero():
    """None-Lücken in der Reihe werden als 0 W behandelt (Pausen)."""
    np = normalized_power([200.0] * 30 + [None] * 30)
    assert np is not None and np > 0


def test_np_respects_sample_rate():
    """Bei sample_rate_s=5 reichen 6 Samples für ein 30s-Fenster."""
    assert normalized_power([200.0] * 6, sample_rate_s=5) == pytest.approx(200.0, abs=0.1)
    assert normalized_power([200.0] * 5, sample_rate_s=5) is None


# ─── Intensity Factor ────────────────────────────────────────

def test_if_basic():
    assert intensity_factor(200.0, 250) == pytest.approx(0.8, abs=0.001)


def test_if_at_threshold():
    assert intensity_factor(250.0, 250) == pytest.approx(1.0, abs=0.001)


def test_if_none_inputs():
    assert intensity_factor(None, 250) is None
    assert intensity_factor(200.0, None) is None
    assert intensity_factor(200.0, 0) is None


# ─── TSS ─────────────────────────────────────────────────────

def test_tss_known_example():
    """200 W, FTP 250, 3600 s → TSS = 64."""
    np = 200.0
    if_ = intensity_factor(np, 250)
    assert tss(3600, np, if_, 250) == pytest.approx(64.0, abs=0.1)


def test_tss_one_hour_at_ftp_is_100():
    """Eine Stunde exakt an der FTP → TSS = 100 (Definition)."""
    np = 250.0
    if_ = intensity_factor(np, 250)
    assert tss(3600, np, if_, 250) == pytest.approx(100.0, abs=0.1)


def test_tss_none_inputs():
    assert tss(None, 200.0, 0.8, 250) is None
    assert tss(3600, None, 0.8, 250) is None
    assert tss(3600, 200.0, None, 250) is None
    assert tss(3600, 200.0, 0.8, None) is None
    assert tss(0, 200.0, 0.8, 250) is None


# ─── Wrapper ─────────────────────────────────────────────────

def test_power_metrics_full():
    m = power_metrics([200.0] * 3600, 3600, 250)
    assert m["np"] == pytest.approx(200.0, abs=0.1)
    assert m["if_"] == pytest.approx(0.8, abs=0.001)
    assert m["tss"] == pytest.approx(64.0, abs=0.1)


def test_power_metrics_no_power():
    """Ohne Power-Samples bleiben alle Power-Kennzahlen None."""
    m = power_metrics([], 3600, 250)
    assert m == {"np": None, "if_": None, "tss": None}


def test_power_metrics_no_ftp():
    """Mit Power aber ohne FTP: NP berechenbar, IF/TSS None."""
    m = power_metrics([200.0] * 3600, 3600, None)
    assert m["np"] == pytest.approx(200.0, abs=0.1)
    assert m["if_"] is None
    assert m["tss"] is None
