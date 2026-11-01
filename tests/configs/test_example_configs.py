"""
The Stockholm and Kyiv example sites.

Both are config-only: the point of these tests is that two very different
places come out of the same code, and that nothing location-specific leaked
into `dertwin/` to make them work.
"""

import json
import re
import tokenize
from pathlib import Path

import pytest

from dertwin.controllers.site_controller import SiteController
from dertwin.main import load_config


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"
PACKAGE_DIR = REPO_ROOT / "dertwin"

STOCKHOLM = CONFIG_DIR / "stockholm_site_config.json"
KYIV = CONFIG_DIR / "kyiv_site_config.json"

STEP_S = 60.0
TICKS_PER_DAY = int(24 * 3600 / STEP_S)


def build(config_path, tmp_path, date=None, clear_sky=True):
    config = load_config(config_path)

    config["real_time"] = False
    config["step"] = STEP_S
    config["state_root"] = str(tmp_path)

    if date is not None:
        config["sim_start_utc"] = f"{date}T00:00:00Z"

    if clear_sky:
        config["external_models"]["irradiance"]["cloud"] = {"enabled": False}

    for asset in config["assets"]:
        asset.pop("protocols", None)

    site = SiteController(config)
    site.build()
    return site


async def run_day(site, ticks=TICKS_PER_DAY):
    irradiance = site.external_models.irradiance_model
    load_model = site.external_models.load_model
    pv = site._devices_by_type["inverter"][0]

    rows = []
    for _ in range(ticks):
        await site.engine.step_once()
        components = irradiance.get_components()
        rows.append({
            "elevation": components.position.elevation_deg,
            "ghi": components.ghi,
            "poa": pv.panel.irradiance_w_per_m2,
            "pv_kw": pv.get_telemetry().total_active_power,
            "load_kw": load_model.get_power_kw(),
            "pv_kwh": pv.get_telemetry().today_output_energy,
        })

    return rows


def daylight_hours(rows):
    return sum(1 for row in rows if row["elevation"] > 0.0) * STEP_S / 3600.0


# ------------------------------------------------------------
# They exist and they run
# ------------------------------------------------------------

@pytest.mark.parametrize("config_path", [STOCKHOLM, KYIV], ids=lambda p: p.stem)
def test_config_is_valid_json_with_a_site_name(config_path):
    config = json.loads(config_path.read_text())

    assert config["site_name"]
    assert config["sim_start_utc"]
    assert config["external_models"]["irradiance"]["latitude"]
    assert config["external_models"]["load"]
    assert {asset["type"] for asset in config["assets"]} == {
        "inverter", "bess", "energy_meter"
    }


