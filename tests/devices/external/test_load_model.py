"""
Site load: daily shape, weekday factor, slow drift and fast noise.

The load used to be `lambda t: base_load` — the same at 03:00 as at 18:00, and
the same on a Sunday as on a Tuesday.
"""

import statistics

import pytest

from dertwin.devices.external.load import (
    DEFAULT_BASE_KW,
    FlatProfile,
    HourlyProfile,
    ParametricProfile,
    SiteLoadModel,
    TabulatedProfile,
    build_daily_profile,
    build_load_model,
)


# A plausible commercial shape: quiet night, morning ramp, evening peak
SHAPE = [
    0.55, 0.52, 0.50, 0.50, 0.52, 0.60, 0.75, 0.95,
    1.05, 1.05, 1.00, 0.98, 0.95, 0.95, 0.98, 1.05,
    1.20, 1.35, 1.30, 1.15, 1.00, 0.85, 0.70, 0.60,
]

PROFILE_CSV = """hour,factor
0,0.6
6,0.8
12,1.0
18,1.4
"""

KW_CSV = """hour,kw
0,30
12,50
"""


def write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def run(model, hours, dt=60.0, start_h=0.0):
    """Advance across a span of simulated hours, returning (hour, kW) samples."""
    samples = []
    steps = int(hours * 3600.0 / dt)

    for step in range(steps):
        sim_time = start_h * 3600.0 + step * dt
        model.advance(sim_time, dt)
        samples.append((sim_time / 3600.0, model.get_power_kw()))

    return samples


# ------------------------------------------------------------
# Flat profile
# ------------------------------------------------------------

def test_flat_profile_is_one_everywhere():
    profile = FlatProfile()

    for hour in range(25):
        assert profile.factor_at(float(hour)) == 1.0


def test_a_bare_model_is_the_old_constant_load():
    model = SiteLoadModel(base_kw=50.0)

    for _, power in run(model, hours=48, dt=600.0):
        assert power == pytest.approx(50.0)


# ------------------------------------------------------------
# Hourly profile
# ------------------------------------------------------------

def test_hourly_factors_land_on_their_hour_centres():
    """
    Hourly figures are averages over the hour, so they belong at the centre.
    Placing them on the hour would shift the whole curve half an hour early.
    """
    profile = HourlyProfile(SHAPE, normalize=False)

    for hour, expected in enumerate(SHAPE):
        assert profile.factor_at(hour + 0.5) == pytest.approx(expected, rel=1e-12)


def test_hourly_profile_interpolates_between_centres():
    profile = HourlyProfile(SHAPE, normalize=False)

    assert profile.factor_at(8.0) == pytest.approx((SHAPE[7] + SHAPE[8]) / 2.0)
    assert profile.factor_at(17.0) == pytest.approx((SHAPE[16] + SHAPE[17]) / 2.0)


def test_hourly_profile_interpolation_wraps_across_midnight():
    profile = HourlyProfile(SHAPE, normalize=False)

    assert profile.factor_at(0.0) == pytest.approx((SHAPE[23] + SHAPE[0]) / 2.0)
    assert profile.factor_at(24.0) == pytest.approx(profile.factor_at(0.0))


def test_hourly_profile_without_interpolation_is_a_staircase():
    profile = HourlyProfile(SHAPE, interpolate=False, normalize=False)

    assert profile.factor_at(8.1) == pytest.approx(SHAPE[8])
    assert profile.factor_at(8.9) == pytest.approx(SHAPE[8])


def test_normalise_makes_the_daily_mean_exactly_one():
    profile = HourlyProfile(SHAPE, normalize=True)

    assert profile.daily_mean() == pytest.approx(1.0, abs=1e-12)


def test_normalise_preserves_the_shape():
    raw = HourlyProfile(SHAPE, normalize=False)
    normalised = HourlyProfile(SHAPE, normalize=True)

    ratio = normalised.factor_at(8.5) / raw.factor_at(8.5)

    for hour in (0.0, 3.7, 12.0, 17.5, 23.9):
        assert normalised.factor_at(hour) / raw.factor_at(hour) == pytest.approx(
            ratio
        )


def test_normalised_profile_keeps_base_kw_meaning_average_load():
    model = SiteLoadModel(base_kw=50.0, profile=HourlyProfile(SHAPE))

    powers = [power for _, power in run(model, hours=24, dt=60.0)]

    assert statistics.fmean(powers) == pytest.approx(50.0, rel=0.002)


