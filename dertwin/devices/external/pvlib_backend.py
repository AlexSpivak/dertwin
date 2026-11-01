"""
Optional pvlib backend for clear-sky irradiance.

The core clear-sky model in solar.py is standard library only, on purpose:
pvlib pulls in numpy, pandas and scipy, which would triple the install and the
Docker image of a package whose only runtime dependencies are pymodbus and
PyYAML — to buy accuracy the cloud model then deliberately randomises.

This adapter exists for two reasons: anyone who wants reference-grade numbers
can opt in with `irradiance.clearsky_model: "pvlib"`, and the test suite can
check the hand-rolled model against it.

    pip install "dertwin[pvlib]"
"""

from datetime import datetime

from dertwin.devices.external.solar import SolarPosition

_MISSING = (
    "clearsky_model 'pvlib' needs the optional pvlib dependency. "
    'Install it with: pip install "dertwin[pvlib]"'
)


def pvlib_clear_sky_ghi(
    position: SolarPosition,
    utc_time: datetime,
    altitude_m: float = 0.0,
    linke_turbidity: float = 3.0,
) -> float:
    """Ineichen-Perez GHI from pvlib, in W/m²."""

    try:
        from pvlib.clearsky import ineichen
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise ImportError(_MISSING) from e

    if not position.is_up:
        return 0.0

    result = ineichen(
        apparent_zenith=position.zenith_deg,
        airmass_absolute=_absolute_airmass(position.zenith_deg, altitude_m),
        linke_turbidity=linke_turbidity,
        altitude=altitude_m,
        # Match the built-in model: the original paper's enhancement term,
        # which pvlib leaves off by default.
        perez_enhancement=True,
    )

    return max(0.0, float(result["ghi"]))


def _absolute_airmass(zenith_deg: float, altitude_m: float) -> float:
    from pvlib.atmosphere import get_absolute_airmass, get_relative_airmass, alt2pres

    relative = get_relative_airmass(zenith_deg)

    return float(get_absolute_airmass(relative, pressure=alt2pres(altitude_m)))
