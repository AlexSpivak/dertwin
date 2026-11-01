"""
Self-contained solar geometry and clear-sky irradiance.

Standard library only — no numpy, no pandas, no pvlib. Installing pandas and
scipy to buy accuracy the cloud model then deliberately randomises would be a
poor trade; a `pvlib` backend is available as an optional extra for anyone who
wants reference-grade numbers.

Measured accuracy
-----------------
Against pvlib's SPA implementation over all of 2025 at 59.3 N, at 30-minute
resolution (see tests/devices/external/test_pvlib_agreement.py):

    solar declination    <= 0.54 deg   (identical to pvlib's own
                                        declination_spencer71 — the residual
                                        is inherent to the Spencer-71 series,
                                        a phase offset within the leap cycle,
                                        worst near the equinoxes and ~0 at the
                                        solstices)
    solar elevation      <= 0.55 deg
    solar azimuth        <= 0.44 deg
    equation of time     <= 0.02 min

Annual clear-sky GHI, integrated over the same year:

    cost of the position error alone        0.03 %
    this Ineichen-Perez vs pvlib's own      1.4 %  (airmass and pressure
                                                    handling, not geometry)

Refraction is not modelled, so elevation is geometric. It is worth up to half
a degree at the horizon and almost no energy. For a simulator whose point is
"a late-October day at 59 N looks like late October", all of this sits far
below the variability the cloud model adds on purpose.

References
----------
Solar position / equation of time:
    NOAA Solar Calculator, after Spencer (1971), "Fourier series
    representation of the position of the sun".
Clear-sky GHI:
    Haurwitz (1945), and Ineichen & Perez (2002), "A new airmass independent
    formulation for the Linke turbidity coefficient".
Airmass:
    Kasten & Young (1989).
Diffuse split:
    Erbs et al. (1982).
Transposition:
    Liu & Jordan (1963) isotropic sky.
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime

SOLAR_CONSTANT_W_M2 = 1367.7

CLEARSKY_HAURWITZ = "haurwitz"
CLEARSKY_INEICHEN = "ineichen"
CLEARSKY_PVLIB = "pvlib"
VALID_CLEARSKY_MODELS = (CLEARSKY_HAURWITZ, CLEARSKY_INEICHEN, CLEARSKY_PVLIB)


@dataclass(frozen=True)
class SolarPosition:
    """Where the sun is, in degrees. Azimuth is clockwise from true north."""
    elevation_deg: float
    azimuth_deg: float
    zenith_deg: float

    @property
    def cos_zenith(self) -> float:
        """Clamped at 0: a sun below the horizon contributes nothing."""
        return max(0.0, math.cos(math.radians(self.zenith_deg)))

    @property
    def is_up(self) -> bool:
        return self.elevation_deg > 0.0


@dataclass(frozen=True)
class IrradianceComponents:
    """
    Horizontal irradiance components in W/m², plus the sun's position.

    Per-array plane-of-array transposition needs all of these, so the shared
    site-level irradiance model publishes components rather than a single
    number and each PV array transposes to its own tilt and azimuth.
    """
    ghi: float
    dni: float
    dhi: float
    position: SolarPosition
    clear_sky_ghi: float
    clear_sky_index: float

    # False for the legacy fixed-shape model, which has no notion of where the
    # sun is. A PV array then treats `ghi` as already in-plane instead of
    # transposing a position that does not exist.
    has_solar_geometry: bool = True

    @classmethod
    def dark(cls, position: "SolarPosition | None" = None) -> "IrradianceComponents":
        return cls(
            ghi=0.0,
            dni=0.0,
            dhi=0.0,
            position=position or SolarPosition(-90.0, 0.0, 180.0),
            clear_sky_ghi=0.0,
            clear_sky_index=0.0,
        )


# ----------------------------------------------------------------------
# Solar geometry
# ----------------------------------------------------------------------

def fractional_year_rad(utc_time: datetime) -> float:
    """Spencer's day angle, including the within-day term."""
    day_of_year = utc_time.timetuple().tm_yday
    hour = utc_time.hour + utc_time.minute / 60.0 + utc_time.second / 3600.0
    return 2.0 * math.pi / 365.0 * (day_of_year - 1 + (hour - 12.0) / 24.0)


def declination_rad(utc_time: datetime) -> float:
    g = fractional_year_rad(utc_time)
    return (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.001480 * math.sin(3 * g)
    )


