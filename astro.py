# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Éphémérides calculées localement, sans service extérieur ni bibliothèque.

Formules de basse précision de l'Astronomical Almanac : position du soleil
à environ 0,01° près, de la lune à environ 0,3° près. C'est largement
suffisant pour situer un passage par rapport au coucher du soleil (à la
minute près) et savoir si la lune éclairait la nuit.

Toutes les heures sont des instants Unix (secondes, UTC).
"""
import math

SUN_HORIZON = -0.833        # lever/coucher : bord supérieur + réfraction
CIVIL = -6.0
NAUTICAL = -12.0
J2000 = 946728000.0         # 1er janvier 2000 à 12:00 UTC

_rad, _deg = math.radians, math.degrees


def _days(ts: float) -> float:
    return (ts - J2000) / 86400.0


def _sun_ecliptic(d: float):
    """Longitude écliptique du soleil (degrés) et obliquité (degrés)."""
    g = _rad((357.528 + 0.9856003 * d) % 360)
    lon = (280.460 + 0.9856474 * d + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360
    return lon, 23.439 - 0.0000004 * d


def _moon_ecliptic(d: float):
    """Longitude, latitude écliptiques (degrés) et parallaxe (degrés) de la lune."""
    t = d / 36525.0
    s = lambda a, b: math.sin(_rad(a + b * t))
    c = lambda a, b: math.cos(_rad(a + b * t))
    lon = (218.32 + 481267.881 * t
           + 6.29 * s(135.0, 477198.87) - 1.27 * s(259.3, -413335.36)
           + 0.66 * s(235.7, 890534.22) + 0.21 * s(269.9, 954397.74)
           - 0.19 * s(357.5, 35999.05) - 0.11 * s(186.5, 966404.03)) % 360
    lat = (5.13 * s(93.3, 483202.02) + 0.28 * s(228.2, 960400.89)
           - 0.28 * s(318.3, 6003.15) - 0.17 * s(217.6, -407332.21))
    par = (0.9508 + 0.0518 * c(135.0, 477198.87) + 0.0095 * c(259.3, -413335.36)
           + 0.0078 * c(235.7, 890534.22) + 0.0028 * c(269.9, 954397.74))
    return lon, lat, par


def _altitude(d: float, lon_ecl: float, lat_ecl: float, obliquity: float,
              latitude: float, longitude: float) -> float:
    """Hauteur géocentrique (degrés) d'un astre donné en coordonnées écliptiques."""
    l, b, e = _rad(lon_ecl), _rad(lat_ecl), _rad(obliquity)
    x = math.cos(b) * math.cos(l)
    y = math.cos(e) * math.cos(b) * math.sin(l) - math.sin(e) * math.sin(b)
    z = math.sin(e) * math.cos(b) * math.sin(l) + math.cos(e) * math.sin(b)
    ra, dec = math.atan2(y, x), math.asin(z)
    gmst = (280.46061837 + 360.98564736629 * d) % 360       # temps sidéral de Greenwich
    hour_angle = _rad(gmst + longitude) - ra
    phi = _rad(latitude)
    return _deg(math.asin(math.sin(phi) * math.sin(dec)
                          + math.cos(phi) * math.cos(dec) * math.cos(hour_angle)))


def sun_altitude(ts: float, latitude: float, longitude: float) -> float:
    d = _days(ts)
    lon, obl = _sun_ecliptic(d)
    return _altitude(d, lon, 0.0, obl, latitude, longitude)


def moon_altitude(ts: float, latitude: float, longitude: float) -> float:
    """Hauteur de la lune vue du lieu (corrigée de la parallaxe)."""
    d = _days(ts)
    lon, lat, par = _moon_ecliptic(d)
    _sun, obl = _sun_ecliptic(d)
    alt = _altitude(d, lon, lat, obl, latitude, longitude)
    return alt - par * math.cos(_rad(alt))


def moon_illumination(ts: float) -> float:
    """Part éclairée du disque lunaire, de 0 (nouvelle lune) à 100 (pleine lune)."""
    d = _days(ts)
    moon_lon, moon_lat, _par = _moon_ecliptic(d)
    sun_lon, _obl = _sun_ecliptic(d)
    cos_elong = math.cos(_rad(moon_lat)) * math.cos(_rad(moon_lon - sun_lon))
    return 50.0 * (1.0 - cos_elong)


def moon_waxing(ts: float) -> bool:
    """Vrai si la lune est croissante."""
    d = _days(ts)
    return (_moon_ecliptic(d)[0] - _sun_ecliptic(d)[0]) % 360 < 180


def period(ts: float, latitude: float, longitude: float) -> str:
    """« jour », « crépuscule » (soleil entre 0 et −12°) ou « nuit »."""
    alt = sun_altitude(ts, latitude, longitude)
    if alt >= SUN_HORIZON:
        return "jour"
    return "crépuscule" if alt >= NAUTICAL else "nuit"


def _crossing(t0: float, t1: float, latitude: float, longitude: float,
              target: float, rising: bool):
    """Premier franchissement de la hauteur `target` par le soleil entre t0 et t1."""
    step = 600.0
    f = lambda t: sun_altitude(t, latitude, longitude) - target
    t, prev = t0, f(t0)
    while t < t1:
        nxt = min(t + step, t1)
        cur = f(nxt)
        if (prev < 0 <= cur) if rising else (prev >= 0 > cur):
            lo, hi = t, nxt
            for _ in range(20):                      # dichotomie : précision < 1 s
                mid = (lo + hi) / 2
                if (f(mid) >= 0) == rising:
                    hi = mid
                else:
                    lo = mid
            return (lo + hi) / 2
        t, prev = nxt, cur
    return None


def night(noon_ts: float, latitude: float, longitude: float) -> dict:
    """Repères de la nuit qui commence après `noon_ts` (midi local du jour).

    Renvoie coucher, fins et débuts des crépuscules, lever (instants Unix ou
    None sous les hautes latitudes), et l'éclairement de la lune au milieu
    de la nuit."""
    end = noon_ts + 86400
    sunset = _crossing(noon_ts, end, latitude, longitude, SUN_HORIZON, rising=False)
    after = sunset if sunset else noon_ts
    sunrise = _crossing(after, end, latitude, longitude, SUN_HORIZON, rising=True)
    dusk = _crossing(after, end, latitude, longitude, CIVIL, rising=False)
    dawn = _crossing(dusk or after, end, latitude, longitude, CIVIL, rising=True)
    middle = (sunset + sunrise) / 2 if sunset and sunrise else noon_ts + 43200
    return {"sunset": sunset, "dusk": dusk, "dawn": dawn, "sunrise": sunrise,
            "moon_illumination": moon_illumination(middle),
            "moon_waxing": moon_waxing(middle)}
