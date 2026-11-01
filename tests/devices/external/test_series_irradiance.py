"""
CSV-driven irradiance and the underlying time series reader.

Reference location: Stockholm, 59.3293 N / 18.0686 E.
"""

from datetime import datetime, timedelta, timezone

import pytest

from dertwin.devices.external.clouds import SeriesCloudModel
from dertwin.devices.external.irradiance import (
    ClearSkyIrradianceModel,
    SeriesIrradianceModel,
    build_irradiance_model,
)
from dertwin.devices.external.timeseries import TimeSeries


STOCKHOLM = dict(latitude=59.3293, longitude=18.0686)

# 2025-06-21 around Stockholm's solar noon (10:49 UTC)
GHI_CSV = """timestamp,ghi_w_m2
2025-06-21T08:00:00Z,400
2025-06-21T10:00:00Z,700
2025-06-21T12:00:00Z,800
2025-06-21T14:00:00Z,500
"""

KT_CSV = """timestamp,kt
2025-06-21T08:00:00Z,1.0
2025-06-21T12:00:00Z,0.5
"""


def write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def utc(hour, minute=0):
    return datetime(2025, 6, 21, hour, minute, tzinfo=timezone.utc)


# ------------------------------------------------------------
# TimeSeries
# ------------------------------------------------------------

def test_series_reproduces_its_samples(tmp_path):
    series = TimeSeries.from_csv(write(tmp_path, "ghi.csv", GHI_CSV), "ghi_w_m2")

    assert len(series) == 4
    assert series.value_at(utc(8)) == pytest.approx(400.0)
    assert series.value_at(utc(12)) == pytest.approx(800.0)
    assert series.value_at(utc(14)) == pytest.approx(500.0)


def test_series_interpolates_linearly_between_samples(tmp_path):
    series = TimeSeries.from_csv(write(tmp_path, "ghi.csv", GHI_CSV), "ghi_w_m2")

    assert series.value_at(utc(9)) == pytest.approx(550.0)
    assert series.value_at(utc(11)) == pytest.approx(750.0)


def test_step_interpolation_holds_the_previous_sample(tmp_path):
    series = TimeSeries.from_csv(
        write(tmp_path, "ghi.csv", GHI_CSV), "ghi_w_m2", interpolate="step"
    )

    assert series.value_at(utc(9)) == pytest.approx(400.0)
    assert series.value_at(utc(11, 59)) == pytest.approx(700.0)


def test_out_of_range_returns_none_and_warns_once(tmp_path, caplog):
    import logging

    series = TimeSeries.from_csv(write(tmp_path, "ghi.csv", GHI_CSV), "ghi_w_m2")

    with caplog.at_level(logging.WARNING):
        assert series.value_at(utc(7)) is None
        assert series.value_at(utc(20)) is None

    assert caplog.text.count("outside the series range") == 1


def test_series_accepts_unsorted_rows_and_offsets(tmp_path):
    body = (
        "timestamp,ghi_w_m2\n"
        "2025-06-21T14:00:00+02:00,100\n"  # 12:00 UTC
        "2025-06-21T08:00:00Z,400\n"
    )
    series = TimeSeries.from_csv(write(tmp_path, "mixed.csv", body), "ghi_w_m2")

    assert series.value_at(utc(8)) == pytest.approx(400.0)
    assert series.value_at(utc(12)) == pytest.approx(100.0)


def test_series_skips_blank_rows(tmp_path):
    body = "timestamp,ghi_w_m2\n2025-06-21T08:00:00Z,400\n,\n2025-06-21T12:00:00Z,\n"
    series = TimeSeries.from_csv(write(tmp_path, "sparse.csv", body), "ghi_w_m2")

    assert len(series) == 1


@pytest.mark.parametrize("body,match", [
    ("timestamp,ghi_w_m2\n", "no usable rows"),
    ("when,ghi_w_m2\n2025-06-21T08:00:00Z,400\n", "missing column 'timestamp'"),
    ("timestamp,other\n2025-06-21T08:00:00Z,400\n", "missing column 'ghi_w_m2'"),
    ("timestamp,ghi_w_m2\nnot-a-date,400\n", "Invalid ISO 8601"),
])
def test_malformed_csv_raises(tmp_path, body, match):
    with pytest.raises(ValueError, match=match):
        TimeSeries.from_csv(write(tmp_path, "bad.csv", body), "ghi_w_m2")