def equation_of_time_min(utc_time: datetime) -> float:
    """Apparent minus mean solar time, in minutes."""
    g = fractional_year_rad(utc_time)
    return 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )


def solar_position(
    utc_time: datetime, latitude_deg: float, longitude_deg: float
) -> SolarPosition:
    """
    Sun elevation and azimuth for an absolute UTC instant.

    Longitude is positive east. Internal time is UTC throughout; a config
    timezone is only ever for display.
    """

    lat = math.radians(latitude_deg)
    decl = declination_rad(utc_time)

    minutes_utc = (
        utc_time.hour * 60.0
        + utc_time.minute
        + utc_time.second / 60.0
        + utc_time.microsecond / 60_000_000.0
    )

    # True solar time, then the hour angle: negative before solar noon.
    true_solar_min = minutes_utc + equation_of_time_min(utc_time) + 4.0 * longitude_deg
    hour_angle = math.radians(true_solar_min / 4.0 - 180.0)

    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(
        decl
    ) * math.cos(hour_angle)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.acos(cos_zenith)

    # Solar unit vector in the local horizontal frame
    east = -math.cos(decl) * math.sin(hour_angle)
    north = math.cos(lat) * math.sin(decl) - math.sin(lat) * math.cos(
        decl
    ) * math.cos(hour_angle)

    azimuth = math.degrees(math.atan2(east, north)) % 360.0

    return SolarPosition(
        elevation_deg=90.0 - math.degrees(zenith),
        azimuth_deg=azimuth,
        zenith_deg=math.degrees(zenith),
    )


def daylight_hours(
    utc_date: datetime, latitude_deg: float, longitude_deg: float = 0.0
) -> float:
    """
    Length of the geometric day, ignoring refraction.

    Degenerate at the poles: returns 0.0 for polar night and 24.0 for
    midnight sun.
    """

    lat = math.radians(latitude_deg)
    decl = declination_rad(utc_date)

    x = -math.tan(lat) * math.tan(decl)

    if x <= -1.0:
        return 24.0
    if x >= 1.0:
        return 0.0

    return 2.0 * math.degrees(math.acos(x)) / 15.0


# ----------------------------------------------------------------------
# Airmass and extraterrestrial irradiance
# ----------------------------------------------------------------------

def extraterrestrial_irradiance(utc_time: datetime) -> float:
    """Normal-incidence irradiance at the top of the atmosphere, W/m²."""
    day_of_year = utc_time.timetuple().tm_yday
    return SOLAR_CONSTANT_W_M2 * (
        1.0 + 0.033 * math.cos(2.0 * math.pi * day_of_year / 365.0)
    )


def relative_airmass(zenith_deg: float) -> float:
    """Kasten & Young (1989). Returns inf when the sun is below the horizon."""
    if zenith_deg >= 90.0:
        return math.inf

    cos_z = math.cos(math.radians(zenith_deg))

    return 1.0 / (
        cos_z + 0.50572 * (96.07995 - zenith_deg) ** -1.6364
    )


def pressure_at_altitude(altitude_m: float) -> float:
    """Standard-atmosphere pressure in Pa."""
    return 100.0 * ((44331.514 - altitude_m) / 11880.516) ** (1.0 / 0.1902632)


def absolute_airmass(zenith_deg: float, altitude_m: float = 0.0) -> float:
    """
    Pressure-corrected airmass.

    Relative airmass counts atmospheres at sea level; at altitude there is less
    of one to cross, which is the main way site elevation reaches the clear-sky
    model.
    """
    relative = relative_airmass(zenith_deg)

    if not math.isfinite(relative):
        return relative

    return relative * pressure_at_altitude(altitude_m) / 101325.0


# ----------------------------------------------------------------------
# Clear-sky GHI
# ----------------------------------------------------------------------

def clamp_to_extraterrestrial(
    ghi: float, position: SolarPosition, utc_time: datetime
) -> float:
    """
    Cap GHI at the extraterrestrial horizontal irradiance.

    A hard physical ceiling, and a necessary one: Ineichen-Perez carries an
    `exp(0.01·AM^1.8)` term that blows up at extreme airmass, so within a
    degree of the horizon it would otherwise report tens of W/m² where the top
    of the atmosphere only delivers single digits.
    """

    ceiling = extraterrestrial_irradiance(utc_time) * position.cos_zenith

    return max(0.0, min(ghi, ceiling))