def test_hourly_profile_needs_twenty_four_factors():
    with pytest.raises(ValueError, match="24 factors"):
        HourlyProfile([1.0, 1.0])


# ------------------------------------------------------------
# Tabulated and parametric profiles
# ------------------------------------------------------------

def test_tabulated_profile_accepts_any_resolution_and_order():
    profile = TabulatedProfile(
        [(18.0, 1.4), (0.0, 0.6), (6.0, 0.8), (12.0, 1.0)], normalize=False
    )

    assert profile.factor_at(0.0) == pytest.approx(0.6)
    assert profile.factor_at(18.0) == pytest.approx(1.4)
    assert profile.factor_at(9.0) == pytest.approx(0.9)


def test_tabulated_profile_wraps_from_the_last_point_to_the_first():
    profile = TabulatedProfile([(0.0, 0.6), (12.0, 1.2)], normalize=False)

    assert profile.factor_at(18.0) == pytest.approx(0.9)


def test_single_point_profile_is_constant():
    profile = TabulatedProfile([(7.0, 0.8)], normalize=False)

    assert profile.factor_at(3.0) == pytest.approx(0.8)
    assert profile.factor_at(20.0) == pytest.approx(0.8)


def test_parametric_profile_hits_its_configured_peaks():
    profile = ParametricProfile(
        night_factor=0.55,
        morning_peak_hour=8.0,
        morning_peak_factor=1.05,
        evening_peak_hour=18.0,
        evening_peak_factor=1.35,
        peak_width_h=3.0,
        normalize=False,
    )

    assert profile.factor_at(8.0) == pytest.approx(1.05, rel=1e-3)
    assert profile.factor_at(18.0) == pytest.approx(1.35, rel=1e-3)
    assert profile.factor_at(3.0) == pytest.approx(0.55, rel=1e-2)


def test_parametric_peak_survives_normalisation():
    profile = ParametricProfile(normalize=True)

    hours = [step / 4.0 for step in range(96)]
    peak_hour = max(hours, key=profile.factor_at)

    assert peak_hour == pytest.approx(18.0)
    assert profile.daily_mean() == pytest.approx(1.0, abs=1e-12)


def test_parametric_bumps_wrap_across_midnight():
    profile = ParametricProfile(
        night_factor=0.5,
        morning_peak_hour=0.0,
        morning_peak_factor=1.5,
        evening_peak_hour=12.0,
        evening_peak_factor=0.5,
        peak_width_h=4.0,
        normalize=False,
    )

    assert profile.factor_at(23.0) > profile.factor_at(20.0)
    assert profile.factor_at(1.0) == pytest.approx(profile.factor_at(23.0), rel=1e-9)


@pytest.mark.parametrize("kwargs,match", [
    (dict(night_factor=-0.1), "night_factor"),
    (dict(peak_width_h=0.0), "peak_width_h"),
])
def test_invalid_parametric_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        ParametricProfile(**kwargs)


# ------------------------------------------------------------
# Weekday / weekend
# ------------------------------------------------------------

def test_weekday_advances_at_each_simulated_midnight():
    model = SiteLoadModel(base_kw=10.0, start_weekday=0)  # Monday

    assert model.weekday_at(0.0) == 0
    assert model.weekday_at(23.9 * 3600.0) == 0
    assert model.weekday_at(24.0 * 3600.0) == 1
    assert model.weekday_at(5 * 86400.0) == 5
    assert model.weekday_at(7 * 86400.0) == 0


def test_weekend_is_saturday_and_sunday():
    model = SiteLoadModel(base_kw=10.0, start_weekday=0)

    weekend = [model.is_weekend(day * 86400.0) for day in range(7)]

    assert weekend == [False, False, False, False, False, True, True]


def test_weekend_factor_lowers_the_load():
    model = SiteLoadModel(
        base_kw=100.0, start_weekday=4, weekday_factor=1.0, weekend_factor=0.75
    )

    friday = run(model, hours=1, dt=3600.0, start_h=0.0)[0][1]
    saturday = run(model, hours=1, dt=3600.0, start_h=24.0)[0][1]

    assert friday == pytest.approx(100.0)
    assert saturday == pytest.approx(75.0)