def test_missing_csv_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        TimeSeries.from_csv(tmp_path / "nope.csv", "ghi_w_m2")


def test_unknown_interpolation_raises():
    with pytest.raises(ValueError, match="Unknown interpolation"):
        TimeSeries([(utc(8), 1.0)], interpolate="spline")


def test_empty_series_raises():
    with pytest.raises(ValueError, match="empty"):
        TimeSeries([])


# ------------------------------------------------------------
# SeriesIrradianceModel
# ------------------------------------------------------------

def build_series_model(tmp_path):
    series = TimeSeries.from_csv(write(tmp_path, "ghi.csv", GHI_CSV), "ghi_w_m2")
    return SeriesIrradianceModel(series=series, **STOCKHOLM)


def test_series_model_reports_recorded_ghi(tmp_path):
    model = build_series_model(tmp_path)

    model.advance(utc(12), 0.0, 60.0)

    assert model.get_irradiance() == pytest.approx(800.0)


def test_series_model_splits_into_components(tmp_path):
    model = build_series_model(tmp_path)
    model.advance(utc(12), 0.0, 60.0)
    components = model.get_components()

    assert components.has_solar_geometry
    assert components.position.elevation_deg > 0.0
    assert (
        components.dni * components.position.cos_zenith + components.dhi
        == pytest.approx(components.ghi, rel=1e-9)
    )


def test_series_model_is_dark_outside_the_series(tmp_path):
    model = build_series_model(tmp_path)

    model.advance(datetime(2025, 6, 22, 12, tzinfo=timezone.utc), 0.0, 60.0)

    assert model.get_irradiance() == 0.0


def test_series_model_is_dark_at_night(tmp_path):
    """Even a nonzero recorded value cannot produce power with the sun down."""
    body = "timestamp,ghi_w_m2\n2025-06-21T00:00:00Z,500\n2025-06-21T23:00:00Z,500\n"
    series = TimeSeries.from_csv(write(tmp_path, "odd.csv", body), "ghi_w_m2")
    model = SeriesIrradianceModel(series=series, latitude=-33.92, longitude=18.42)

    model.advance(datetime(2025, 6, 21, 22, tzinfo=timezone.utc), 0.0, 60.0)

    assert model.get_irradiance() == 0.0


# ------------------------------------------------------------
# Factory wiring
# ------------------------------------------------------------

def test_factory_builds_a_series_model_from_absolute_ghi(tmp_path):
    write(tmp_path, "ghi.csv", GHI_CSV)

    model = build_irradiance_model(
        dict(STOCKHOLM, series_csv="ghi.csv"),
        data_root=tmp_path,
        sim_start_utc_configured=True,
    )

    assert isinstance(model, SeriesIrradianceModel)

    model.advance(utc(12), 0.0, 60.0)
    assert model.get_irradiance() == pytest.approx(800.0)


def test_factory_uses_a_kt_series_to_modulate_clear_sky(tmp_path):
    write(tmp_path, "kt.csv", KT_CSV)

    model = build_irradiance_model(
        dict(STOCKHOLM, series_csv="kt.csv", series_column="kt"),
        data_root=tmp_path,
        sim_start_utc_configured=True,
    )

    assert isinstance(model, ClearSkyIrradianceModel)
    assert isinstance(model.cloud_model, SeriesCloudModel)

    model.advance(utc(12), 0.0, 60.0)
    components = model.get_components()

    assert components.clear_sky_index == pytest.approx(0.5)
    assert components.ghi == pytest.approx(components.clear_sky_ghi * 0.5, rel=1e-9)


def test_factory_resolves_an_absolute_csv_path(tmp_path):
    path = write(tmp_path, "ghi.csv", GHI_CSV)

    model = build_irradiance_model(
        dict(STOCKHOLM, series_csv=str(path)),
        data_root=tmp_path / "elsewhere",
        sim_start_utc_configured=True,
    )

    model.advance(utc(12), 0.0, 60.0)
    assert model.get_irradiance() == pytest.approx(800.0)


def test_factory_honours_step_interpolation(tmp_path):
    write(tmp_path, "ghi.csv", GHI_CSV)

    model = build_irradiance_model(
        dict(STOCKHOLM, series_csv="ghi.csv", series_interpolate="step"),
        data_root=tmp_path,
        sim_start_utc_configured=True,
    )

    model.advance(utc(9), 0.0, 60.0)
    assert model.get_irradiance() == pytest.approx(400.0)