@pytest.mark.parametrize("config_path", [STOCKHOLM, KYIV], ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_config_runs_a_full_day(config_path, tmp_path):
    rows = await run_day(build(config_path, tmp_path))

    assert len(rows) == TICKS_PER_DAY
    assert max(row["pv_kw"] for row in rows) > 0.0
    assert min(row["load_kw"] for row in rows) > 0.0
    assert rows[-1]["pv_kwh"] > 0.0


@pytest.mark.parametrize("config_path", [STOCKHOLM, KYIV], ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_config_is_deterministic(config_path, tmp_path):
    """Both configs seed every stochastic model, so two runs must agree."""
    first = await run_day(build(config_path, tmp_path, clear_sky=False), 600)
    second = await run_day(build(config_path, tmp_path, clear_sky=False), 600)

    assert first == second


@pytest.mark.parametrize("config_path", [STOCKHOLM, KYIV], ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_nothing_is_produced_at_night(config_path, tmp_path):
    rows = await run_day(build(config_path, tmp_path))

    dark = [row for row in rows if row["elevation"] <= 0.0]
    assert dark

    for row in dark:
        assert row["ghi"] == 0.0
        assert row["poa"] == 0.0


# ------------------------------------------------------------
# The geography actually shows up
# ------------------------------------------------------------

# Clear-sky reference values for the two configs, verified against the solar
# model. Yield is deliberately not compared: the two arrays are sized and
# tilted differently, so that would measure the plant, not the place.
EXPECTED = {
    "2025-10-31": {"stockholm": (8.72, 274.0), "kyiv": (9.68, 411.0)},
    "2025-06-21": {"stockholm": (18.28, 839.0), "kyiv": (16.23, 905.0)},
    "2025-12-21": {"stockholm": (5.73, 108.0), "kyiv": (7.78, 239.0)},
}


@pytest.mark.parametrize("date", sorted(EXPECTED))
@pytest.mark.parametrize("site", ["stockholm", "kyiv"])
@pytest.mark.asyncio
async def test_clear_sky_resource_matches_the_documented_values(
    site, date, tmp_path
):
    config_path = STOCKHOLM if site == "stockholm" else KYIV
    expected_hours, expected_peak = EXPECTED[date][site]

    rows = await run_day(build(config_path, tmp_path, date=date))

    assert daylight_hours(rows) == pytest.approx(expected_hours, abs=0.1)
    assert max(row["ghi"] for row in rows) == pytest.approx(
        expected_peak, rel=0.03
    )


@pytest.mark.asyncio
async def test_kyiv_gets_more_sun_than_stockholm_in_late_october(tmp_path):
    stockholm = await run_day(build(STOCKHOLM, tmp_path, date="2025-10-31"))
    kyiv = await run_day(build(KYIV, tmp_path, date="2025-10-31"))

    assert daylight_hours(kyiv) > daylight_hours(stockholm)
    assert max(row["ghi"] for row in kyiv) > max(
        row["ghi"] for row in stockholm
    ) * 1.3


@pytest.mark.asyncio
async def test_stockholm_gets_the_longer_midsummer_day(tmp_path):
    stockholm = await run_day(build(STOCKHOLM, tmp_path, date="2025-06-21"))
    kyiv = await run_day(build(KYIV, tmp_path, date="2025-06-21"))

    assert daylight_hours(stockholm) > daylight_hours(kyiv) + 1.5


@pytest.mark.asyncio
async def test_winter_is_much_thinner_than_summer_at_both_sites(tmp_path):
    for config_path in (STOCKHOLM, KYIV):
        summer = await run_day(build(config_path, tmp_path, date="2025-06-21"))
        winter = await run_day(build(config_path, tmp_path, date="2025-12-21"))

        assert max(row["ghi"] for row in summer) > max(
            row["ghi"] for row in winter
        ) * 3.0
        assert summer[-1]["pv_kwh"] > winter[-1]["pv_kwh"] * 3.0


@pytest.mark.asyncio
async def test_a_steep_tilt_beats_the_horizontal_resource_in_autumn(tmp_path):
    """Both arrays are tilted, so in-plane irradiance exceeds GHI by a lot."""
    for config_path in (STOCKHOLM, KYIV):
        rows = await run_day(build(config_path, tmp_path, date="2025-10-31"))

        assert max(row["poa"] for row in rows) > max(
            row["ghi"] for row in rows
        ) * 1.5


# ------------------------------------------------------------
# Config-only: nothing about these places is in the code
# ------------------------------------------------------------

def package_code_lines():
    """
    Every line of executable code in the package, with comments and strings
    stripped out.

    Prose is not behaviour: a comment citing a measured value at a named
    latitude is good documentation, and a guard that forbade it would only
    make the code worse. These guards are about what the package *does*.
    """
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        lines = {}

        with open(path, "rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type in (
                    tokenize.COMMENT, tokenize.STRING, tokenize.NL,
                    tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                ):
                    continue
                lines.setdefault(token.start[0], []).append(token.string)

        for line_no, parts in sorted(lines.items()):
            yield path, line_no, " ".join(parts)


def test_no_site_coordinates_are_hard_coded_in_the_package():
    """
    The whole premise. If a latitude ever shows up in dertwin/, the examples
    have stopped being examples.
    """
    coordinates = re.compile(r"\b(59\.3\d|50\.4[45]|18\.06\d|30\.52\d)")

    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line_no}: {code}"
        for path, line_no, code in package_code_lines()
        if coordinates.search(code)
    ]

    assert not offenders, "site coordinates leaked into the package:\n" + "\n".join(
        offenders
    )


def test_no_place_names_are_hard_coded_in_the_package():
    names = re.compile(r"stockholm|kyiv|kiev", re.IGNORECASE)

    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line_no}: {code}"
        for path, line_no, code in package_code_lines()
        if names.search(code)
    ]

    assert not offenders, "place names leaked into the package:\n" + "\n".join(
        offenders
    )


def test_the_guards_would_actually_catch_a_leak():
    """A guard nobody has seen fail is not a guard."""
    coordinates = re.compile(r"\b(59\.3\d|50\.4[45]|18\.06\d|30\.52\d)")
    names = re.compile(r"stockholm|kyiv|kiev", re.IGNORECASE)

    assert coordinates.search("latitude = 59.3293")
    assert names.search('if site == "stockholm":')

    # ...and the tokeniser really did yield the package's code
    assert len(list(package_code_lines())) > 500


@pytest.mark.asyncio
async def test_the_two_sites_differ_only_by_configuration(tmp_path):
    """Same classes, same code path; only the numbers differ."""
    stockholm = build(STOCKHOLM, tmp_path)
    kyiv = build(KYIV, tmp_path)

    for attribute in ("irradiance_model", "load_model", "power_model"):
        assert type(getattr(stockholm.external_models, attribute)) is type(
            getattr(kyiv.external_models, attribute)
        )

    assert [type(c.device) for c in stockholm.controllers] == [
        type(c.device) for c in kyiv.controllers
    ]

    assert (
        stockholm.external_models.irradiance_model.latitude
        != kyiv.external_models.irradiance_model.latitude
    )


# ------------------------------------------------------------
# Persistence wiring
# ------------------------------------------------------------

@pytest.mark.parametrize("config_path", [STOCKHOLM, KYIV], ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_soc_is_persisted_under_state_root(config_path, tmp_path):
    site = build(config_path, tmp_path)
    await run_day(site, ticks=120)

    site.save_device_state()

    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1

    state = json.loads(written[0].read_text())
    assert state["schema"] == 1
    assert 0.0 <= state["soc_percent"] <= 100.0


def test_state_root_points_outside_the_configs_directory():
    """Both configs write to the repo's state/ directory, not configs/."""
    for config_path in (STOCKHOLM, KYIV):
        raw = json.loads(config_path.read_text())
        assert raw["state_root"] == "../state"

        resolved = Path(load_config(config_path)["state_root"])
        assert resolved == (REPO_ROOT / "state").resolve()