def test_start_weekday_places_the_week():
    saturday_start = SiteLoadModel(base_kw=10.0, start_weekday=5)

    assert saturday_start.is_weekend(0.0)
    assert saturday_start.is_weekend(86400.0)
    assert not saturday_start.is_weekend(2 * 86400.0)


def test_an_absolute_instant_overrides_the_offset_week():
    """
    Stage 2 gives the clock a real date. When one is available the calendar
    wins over start_weekday, so the two stages compose.
    """
    from datetime import datetime, timezone

    model = SiteLoadModel(base_kw=10.0, start_weekday=0)
    saturday = datetime(2025, 11, 1, 12, tzinfo=timezone.utc)

    assert model.weekday_at(0.0) == 0
    assert model.weekday_at(0.0, utc_time=saturday) == 5
    assert model.is_weekend(0.0, utc_time=saturday)


def test_invalid_start_weekday_raises():
    with pytest.raises(ValueError, match="start_weekday"):
        SiteLoadModel(base_kw=10.0, start_weekday=7)


# ------------------------------------------------------------
# Drift
# ------------------------------------------------------------

def test_drift_is_stationary_around_the_base_load():
    model = SiteLoadModel(
        base_kw=100.0, drift_sigma=0.12, drift_tau_s=10800.0, seed=7
    )

    powers = [power for _, power in run(model, hours=2000, dt=300.0)]

    assert statistics.fmean(powers) == pytest.approx(100.0, rel=0.02)
    assert statistics.stdev(powers) == pytest.approx(12.0, rel=0.2)


def test_drift_is_bounded():
    model = SiteLoadModel(
        base_kw=100.0,
        drift_sigma=1.0,
        drift_tau_s=600.0,
        drift_min_factor=0.7,
        drift_max_factor=1.3,
        seed=7,
    )

    powers = [power for _, power in run(model, hours=500, dt=60.0)]

    assert min(powers) >= 70.0 - 1e-9
    assert max(powers) <= 130.0 + 1e-9
    assert min(powers) == pytest.approx(70.0)
    assert max(powers) == pytest.approx(130.0)


def test_drift_is_slow():
    """Neighbouring samples are strongly correlated — it is a drift, not noise."""
    model = SiteLoadModel(
        base_kw=100.0, drift_sigma=0.12, drift_tau_s=10800.0, seed=7
    )

    powers = [power for _, power in run(model, hours=200, dt=60.0)]
    mean = statistics.fmean(powers)

    numerator = sum(
        (powers[i] - mean) * (powers[i + 1] - mean) for i in range(len(powers) - 1)
    )
    denominator = sum((power - mean) ** 2 for power in powers)

    assert numerator / denominator > 0.95


def test_drift_off_by_default():
    model = SiteLoadModel(base_kw=100.0, seed=7)

    powers = [power for _, power in run(model, hours=48, dt=600.0)]

    assert min(powers) == pytest.approx(100.0)
    assert max(powers) == pytest.approx(100.0)


# ------------------------------------------------------------
# Noise
# ------------------------------------------------------------

def test_noise_is_fast_and_zero_mean():
    model = SiteLoadModel(
        base_kw=100.0, noise_sigma=0.02, noise_tau_s=30.0, seed=7
    )

    powers = [power for _, power in run(model, hours=50, dt=60.0)]

    assert statistics.fmean(powers) == pytest.approx(100.0, rel=0.01)
    assert statistics.stdev(powers) == pytest.approx(2.0, rel=0.2)


def test_drift_and_noise_are_distinguishable():
    drifting = SiteLoadModel(
        base_kw=100.0, drift_sigma=0.1, drift_tau_s=10800.0, seed=7
    )
    noisy = SiteLoadModel(
        base_kw=100.0, noise_sigma=0.1, noise_tau_s=30.0, seed=7
    )

    def lag1(model):
        powers = [power for _, power in run(model, hours=100, dt=60.0)]
        mean = statistics.fmean(powers)
        numerator = sum(
            (powers[i] - mean) * (powers[i + 1] - mean)
            for i in range(len(powers) - 1)
        )
        return numerator / sum((power - mean) ** 2 for power in powers)

    assert lag1(drifting) > 0.95
    assert lag1(noisy) < 0.3


# ------------------------------------------------------------
# Bounds and determinism
# ------------------------------------------------------------