def haurwitz_ghi(position: SolarPosition) -> float:
    """
    Haurwitz (1945): clear-sky GHI from the sun's elevation alone.

    One expression, no turbidity, no altitude. Good to roughly 10% at
    mid-latitudes and a useful sanity check against the richer model.
    """

    cos_z = position.cos_zenith
    if cos_z <= 0.0:
        return 0.0

    return 1098.0 * cos_z * math.exp(-0.059 / cos_z)


def ineichen_ghi(
    position: SolarPosition,
    utc_time: datetime,
    altitude_m: float = 0.0,
    linke_turbidity: float = 3.0,
    perez_enhancement: bool = True,
) -> float:
    """
    Ineichen & Perez (2002) clear-sky GHI, W/m².

    Adds site altitude and atmospheric turbidity. linke_turbidity ~2 is a very
    clear maritime or winter atmosphere, ~3 a clear continental one, ~5–6 a
    hazy summer industrial one.

    `perez_enhancement` is the `exp(0.01·AM^1.8)` term from the original
    paper. pvlib disables it by default because it misbehaves at extreme
    airmass; we keep it on, because without it the model reads ~30% low at the
    low winter sun of a high-latitude site (62 vs 87 W/m² at Stockholm in
    December, against Haurwitz) — and `clamp_to_extraterrestrial` already
    handles the misbehaviour near the horizon.
    """

    cos_z = position.cos_zenith
    if cos_z <= 0.0:
        return 0.0

    airmass = absolute_airmass(position.zenith_deg, altitude_m)
    if not math.isfinite(airmass):
        return 0.0

    fh1 = math.exp(-altitude_m / 8000.0)
    fh2 = math.exp(-altitude_m / 1250.0)
    cg1 = 5.09e-5 * altitude_m + 0.868
    cg2 = 3.92e-5 * altitude_m + 0.0387

    ghi = (
        cg1
        * extraterrestrial_irradiance(utc_time)
        * cos_z
        * math.exp(-cg2 * airmass * (fh1 + fh2 * (linke_turbidity - 1.0)))
    )

    if perez_enhancement:
        ghi *= math.exp(0.01 * airmass ** 1.8)

    return clamp_to_extraterrestrial(ghi, position, utc_time)


def ineichen_dni(
    position: SolarPosition,
    utc_time: datetime,
    altitude_m: float = 0.0,
    linke_turbidity: float = 3.0,
) -> float:
    """
    Ineichen & Perez (2002) clear-sky direct normal irradiance, W/m².

    The companion of `ineichen_ghi`, and the physical ceiling on any beam
    component: it is what the atmosphere can actually transmit along the
    path to the sun.

    This matters because the Erbs correlation infers DNI by dividing the
    diffuse-corrected GHI by cos(zenith), which blows up as the sun
    approaches the horizon. Two degrees above the horizon in a Nordic
    December, Erbs reports a beam of ~1180 W/m² — 84% of the extraterrestrial
    value, through some twenty-five airmasses. This equation gives ~40.
    """

    cos_z = position.cos_zenith
    if cos_z <= 0.0:
        return 0.0

    airmass = absolute_airmass(position.zenith_deg, altitude_m)
    if not math.isfinite(airmass):
        return 0.0

    fh1 = math.exp(-altitude_m / 8000.0)
    b = 0.664 + 0.163 / fh1

    dni = (
        b
        * extraterrestrial_irradiance(utc_time)
        * math.exp(-0.09 * airmass * (linke_turbidity - 1.0))
    )

    return max(0.0, dni)


def limit_beam_to_clear_sky(
    components: "IrradianceComponents",
    utc_time: datetime,
    altitude_m: float = 0.0,
    linke_turbidity: float = 3.0,
) -> "IrradianceComponents":
    """
    Cap DNI at what the atmosphere can transmit, moving the excess to DHI.

    GHI is preserved exactly: energy the beam cannot carry arrives as diffuse,
    which is what actually happens.
    """

    position = components.position
    cos_z = position.cos_zenith

    if components.dni <= 0.0 or cos_z <= 0.0:
        return components

    ceiling = ineichen_dni(
        position, utc_time,
        altitude_m=altitude_m, linke_turbidity=linke_turbidity,
    )

    if components.dni <= ceiling:
        return components

    return replace(
        components,
        dni=ceiling,
        dhi=max(0.0, components.ghi - ceiling * cos_z),
    )


def clear_sky_ghi(
    position: SolarPosition,
    utc_time: datetime,
    model: str = CLEARSKY_INEICHEN,
    altitude_m: float = 0.0,
    linke_turbidity: float = 3.0,
) -> float:
    if model == CLEARSKY_HAURWITZ:
        return haurwitz_ghi(position)

    if model == CLEARSKY_INEICHEN:
        return ineichen_ghi(
            position, utc_time, altitude_m=altitude_m,
            linke_turbidity=linke_turbidity,
        )

    if model == CLEARSKY_PVLIB:
        from dertwin.devices.external.pvlib_backend import pvlib_clear_sky_ghi

        return clamp_to_extraterrestrial(
            pvlib_clear_sky_ghi(
                position, utc_time, altitude_m=altitude_m,
                linke_turbidity=linke_turbidity,
            ),
            position,
            utc_time,
        )

    raise ValueError(
        f"Unknown clear-sky model '{model}'. "
        f"Must be one of {VALID_CLEARSKY_MODELS}."
    )


# ----------------------------------------------------------------------
# Diffuse split
# ----------------------------------------------------------------------

def erbs_split(
    ghi: float, position: SolarPosition, utc_time: datetime
) -> tuple[float, float]:
    """
    Erbs et al. (1982): split GHI into (DNI, DHI).

    Driven by the clearness index, so cloud cover raises the diffuse fraction
    on its own — a thick overcast comes out almost entirely diffuse without
    any extra bookkeeping.
    """

    cos_z = position.cos_zenith
    if ghi <= 0.0 or cos_z <= 0.0:
        return 0.0, 0.0

    extraterrestrial_horizontal = extraterrestrial_irradiance(utc_time) * cos_z
    if extraterrestrial_horizontal <= 0.0:
        return 0.0, ghi

    kt = min(1.0, ghi / extraterrestrial_horizontal)

    if kt <= 0.22:
        diffuse_fraction = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        diffuse_fraction = (
            0.9511
            - 0.1604 * kt
            + 4.388 * kt ** 2
            - 16.638 * kt ** 3
            + 12.336 * kt ** 4
        )
    else:
        diffuse_fraction = 0.165

    diffuse_fraction = max(0.0, min(1.0, diffuse_fraction))

    dhi = ghi * diffuse_fraction
    dni = (ghi - dhi) / cos_z

    return dni, dhi


# ----------------------------------------------------------------------
# Plane-of-array transposition
# ----------------------------------------------------------------------

def cos_angle_of_incidence(
    position: SolarPosition, tilt_deg: float, azimuth_deg: float
) -> float:
    """Cosine of the angle between the sun and a tilted plane's normal."""

    zenith = math.radians(position.zenith_deg)
    tilt = math.radians(tilt_deg)
    delta_azimuth = math.radians(position.azimuth_deg - azimuth_deg)

    return (
        math.cos(zenith) * math.cos(tilt)
        + math.sin(zenith) * math.sin(tilt) * math.cos(delta_azimuth)
    )


def plane_of_array_irradiance(
    components: IrradianceComponents,
    tilt_deg: float,
    azimuth_deg: float,
    albedo: float = 0.2,
) -> float:
    """
    Isotropic-sky (Liu & Jordan) transposition onto a tilted plane, W/m².

    POA = beam + isotropic sky diffuse + ground reflected.
    """

    if not components.position.is_up:
        return 0.0

    tilt = math.radians(tilt_deg)

    cos_aoi = cos_angle_of_incidence(components.position, tilt_deg, azimuth_deg)
    beam = components.dni * max(0.0, cos_aoi)

    sky_diffuse = components.dhi * (1.0 + math.cos(tilt)) / 2.0
    ground = components.ghi * albedo * (1.0 - math.cos(tilt)) / 2.0

    return max(0.0, beam + sky_diffuse + ground)


def default_tilt_deg(latitude_deg: float) -> float:
    """Latitude tilt, capped — the usual fixed-mount rule of thumb."""
    return min(60.0, abs(latitude_deg))


def default_azimuth_deg(latitude_deg: float) -> float:
    """Equator-facing: due south in the north, due north in the south."""
    return 180.0 if latitude_deg >= 0.0 else 0.0