def test_min_and_max_kw_clamp_the_result():
    model = SiteLoadModel(
        base_kw=100.0,
        profile=HourlyProfile(SHAPE),
        min_kw=80.0,
        max_kw=120.0,
        drift_sigma=0.5,
        drift_tau_s=600.0,
        drift_min_factor=0.1,
        drift_max_factor=3.0,
        seed=7,
    )

    powers = [power for _, power in run(model, hours=200, dt=60.0)]

    assert min(powers) >= 80.0
    assert max(powers) <= 120.0


def test_load_is_never_negative():
    model = SiteLoadModel(
        base_kw=1.0,
        noise_sigma=5.0,
        noise_tau_s=30.0,
        drift_sigma=5.0,
        drift_tau_s=600.0,
        drift_min_factor=0.0,
        seed=7,
    )

    for _, power in run(model, hours=200, dt=60.0):
        assert power >= 0.0


def test_same_seed_gives_the_same_load_trace():
    def trace():
        model = SiteLoadModel(
            base_kw=100.0,
            profile=HourlyProfile(SHAPE),
            drift_sigma=0.12,
            noise_sigma=0.02,
            seed=7,
        )
        return [power for _, power in run(model, hours=24, dt=60.0)]

    assert trace() == trace()


def test_different_seeds_give_different_load_traces():
    def trace(seed):
        model = SiteLoadModel(
            base_kw=100.0, drift_sigma=0.12, noise_sigma=0.02, seed=seed
        )
        return [power for _, power in run(model, hours=24, dt=60.0)]

    assert trace(7) != trace(8)


@pytest.mark.parametrize("kwargs,match", [
    (dict(base_kw=-1.0), "base_kw"),
    (dict(base_kw=1.0, weekend_factor=-0.1), "weekend_factor"),
    (dict(base_kw=1.0, drift_min_factor=1.5, drift_max_factor=1.0), "drift_min_factor"),
    (dict(base_kw=1.0, min_kw=-1.0), "min_kw"),
    (dict(base_kw=1.0, min_kw=10.0, max_kw=5.0), "max_kw"),
])
def test_invalid_model_configuration_raises(kwargs, match):
    with pytest.raises(ValueError, match=match):
        SiteLoadModel(**kwargs)


# ------------------------------------------------------------
# Factory
# ------------------------------------------------------------

def test_factory_falls_back_to_the_legacy_base_load_w():
    model = build_load_model({"power": {"base_load_w": 50000.0}})

    assert model.base_kw == pytest.approx(50.0)
    assert model.get_power_kw() == pytest.approx(50.0)


def test_factory_defaults_to_five_kilowatts():
    for config in (None, {}, {"power": {}}):
        assert build_load_model(config).base_kw == pytest.approx(DEFAULT_BASE_KW)


def test_factory_builds_the_full_model():
    model = build_load_model({
        "load": {
            "base_kw": 45.0,
            "profile": {"model": "hourly", "hourly": SHAPE},
            "weekday_factor": 1.0,
            "weekend_factor": 0.7,
            "start_weekday": "saturday",
            "drift": {"sigma": 0.1, "tau_s": 7200.0,
                      "min_factor": 0.8, "max_factor": 1.2},
            "noise": {"sigma": 0.03, "tau_s": 45.0},
            "min_kw": 5.0,
            "max_kw": 90.0,
            "seed": 59,
        }
    })

    assert model.base_kw == pytest.approx(45.0)
    assert isinstance(model.profile, HourlyProfile)
    assert model.weekend_factor == pytest.approx(0.7)
    assert model.start_weekday == 5
    assert model.is_weekend(0.0)
    assert model.min_kw == pytest.approx(5.0)
    assert model.max_kw == pytest.approx(90.0)


def test_factory_accepts_a_weekday_name_or_number():
    for value, expected in (("monday", 0), ("Sunday", 6), (3, 3)):
        model = build_load_model({"load": {"start_weekday": value}})
        assert model.start_weekday == expected


def test_factory_rejects_an_unknown_weekday():
    with pytest.raises(ValueError, match="start_weekday"):
        build_load_model({"load": {"start_weekday": "funday"}})


def test_factory_honours_disabled_drift_and_noise():
    model = build_load_model({
        "load": {
            "base_kw": 10.0,
            "drift": {"enabled": False, "sigma": 0.5},
            "noise": {"enabled": False, "sigma": 0.5},
            "seed": 7,
        }
    })

    powers = [power for _, power in run(model, hours=48, dt=600.0)]

    assert min(powers) == pytest.approx(10.0)
    assert max(powers) == pytest.approx(10.0)


def test_factory_builds_a_parametric_profile():
    model = build_load_model({
        "load": {
            "base_kw": 10.0,
            "profile": {
                "model": "parametric",
                "parametric": {"evening_peak_hour": 20.0,
                               "evening_peak_factor": 1.6},
            },
        }
    })

    assert isinstance(model.profile, ParametricProfile)
    assert model.profile.evening_peak_hour == 20.0


@pytest.mark.parametrize("profile,match", [
    ({"model": "weather"}, "Unknown load profile model"),
    ({"model": "hourly"}, "requires 'hourly'"),
    ({"model": "csv"}, "requires 'csv'"),
])
def test_factory_rejects_broken_profiles(profile, match):
    with pytest.raises(ValueError, match=match):
        build_daily_profile(profile)


# ------------------------------------------------------------
# CSV profiles
# ------------------------------------------------------------

def test_csv_profile_of_factors(tmp_path):
    write(tmp_path, "profile.csv", PROFILE_CSV)

    model = build_load_model(
        {"load": {"base_kw": 10.0,
                  "profile": {"model": "csv", "csv": "profile.csv"}}},
        data_root=tmp_path,
    )

    assert model.base_kw == pytest.approx(10.0)
    assert model.profile.daily_mean() == pytest.approx(1.0, abs=1e-12)

    # The shape is preserved: 18:00 is the peak, 00:00 the trough
    assert model.profile.factor_at(18.0) > model.profile.factor_at(12.0)
    assert model.profile.factor_at(0.0) < model.profile.factor_at(6.0)


def test_csv_profile_of_absolute_kw_sets_the_base_load(tmp_path):
    """A kW column is a shape plus a level; the level becomes base_kw."""
    write(tmp_path, "kw.csv", KW_CSV)

    model = build_load_model(
        {"load": {"profile": {"model": "csv", "csv": "kw.csv",
                              "csv_column": "kw"}}},
        data_root=tmp_path,
    )

    assert model.base_kw == pytest.approx(40.0)  # mean of 30 and 50

    model.advance(0.0, 60.0)
    assert model.get_power_kw() == pytest.approx(30.0, rel=1e-9)

    model.advance(12 * 3600.0, 60.0)
    assert model.get_power_kw() == pytest.approx(50.0, rel=1e-9)


def test_explicit_base_kw_scales_a_kw_csv(tmp_path):
    write(tmp_path, "kw.csv", KW_CSV)

    model = build_load_model(
        {"load": {"base_kw": 80.0,
                  "profile": {"model": "csv", "csv": "kw.csv",
                              "csv_column": "kw"}}},
        data_root=tmp_path,
    )

    model.advance(0.0, 60.0)
    assert model.get_power_kw() == pytest.approx(60.0, rel=1e-9)


def test_absolute_csv_path_is_used_as_given(tmp_path):
    path = write(tmp_path, "profile.csv", PROFILE_CSV)

    model = build_load_model(
        {"load": {"profile": {"model": "csv", "csv": str(path)}}},
        data_root=tmp_path / "elsewhere",
    )

    assert model.profile.daily_mean() == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("body,match", [
    ("hour,factor\n", "no usable rows"),
    ("time,factor\n0,1\n", "missing column 'hour'"),
    ("hour,other\n0,1\n", "missing column 'factor'"),
    ("hour,factor\nnoon,1\n", "could not convert"),
])
def test_malformed_csv_raises(tmp_path, body, match):
    write(tmp_path, "bad.csv", body)

    with pytest.raises(ValueError, match=match):
        build_load_model(
            {"load": {"profile": {"model": "csv", "csv": "bad.csv"}}},
            data_root=tmp_path,
        )


def test_missing_csv_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        build_load_model(
            {"load": {"profile": {"model": "csv", "csv": "nope.csv"}}},
            data_root=tmp_path,
        )


def test_csv_rows_with_blanks_are_skipped(tmp_path):
    write(tmp_path, "sparse.csv", "hour,factor\n0,0.6\n,\n12,\n18,1.4\n")

    model = build_load_model(
        {"load": {"profile": {"model": "csv", "csv": "sparse.csv"}}},
        data_root=tmp_path,
    )

    assert len(model.profile._values) == 2
