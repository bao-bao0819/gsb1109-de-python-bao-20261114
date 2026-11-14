# Copyright (c) 2004,2018 Python-Metar Developers.
# Distributed under the terms of the BSD 2-Clause License.
# SPDX-License-Identifier: BSD-2-Clause
"""This module defines the Metar class.

A Metar object represents the weather report encoded by a single METAR code.
"""
import re
import calendar
import datetime
import warnings
import logging

from metar import __version__, __author__, __email__, __LICENSE__
from metar.Datatypes import (
    temperature,
    pressure,
    speed,
    distance,
    direction,
    precipitation,
)

# logger
_logger = logging.getLogger(__name__)


# Exceptions
class ParserError(Exception):
    """Exception raised when an unparseable group is found in body of the report."""

    pass


# regular expressions to decode various groups of the METAR code
MISSING_RE = re.compile(r"^[M/]+$")

TYPE_RE = re.compile(r"^(?P<type>METAR|SPECI)\s+")
COR_RE = re.compile(r"^(?P<cor>COR)\s+")
STATION_RE = re.compile(r"^(?P<station>[A-Z][A-Z0-9]{3})\s+")
TIME_RE = re.compile(
    r"""^(?P<day>\d\d)
        (?P<hour>\d\d)
        (?P<min>\d\d)Z?\s+""",
    re.VERBOSE,
)
MODIFIER_RE = re.compile(r"^(?P<mod>AUTO|COR AUTO|FINO|NIL|TEST|CORR?|RTD|CC[A-G])\s+")
WIND_RE = re.compile(
    r"""^(?P<dir>[\dO]{3}|[0O]|///|MMM|VRB)
        (?P<speed>P?[\dO]{2,3}|[/M]{2,3})
        (G(?P<gust>P?(\d{1,3}|[/M]{1,3})))?
        (?P<units>KTS?|LT|K|T|KMH|MPS)?
        (\s+(?P<varfrom>\d\d\d)V
        (?P<varto>\d\d\d))?\s+""",
    re.VERBOSE,
)
VISIBILITY_RE = re.compile(
    r"""^(?P<vis>(?P<dist>(M|P)?\d\d\d\d|////)
        (?P<dir>[NSEW][EW]? | NDV)? |
        (?P<distu>(M|P)?(\d+|\d\d?/\d\d?|\d+\s+\d/\d))
        (?P<units>SM|KM|M|U) |
        CAVOK )\s+""",
    re.VERBOSE,
)
RUNWAY_RE = re.compile(
    r"""^(RVRNO |
        R(?P<name>\d\d(RR?|LL?|C)?)/
        (?P<low>(M|P)?(\d\d\d\d|/{4}))
        (V(?P<high>(M|P)?\d\d\d\d))?
        (?P<unit>FT)?(?P<tendency>[/NDU]*))\s+""",
    re.VERBOSE,
)
WEATHER_RE = re.compile(
    r"""^(?P<int>(-|\+|VC)*)
        (?P<desc>(MI|PR|BC|DR|BL|SH|TS|FZ)+)?
        (?P<prec>(DZ|RA|SN|SG|IC|PL|GR|GS|UP|/)*)
        (?P<obsc>BR|FG|FU|VA|DU|SA|HZ|PY)?
        (?P<other>PO|SQ|FC|SS|DS|NSW|/+)?
        (?P<int2>[-+])?\s+""",
    re.VERBOSE,
)
SKY_RE = re.compile(
    r"""^(?P<cover>VV|CLR|SKC|SCK|NSC|NCD|BKN|SCT|FEW|[O0]VC|///)
        (?P<height>[\dO]{2,4}|///)?
        (?P<cloud>([A-Z][A-Z]+|///))?\s+""",
    re.VERBOSE,
)
TEMP_RE = re.compile(
    r"""^(?P<temp>(M|-)?\d{1,2}|//|XX|MM)/
        (?P<dewpt>(M|-)?\d{1,2}|//|XX|MM)?\s+""",
    re.VERBOSE,
)
PRESS_RE = re.compile(
    r"""^(?P<unit>A|Q|QNH)?
        (?P<press>[\dO]{3,4}|////)
        (?P<unit2>INS)?\s+""",
    re.VERBOSE,
)
RECENT_RE = re.compile(
    r"""^RE(?P<desc>MI|PR|BC|DR|BL|SH|TS|FZ)?
        (?P<prec>(DZ|RA|SN|SG|IC|PL|GR|GS|UP)*)?
        (?P<obsc>BR|FG|FU|VA|DU|SA|HZ|PY)?
        (?P<other>PO|SQ|FC|SS|DS)?\s+""",
    re.VERBOSE,
)
WINDSHEAR_RE = re.compile(r"^(WS\s+)?(ALL\s+RWY|R(WY)?(?P<name>\d\d(RR?|L?|C)?))\s+")
COLOR_RE = re.compile(
    r"""^(BLACK)?(BLU|GRN|WHT|RED)\+?
                        (/?(BLACK)?(BLU|GRN|WHT|RED)\+?)*\s*""",
    re.VERBOSE,
)
RUNWAYSTATE_RE = re.compile(
    r"""((?P<snoclo>R/SNOCLO) |
        ((?P<name>\d\d) | R(?P<namenew>\d\d)(RR?|LL?|C)?/?)
        ((?P<special> SNOCLO|CLRD(\d\d|//)) |
        (?P<deposit>(\d|/))
        (?P<extent>(\d|/))
        (?P<depth>(\d\d|//))
        (?P<friction>(\d\d|//))))\s+""",
    re.VERBOSE,
)
TREND_RE = re.compile(r"^(?P<trend>TEMPO|BECMG|FCST|NOSIG)\s+")

TRENDTIME_RE = re.compile(r"(?P<when>(FM|TL|AT))(?P<hour>\d\d)(?P<min>\d\d)\s+")

REMARK_RE = re.compile(r"^(RMKS?|NOSPECI|NOSIG)\s+")

# regular expressions for remark groups
AUTO_RE = re.compile(r"^AO(?P<type>\d)\s+")
SEALVL_PRESS_RE = re.compile(r"^SLP(?P<press>\d\d\d)\s+")
PEAK_WIND_RE = re.compile(
    r"""^P[A-Z]\s+WND\s+
        (?P<dir>\d\d\d)
        (?P<speed>P?\d\d\d?)/
        (?P<hour>\d\d)?
        (?P<min>\d\d)\s+""",
    re.VERBOSE,
)
WIND_SHIFT_RE = re.compile(
    r"""^WSHFT\s+
        (?P<hour>\d\d)?
        (?P<min>\d\d)
        (\s+(?P<front>FROPA))?\s+""",
    re.VERBOSE,
)
PRECIP_1HR_RE = re.compile(r"^P(?P<precip>\d\d\d\d)\s+")
PRECIP_24HR_RE = re.compile(
    r"""^(?P<type>6|7)
        (?P<precip>\d\d\d\d)\s+""",
    re.VERBOSE,
)
PRESS_3HR_RE = re.compile(
    r"""^5(?P<tend>[0-8])
(?P<press>\d\d\d)\s+""",
    re.VERBOSE,
)
TEMP_1HR_RE = re.compile(
    r"""^T(?P<tsign>0|1)
        (?P<temp>\d\d\d)
        ((?P<dsign>0|1)
        (?P<dewpt>\d\d\d))?\s+""",
    re.VERBOSE,
)
TEMP_6HR_RE = re.compile(
    r"""^(?P<type>1|2)
        (?P<sign>0|1)
        (?P<temp>\d\d\d)\s+""",
    re.VERBOSE,
)
TEMP_24HR_RE = re.compile(
    r"""^4(?P<smaxt>0|1)
        (?P<maxt>\d\d\d)
        (?P<smint>0|1)
        (?P<mint>\d\d\d)\s+""",
    re.VERBOSE,
)
UNPARSED_RE = re.compile(r"(?P<group>\S+)\s+")

LIGHTNING_RE = re.compile(
    r"""^((?P<freq>OCNL|FRQ|CONS)\s+)?
        LTG(?P<type>(IC|CC|CG|CA)*)
        ( \s+(?P<loc>( OHD | VC | DSNT\s+ | \s+AND\s+ |
        [NSEW][EW]? (-[NSEW][EW]?)* )+) )?\s+""",
    re.VERBOSE,
)

TS_LOC_RE = re.compile(
    r"""TS(\s+(?P<loc>( OHD | VC | DSNT\s+ | \s+AND\s+ |
        [NSEW][EW]? (-[NSEW][EW]?)* )+))?
        ( \s+MOV\s+(?P<dir>[NSEW][EW]?) )?\s+""",
    re.VERBOSE,
)
SNOWDEPTH_RE = re.compile(r"""^4/(?P<snowdepth>\d\d\d)\s+""")
ICE_ACCRETION_RE = re.compile(
    r"^I(?P<ice_accretion_hours>[136])(?P<ice_accretion_depth>\d\d\d)\s+"
)


# translation of weather location codes
loc_terms = [("OHD", "overhead"), ("DSNT", "distant"), ("AND", "and"), ("VC", "nearby")]


def xlate_loc(loc):
    """Substitute English terms for the location codes in the given string."""
    for code, english in loc_terms:
        loc = loc.replace(code, english)
    return loc


# translation of the sky-condition codes into english
SKY_COVER = {
    "SKC": "clear",
    "CLR": "clear",
    "NSC": "clear",
    "NCD": "clear",
    "FEW": "a few ",
    "SCT": "scattered ",
    "BKN": "broken ",
    "OVC": "overcast",
    "///": "",
    "VV": "indefinite ceiling",
}


CLOUD_TYPE = {
    "AC": "altocumulus",
    "ACC": "altocumulus castellanus",
    "ACSL": "standing lenticular altocumulus",
    "AS": "altostratus",
    "CB": "cumulonimbus",
    "CBMAM": "cumulonimbus mammatus",
    "CCSL": "standing lenticular cirrocumulus",
    "CC": "cirrocumulus",
    "CI": "cirrus",
    "CS": "cirrostratus",
    "CU": "cumulus",
    "NS": "nimbostratus",
    "SC": "stratocumulus",
    "ST": "stratus",
    "SCSL": "standing lenticular stratocumulus",
    "TCU": "towering cumulus",
}

# translation of the present-weather codes into english
WEATHER_INT = {
    "-": "light",
    "+": "heavy",
    "-VC": "nearby light",
    "+VC": "nearby heavy",
    "VC": "nearby",
}
WEATHER_DESC = {
    "MI": "shallow",
    "PR": "partial",
    "BC": "patches of",
    "DR": "low drifting",
    "BL": "blowing",
    "SH": "showers",
    "TS": "thunderstorm",
    "FZ": "freezing",
}
WEATHER_PREC = {
    "DZ": "drizzle",
    "RA": "rain",
    "SN": "snow",
    "SG": "snow grains",
    "IC": "ice crystals",
    "PL": "ice pellets",
    "GR": "hail",
    "GS": "snow pellets",
    "UP": "unknown precipitation",
    "//": "",
}
WEATHER_OBSC = {
    "BR": "mist",
    "FG": "fog",
    "FU": "smoke",
    "VA": "volcanic ash",
    "DU": "dust",
    "SA": "sand",
    "HZ": "haze",
    "PY": "spray",
}
WEATHER_OTHER = {
    "PO": "sand whirls",
    "SQ": "squalls",
    "FC": "funnel cloud",
    "SS": "sandstorm",
    "DS": "dust storm",
    "NSW": "no significant weather",
}

WEATHER_SPECIAL = {"+FC": "tornado"}

COLOR = {"BLU": "blue", "GRN": "green", "WHT": "white"}

# translation of various remark codes into English
PRESSURE_TENDENCY = {
    "0": "increasing, then decreasing",
    "1": "increasing more slowly",
    "2": "increasing",
    "3": "increasing more quickly",
    "4": "steady",
    "5": "decreasing, then increasing",
    "6": "decreasing more slowly",
    "7": "decreasing",
    "8": "decreasing more quickly",
}

LIGHTNING_FREQUENCY = {"OCNL": "occasional", "FRQ": "frequent", "CONS": "constant"}
LIGHTNING_TYPE = {
    "IC": "intracloud",
    "CC": "cloud-to-cloud",
    "CG": "cloud-to-ground",
    "CA": "cloud-to-air",
}

REPORT_TYPE = {
    "METAR": "routine report",
    "SPECI": "special report",
    "AUTO": "automatic report",
    "COR": "manually corrected report",
}


# Helper functions
def _sanitize(code):
    """Some string prep to improve parsing fidelity."""
    # Remove extraneous whitespace, any trailing =, then add trailing
    # whitespace as regex matches need that.
    return "%s " % (code.strip().rstrip("="),)


def _report_match(handler, match):
    """Report success or failure of the given handler function. (DEBUG)"""
    if match:
        _logger.debug("%s matched '%s'", handler.__name__, match)
    else:
        _logger.debug("%s didn't match...", handler.__name__)


def _unparsedGroup(self, d):
    """
    Handle otherwise unparseable main-body groups.
    """
    self._unparsed_groups.append(d["group"])


# ---------------------------------------------------------------------------
# Trend (BECMG / TEMPO) timeline support
#
# A trend group is a forecast, so it describes conditions that are not the
# observation itself.  The classes below keep the observation, the permanent
# (BECMG) changes and the temporary (TEMPO) overlays apart while still making
# it possible to ask what the weather is at a particular instant.
# ---------------------------------------------------------------------------

TREND_INDICATORS = ("BECMG", "TEMPO", "FCST", "NOSIG")

# A trend time group is one of FMhhmm, TLhhmm or AThhmm.  Some reports also
# use the older shorthand "hhmm" for a window, e.g. BECMG 0306.  Both forms
# are handled by _parse_trend_time().
TREND_TIME_TOKEN_RE = re.compile(r"^(?P<when>FM|TL|AT)(?P<hour>\d\d)(?P<min>\d\d)$")
BARE_TREND_TIME_RE = re.compile(r"^(?P<hour>\d\d)(?P<min>\d\d)$")


class Conditions(object):
    """A snapshot of the decoded weather elements at one instant.

    The attribute names deliberately mirror the attributes of :class:`Metar`
    so that a condition can be used in the same way as the observation from
    which it was derived.  ``Conditions`` objects also support a small number
    of convenient aliases through ``[]`` (``"visibility"``, ``"wind"``,
    ``"temperature"``, ...).
    """

    FIELDS = (
        "wind_dir",
        "wind_speed",
        "wind_gust",
        "wind_dir_from",
        "wind_dir_to",
        "vis",
        "vis_dir",
        "max_vis",
        "max_vis_dir",
        "weather",
        "sky",
        "cavok",
        "temp",
        "dewpt",
        "press",
        "recent",
        "runway",
        "runway_tendencies",
        "windshear",
        "snowdepth",
    )

    def __init__(self, **kwargs):
        for field in self.FIELDS:
            value = kwargs.get(field)
            if field in ("weather", "recent", "sky", "runway", "windshear"):
                value = list(value) if value is not None else []
            elif field == "runway_tendencies":
                value = list(value) if value is not None else []
            setattr(self, field, value)

    @classmethod
    def from_metar(cls, metar):
        """Return the conditions observed in a parsed :class:`Metar`."""
        return cls(
            wind_dir=metar.wind_dir,
            wind_speed=metar.wind_speed,
            wind_gust=metar.wind_gust,
            wind_dir_from=metar.wind_dir_from,
            wind_dir_to=metar.wind_dir_to,
            vis=metar.vis,
            vis_dir=metar.vis_dir,
            max_vis=metar.max_vis,
            max_vis_dir=metar.max_vis_dir,
            weather=metar.weather,
            sky=metar.sky,
            temp=metar.temp,
            dewpt=metar.dewpt,
            press=metar.press,
            recent=metar.recent,
            runway=metar.runway,
            runway_tendencies=metar.runway_tendencies,
            windshear=metar.windshear,
            snowdepth=metar.snowdepth,
        )

    def copy(self):
        """Return a shallow copy (the element lists are copied as well)."""
        return Conditions(**{field: getattr(self, field) for field in self.FIELDS})

    def __getitem__(self, key):
        aliases = {
            "visibility": "vis",
            "wind": "wind",
            "temperature": "temp",
            "dew_point": "dewpt",
            "dewpoint": "dewpt",
            "pressure": "press",
            "present_weather": "weather",
            "clouds": "sky",
            "cloud": "sky",
            "runway_visual_range": "runway",
        }
        field = aliases.get(key, key)
        if field == "wind":
            return {
                "direction": self.wind_dir,
                "speed": self.wind_speed,
                "gust": self.wind_gust,
                "variable_from": self.wind_dir_from,
                "variable_to": self.wind_dir_to,
            }
        if field not in self.FIELDS:
            raise KeyError(key)
        return getattr(self, field)

    def get(self, key, default=None):
        """Mapping-style access with a default."""
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def __getattr__(self, name):
        # Friendly aliases such as ``snapshot.visibility``.
        aliases = {
            "visibility": "vis",
            "temperature": "temp",
            "dew_point": "dewpt",
            "dewpoint": "dewpt",
            "pressure": "press",
            "present_weather": "weather",
            "clouds": "sky",
            "cloud": "sky",
            "runway_visual_range": "runway",
        }
        field = aliases.get(name)
        if field is not None and field != "wind":
            return object.__getattribute__(self, field)
        raise AttributeError(name)

    def as_dict(self):
        """Return the conditions as a plain dictionary."""
        return {field: getattr(self, field) for field in self.FIELDS}

    def to_dict(self):
        """Alias for :meth:`as_dict`."""
        return self.as_dict()

    def wind(self):
        """Return a dictionary describing the wind."""
        return self["wind"]

    def weather_text(self):
        """Return a short textual description of the present weather."""
        if not self.weather:
            return ""
        return Metar._weather(self, self.weather)

    def __repr__(self):
        parts = []
        if self.vis is not None:
            parts.append("vis=%s" % (self.vis,))
        if self.weather:
            parts.append("weather=%r" % (self.weather,))
        if self.sky:
            parts.append("sky=%r" % (self.sky,))
        if self.wind_speed is not None:
            parts.append("wind=%s" % (self.wind_speed,))
        return "Conditions(%s)" % ", ".join(parts)


class TrendSegment(object):
    """One record in a metar's trend timeline.

    ``kind`` is one of ``"observation"``, ``"becoming"``, ``"temporary"``,
    ``"forecast"`` or ``"nosig"``.  For a ``becoming`` segment ``start`` and
    ``end`` bound the period during which the change may happen and
    ``effective`` is the instant from which the new conditions prevail.  For
    a ``temporary`` segment ``start``/``end`` are the window in which the
    temporary phenomenon may occur.  Segment times are complete datetimes,
    anchored to the date of the report (so a window such as FM2300 TL0100
    correctly crosses midnight).
    """

    def __init__(
        self,
        kind,
        start=None,
        end=None,
        conditions=None,
        changes=None,
        before=None,
        after=None,
        effective=None,
        tokens=None,
        index=0,
        uncertain=None,
        indicator=None,
        definite=None,
    ):
        self.kind = kind
        self.start = start
        self.end = end
        self.conditions = conditions
        self.changes = changes
        self.before = before
        self.after = after
        self.effective = effective if effective is not None else start
        self.tokens = list(tokens or [])
        self.index = index
        if definite is None:
            definite = end is None or start is None or end == start
        self.definite = bool(definite)
        if uncertain is None:
            uncertain = start is not None and end is not None and start < end
        self.uncertain = bool(uncertain)
        self.indicator = indicator

    # -- timing ------------------------------------------------------------
    def is_established(self, when):
        """Return True once the new conditions are definitely in force."""
        if self.start is None or when < self.start:
            return False
        if self.definite:
            return when >= (self.effective if self.effective is not None else self.start)
        if self.end is None:
            return when >= self.start
        return when >= self.end

    def is_pending(self, when):
        """Return True while the change may or may not have happened."""
        if self.start is None or when < self.start:
            return False
        if self.definite:
            return False
        if self.end is None:
            return True
        if self.end <= self.start:
            return False
        return when < self.end

    # -- aliases -----------------------------------------------------------
    @property
    def type(self):
        """The code used for this segment (BECMG, TEMPO, ...)."""
        return self.indicator or self.kind.upper()

    @property
    def category(self):
        """Group the segment as observation, permanent or temporary."""
        if self.kind in ("observation", "nosig"):
            return "observation"
        if self.kind == "becoming":
            return "permanent"
        return "temporary"

    @property
    def change(self):
        """The change indicator, if this is a forecast segment."""
        return self.indicator

    @property
    def is_observation(self):
        return self.kind == "observation"

    @property
    def is_becoming(self):
        return self.kind == "becoming"

    @property
    def is_temporary(self):
        return self.kind in ("temporary", "forecast")

    @property
    def is_permanent(self):
        return self.kind == "becoming"

    @property
    def window_start(self):
        return self.start

    @property
    def window_end(self):
        return self.end

    @property
    def begin(self):
        return self.start

    @property
    def until(self):
        return self.end

    @property
    def valid_from(self):
        return self.start

    @property
    def valid_to(self):
        return self.end

    @property
    def text(self):
        return " ".join(self.tokens)

    @property
    def code(self):
        return self.text

    @property
    def midpoint(self):
        """The midpoint of the segment's time window (or its start)."""
        if self.start is None:
            return None
        if self.end is None or self.end <= self.start:
            return self.start
        return self.start + (self.end - self.start) / 2

    @property
    def duration(self):
        if self.start is None or self.end is None:
            return None
        return self.end - self.start

    def contains(self, when):
        """Return True when ``when`` lies in this segment's window."""
        if self.start is None:
            return False
        if self.end is None:
            return when >= self.start
        if self.end == self.start:
            return when == self.start
        return self.start <= when < self.end

    def __getitem__(self, key):
        if key in ("kind", "type", "category", "start", "end", "effective"):
            return getattr(self, key)
        if key in ("conditions", "changes", "before", "after", "uncertain"):
            return getattr(self, key)
        if key in ("window_start", "valid_from", "begin"):
            return self.start
        if key in ("window_end", "valid_to", "until"):
            return self.end
        raise KeyError(key)

    def as_dict(self):
        """Return a plain dictionary describing the segment."""
        return {
            "kind": self.kind,
            "type": self.type,
            "category": self.category,
            "start": self.start,
            "end": self.end,
            "effective": self.effective,
            "uncertain": self.uncertain,
            "conditions": self.conditions,
            "changes": self.changes,
            "tokens": list(self.tokens),
        }

    def __repr__(self):
        return "TrendSegment(%s, %s..%s)" % (self.kind, self.start, self.end)


class TrendSegmentList(list):
    """A list of :class:`TrendSegment` that may also be called."""

    def __call__(self):
        return self

    def at(self, when):
        """Return the segments whose window contains ``when``."""
        return TrendSegmentList(segment for segment in self if segment.contains(when))

    def of_kind(self, kind):
        """Return the segments of a particular kind."""
        return TrendSegmentList(segment for segment in self if segment.kind == kind)

    @property
    def observations(self):
        return self.of_kind("observation")

    @property
    def becoming(self):
        return self.of_kind("becoming")

    @property
    def temporary(self):
        return TrendSegmentList(
            segment for segment in self if segment.kind in ("temporary", "forecast")
        )

    @property
    def permanent(self):
        return self.becoming


class TrendSnapshot(object):
    """The conditions at one instant, together with their certainty.

    ``uncertain`` is true when the queried time falls inside a change window
    (for BECMG) or inside a temporary-weather window (for TEMPO).  In that
    case ``possible`` contains the alternatives that the report supports;
    ``conditions`` is the first/expected one, ``prevailing`` is the unmodified
    underlying state and ``temporary`` lists the overlays that may apply.
    """

    def __init__(
        self,
        time,
        conditions,
        uncertain=False,
        possible=None,
        prevailing=None,
        temporary=None,
        segments=None,
        status=None,
    ):
        self.time = time
        self.conditions = conditions
        self.prevailing = prevailing if prevailing is not None else conditions
        self.underlying = self.prevailing
        self.uncertain = bool(uncertain)
        self.possible = list(possible) if possible is not None else [conditions]
        self.possibilities = self.possible
        self.temporary = list(temporary or [])
        self.overlays = self.temporary
        self.segments = list(segments or [])
        self.status = status or ("uncertain" if self.uncertain else "certain")

    @property
    def certain(self):
        return not self.uncertain

    @property
    def is_uncertain(self):
        return self.uncertain

    @property
    def ambiguous(self):
        return self.uncertain

    @property
    def certainty(self):
        return self.status

    @property
    def alternatives(self):
        return self.possible[1:]

    def __getattr__(self, name):
        # Delegate weather-element access to the primary conditions.
        if name in Conditions.FIELDS or name in (
            "visibility",
            "wind",
            "temperature",
            "dew_point",
            "dewpoint",
            "pressure",
            "present_weather",
            "clouds",
            "cloud",
        ):
            return getattr(self.conditions, name)
        raise AttributeError(name)

    def __getitem__(self, key):
        metadata = {
            "time": self.time,
            "conditions": self.conditions,
            "prevailing": self.prevailing,
            "underlying": self.underlying,
            "uncertain": self.uncertain,
            "possible": self.possible,
            "temporary": self.temporary,
            "status": self.status,
        }
        if key in metadata:
            return metadata[key]
        return self.conditions[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def as_dict(self):
        result = self.conditions.as_dict()
        result.update(
            {
                "time": self.time,
                "uncertain": self.uncertain,
                "status": self.status,
                "possible": [state.as_dict() for state in self.possible],
                "prevailing": self.prevailing.as_dict(),
                "temporary": list(self.temporary),
            }
        )
        return result

    def to_dict(self):
        return self.as_dict()

    def __repr__(self):
        return "TrendSnapshot(%s, %s%s)" % (
            self.time,
            self.conditions,
            ", uncertain" if self.uncertain else "",
        )


class TrendView(object):
    """A convenient wrapper around a report's trend segments."""

    def __init__(self, metar):
        self.metar = metar
        self.segments = metar.trend_segments
        self.timeline = self.segments

    def __call__(self):
        return self

    def __iter__(self):
        return iter(self.segments)

    def __len__(self):
        return len(self.segments)

    def __getitem__(self, index):
        return self.segments[index]

    def at(self, when):
        return self.metar.conditions_at(when)

    def conditions_at(self, when):
        return self.metar.conditions_at(when)

    def __repr__(self):
        return "TrendView(%r)" % (self.segments,)


# scratch object used to decode trend groups without disturbing the Metar
class _TrendScratch(object):
    def __init__(self, station_id=None):
        self.station_id = station_id
        self.wind_dir = None
        self.wind_speed = None
        self.wind_gust = None
        self.wind_dir_from = None
        self.wind_dir_to = None
        self.vis = None
        self.vis_dir = None
        self.max_vis = None
        self.max_vis_dir = None
        self.weather = []
        self.sky = []


def _value_key(value):
    """Return a comparable key for a decoded weather element."""
    if isinstance(value, (list, tuple)):
        return tuple(_value_key(item) for item in value)
    value_method = getattr(value, "value", None)
    if callable(value_method):
        try:
            return value_method()
        except TypeError:
            pass
    return value


def _conditions_equal(left, right):
    """Compare two :class:`Conditions` objects element by element."""
    for field in Conditions.FIELDS:
        if _value_key(getattr(left, field)) != _value_key(getattr(right, field)):
            return False
    return True


def _unique_conditions(states):
    """Return ``states`` without duplicates, preserving their order."""
    unique = []
    for state in states:
        if not any(_conditions_equal(state, seen) for seen in unique):
            unique.append(state)
    return unique


def _apply_trend_changes(base, changes):
    """Return a copy of ``base`` with the explicitly changed elements applied."""
    if changes is None:
        return base.copy()
    if changes.get("cavok"):
        result = base.copy()
        result.vis = distance(10000, "M", ">")
        result.vis_dir = None
        result.max_vis = None
        result.max_vis_dir = None
        result.weather = []
        result.sky = []
        result.cavok = True
        return result
    result = base.copy()
    for field in ("wind_dir", "wind_speed", "wind_gust", "wind_dir_from", "wind_dir_to"):
        if "wind" in changes:
            setattr(result, field, changes.get(field))
    for field in ("vis", "vis_dir", "max_vis", "max_vis_dir"):
        if "visibility" in changes:
            setattr(result, field, changes.get(field))
    if "weather" in changes:
        result.weather = list(changes["weather"])
    if "sky" in changes:
        result.sky = list(changes["sky"])
    if "cavok" in changes:
        result.cavok = changes["cavok"]
    for field in ("temp", "dewpt", "press", "snowdepth"):
        if field in changes:
            setattr(result, field, changes[field])
    return result


def _decode_trend_conditions(tokens, metar):
    """Decode the forecast groups following a trend indicator.

    Returns ``(changes, remaining_tokens)``.  ``changes`` only contains the
    elements that the trend group actually mentions, so applying it to an
    earlier state preserves everything else.
    """
    scratch = _TrendScratch(getattr(metar, "station_id", None))
    changes = {}
    remaining = []
    weather_seen = False
    sky_seen = False
    for token in tokens:
        padded = token + " "
        match = WIND_RE.match(padded)
        if match and match.group("dir") is not None:
            Metar._handleWind(scratch, match.groupdict())
            changes["wind"] = True
            for field in (
                "wind_dir",
                "wind_speed",
                "wind_gust",
                "wind_dir_from",
                "wind_dir_to",
            ):
                changes[field] = getattr(scratch, field)
            continue
        match = VISIBILITY_RE.match(padded)
        if match:
            Metar._handleVisibility(scratch, match.groupdict())
            changes["visibility"] = True
            for field in ("vis", "vis_dir", "max_vis", "max_vis_dir"):
                changes[field] = getattr(scratch, field)
            if token.upper() == "CAVOK":
                changes["cavok"] = True
            continue
        match = WEATHER_RE.match(padded)
        if match:
            Metar._handleWeather(scratch, match.groupdict())
            weather_seen = True
            continue
        match = SKY_RE.match(padded)
        if match:
            Metar._handleSky(scratch, match.groupdict())
            sky_seen = True
            continue
        # Colour codes and unrecognised groups do not describe the weather.
        remaining.append(token)
    if weather_seen:
        if any(weatheri[4] == "NSW" for weatheri in scratch.weather):
            changes["weather"] = []
        else:
            changes["weather"] = list(scratch.weather)
    if sky_seen:
        changes["sky"] = list(scratch.sky)
    return changes, remaining


def _parse_trend_time_tokens(tokens):
    """Split a trend group into its time specification and weather groups.

    ``tokens`` are the raw trend tokens after the indicator.  The returned
    dictionary has any of ``fm``, ``tl``, ``at``, ``period`` (hour pairs) and
    ``remaining``.
    """
    spec = {"fm": None, "tl": None, "at": None, "period": None}
    remaining = []
    for token in tokens:
        match = TREND_TIME_TOKEN_RE.match(token)
        if match:
            value = (int(match.group("hour")), int(match.group("min")))
            spec[match.group("when").lower()] = value
            continue
        remaining.append(token)
    # Older reports give the change period as two hours, e.g. "BECMG 0306"
    # for 03:00 to 06:00.  This shorthand only appears as the first group
    # after the indicator; a later four-digit group is a visibility value.
    if spec["fm"] is None and spec["tl"] is None and spec["at"] is None:
        if remaining:
            match = BARE_TREND_TIME_RE.match(remaining[0])
            if match:
                start_hour = int(match.group("hour"))
                end_hour = int(match.group("min"))
                if start_hour <= 23 and end_hour <= 23:
                    spec["period"] = (start_hour, end_hour)
                    remaining = remaining[1:]
    spec["remaining"] = remaining
    return spec


# METAR report objects
debug = False


class Metar(object):
    """METAR (aviation meteorology report)"""

    def __init__(self, metarcode, month=None, year=None, strict=True):
        """
        Parse raw METAR code.

        Parameters
        ----------
        metarcode : str
        month, year : int, optional
          Date values to be used when parsing a non-current METAR code. If not
          provided, then the month and year are guessed from the current date.
        strict : bool (default is True)
          This option determines if a ``ParserError`` is raised when
          unparsable groups are found or an unexpected exception is encountered.
          Setting this to `False` will prevent exceptions from being raised and
          only generate warning messages.
        """

        self.code = metarcode  # original METAR code
        self.type = "METAR"  # METAR (routine) or SPECI (special)
        self.correction = None  # COR (corrected - WMO spec)
        self.mod = "AUTO"  # AUTO (automatic) or COR (corrected - US spec)
        self.station_id = None  # 4-character ICAO station code
        self.time = None  # observation time [datetime]
        self.cycle = None  # observation cycle (0-23) [int]
        self.wind_dir = None  # wind direction [direction]
        self.wind_speed = None  # wind speed [speed]
        self.wind_gust = None  # wind gust speed [speed]
        self.wind_dir_from = None  # beginning of range for win dir [direction]
        self.wind_dir_to = None  # end of range for wind dir [direction]
        self.vis = None  # visibility [distance]
        self.vis_dir = None  # visibility direction [direction]
        self.max_vis = None  # visibility [distance]
        self.max_vis_dir = None  # visibility direction [direction]
        self.temp = None  # temperature (C) [temperature]
        self.dewpt = None  # dew point (C) [temperature]
        self.press = None  # barometric pressure [pressure]
        self.runway = []  # runway visibility (list of tuples)
        self.runway_tendencies = []  # U, D, N or None, in the same order as runway
        self.weather = []  # present weather (list of tuples)
        self.recent = []  # recent weather (list of tuples)
        self.sky = []  # sky conditions (list of tuples)
        self.windshear = []  # runways w/ wind shear (list of strings)
        self.wind_speed_peak = None  # peak wind speed in last hour
        self.wind_dir_peak = None  # direction of peak wind speed in last hour
        self.peak_wind_time = None  # time of peak wind observation [datetime]
        self.wind_shift_time = None  # time of wind shift [datetime]
        self.max_temp_6hr = None  # max temp in last 6 hours
        self.min_temp_6hr = None  # min temp in last 6 hours
        self.max_temp_24hr = None  # max temp in last 24 hours
        self.min_temp_24hr = None  # min temp in last 24 hours
        self.press_sea_level = None  # sea-level pressure
        self.precip_1hr = None  # precipitation over the last hour
        self.precip_3hr = None  # precipitation over the last 3 hours
        self.precip_6hr = None  # precipitation over the last 6 hours
        self.precip_24hr = None  # precipitation over the last 24 hours
        self.snowdepth = None  # snow depth (distance)
        self.ice_accretion_1hr = None  # ice accretion over the past hour
        self.ice_accretion_3hr = None  # ice accretion over the past 3 hours
        self.ice_accretion_6hr = None  # ice accretion over the past 6 hours
        self._trend = False  # trend groups present (bool)
        self._trend_groups = []  # trend forecast groups
        self._trend_sections = []  # trend groups, split by BECMG/TEMPO indicator
        self._trend_segments = TrendSegmentList()  # decoded trend timeline
        self._remarks = []  # remarks (list of strings)
        self._unparsed_groups = []
        self._unparsed_remarks = []

        self._now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        self._month = month
        self._year = year

        # Do some string prep before parsing
        code = _sanitize(self.code)
        try:
            ngroup = len(self.handlers)
            igroup = 0
            ifailed = -1
            while igroup < ngroup and code:
                pattern, handler, repeatable = self.handlers[igroup]
                if debug:
                    _logger.debug("%s: %s", handler.__name__, code)
                m = pattern.match(code)
                while m:
                    ifailed = -1
                    if debug:
                        _report_match(handler, m.group())
                    handler(self, m.groupdict())
                    code = code[m.end():]
                    if self._trend:
                        code = self._do_trend_handlers(code)
                    if not repeatable:
                        break

                    if debug:
                        _logger.debug("%s: %s", handler.__name__, code)
                    m = pattern.match(code)
                if not m and ifailed < 0:
                    ifailed = igroup
                igroup += 1
                if igroup == ngroup and not m:
                    pattern, handler = (UNPARSED_RE, _unparsedGroup)
                    if debug:
                        _logger.debug("%s: %s", handler.__name__, code)
                    m = pattern.match(code)
                    if debug:
                        _report_match(handler, m.group())
                    handler(self, m.groupdict())
                    code = code[m.end():]
                    igroup = ifailed
                    ifailed = -2  # if it's still -2 when we run out of main-body
                    #  groups, we'll try parsing this group as a remark
            if pattern == REMARK_RE or self.press:
                while code:
                    for pattern, handler in self.remark_handlers:
                        if debug:
                            _logger.debug("%s: %s", handler.__name__, code)
                        m = pattern.match(code)
                        if m:
                            if debug:
                                _report_match(handler, m.group())
                            handler(self, m.groupdict())
                            code = pattern.sub("", code, 1)
                            break

        except Exception as err:
            message = ("%s failed while processing '%s'\n\t%s") % (
                handler.__name__,
                code,
                "\n\t".join(err.args),
            )
            if strict:
                raise ParserError(message)
            else:
                warnings.warn(message, RuntimeWarning)

        if self._unparsed_groups:
            code = " ".join(self._unparsed_groups)
            message = "Unparsed groups in body '%s' while processing '%s'" % (
                code,
                metarcode,
            )
            if strict:
                raise ParserError(message)
            else:
                warnings.warn(message, RuntimeWarning)

        # The observation is now known, so the trend groups can be decoded
        # into a timeline that keeps the observation, the permanent changes
        # and the temporary overlays apart.
        self._build_trend_segments()

    @property
    def decode_completed(self):
        """
        Indicate whether the decoding was complete for non-remark elements.
        """
        return not self._unparsed_groups

    def _do_trend_handlers(self, code):
        for pattern, handler, repeatable in self.trend_handlers:
            if debug:
                print(handler.__name__, ":", code)
            m = pattern.match(code)
            while m:
                if debug:
                    _report_match(handler, m.group())
                group = m.group().strip()
                self._trend_groups.append(group)
                if self._trend_sections:
                    self._trend_sections[-1]["tokens"].append(group)
                handler(self, m.groupdict())
                code = code[m.end():]
                if not repeatable:
                    break
                m = pattern.match(code)
        return code

    def __str__(self):
        return self.string()

    # -- trend timeline ----------------------------------------------------

    def _trend_anchor(self, day, hour, minute):
        """Turn a trend time group into a datetime anchored to this report.

        Trend groups carry no day, so they are placed on the observation day.
        A time more than 12 hours behind the observation belongs to the next
        day; that keeps windows such as FM2300 TL0100 and reports issued just
        before midnight on one continuous timeline.
        """
        if day is None or self.time is None:
            return None
        candidate = datetime.datetime(
            self.time.year, self.time.month, self.time.day, hour, minute
        )
        if self.time.month is not None and day != self.time.day:
            # Rare reports put the day in the trend time group.  Only accept
            # it when it is a plausible neighbour of the observation day.
            base = datetime.datetime(
                self.time.year, self.time.month, max(1, self.time.day)
            )
            delta = day - self.time.day
            if delta < 0:
                delta += calendar.monthrange(self.time.year, self.time.month)[1]
            candidate = base + datetime.timedelta(days=delta)
        if candidate < self.time - datetime.timedelta(hours=12):
            candidate += datetime.timedelta(days=1)
        return candidate

    def _trend_time(self, value, reference=None):
        """Resolve a trend time tuple ``(hour, minute)`` to a datetime.

        Trend groups carry no date.  A start time is placed at the first
        occurrence at or after the observation, and an end time at the first
        occurrence at or after the start.  Anchoring both ends to the report
        this way keeps a window that crosses midnight (FM2300 TL0100) on one
        continuous timeline instead of letting each end pick its own day.
        """
        if value is None:
            return None
        hour, minute = value
        anchor = reference if reference is not None else self.time
        if anchor is None:
            return None
        candidate = anchor.replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0
        )
        if reference is not None:
            while candidate < reference:
                candidate += datetime.timedelta(days=1)
            return candidate
        while candidate < self.time:
            candidate += datetime.timedelta(days=1)
        return candidate

    def _trend_period_end(self, start=None):
        """Return the end of the report's trend period.

        A trend forecast covers the period from the observation (or the given
        start) up to two hours later.  It is used when a trend group omits its
        time specification altogether.
        """
        base = start if start is not None else self.time
        if base is None:
            return None
        return base + datetime.timedelta(hours=2)

    def _trend_window(self, spec):
        """Return ``(start, end, effective, definite)`` for a trend group.

        ``definite`` is True only when the group names the instant the change
        takes effect (FM or AT).  A change window (``FM...TL...``, ``TL``
        alone or the older two-hour shorthand) leaves the exact instant open,
        so queries inside the window are reported as uncertain.  A group with
        no time at all applies to the report's two-hour trend period.
        """
        frm = self._trend_time(spec.get("fm"))
        til = self._trend_time(spec.get("tl"), reference=frm)
        at = self._trend_time(spec.get("at"))
        period = spec.get("period")
        if period is not None:
            start_hour, end_hour = period
            start = self._trend_time((start_hour, 0))
            end = self._trend_time((end_hour, 0), reference=start)
            return start, end, None, False
        if frm is not None and til is not None:
            return frm, til, None, False
        if frm is not None:
            return frm, frm, frm, True
        if at is not None:
            return at, at, at, True
        if til is not None:
            # Only the end of the change window is known, so the change can
            # happen anywhere from now until then.
            return self.time, til, None, False
        return self.time, self._trend_period_end(), None, False

    def _build_trend_segments(self):
        """Decode ``_trend_sections`` into ``_trend_segments``."""
        self._trend_segments = TrendSegmentList()
        if self.time is None:
            return
        state = Conditions.from_metar(self)
        observation = TrendSegment(
            "observation",
            start=self.time,
            end=None,
            conditions=state.copy(),
            index=0,
            uncertain=False,
        )
        self._trend_segments.append(observation)
        if not self._trend_sections:
            return

        # The observation prevails until the first permanent change takes
        # effect.  Temporary overlays can occur anywhere in the trend period.
        first_becoming = None
        index = 1
        for section in self._trend_sections:
            indicator = section["indicator"]
            tokens = section["tokens"]
            spec = _parse_trend_time_tokens(tokens)
            changes, _ = _decode_trend_conditions(spec["remaining"], self)
            start, end, effective, definite = self._trend_window(spec)
            if indicator == "NOSIG":
                continue
            if indicator == "BECMG":
                before = state.copy()
                after = _apply_trend_changes(state, changes)
                state = after
                if first_becoming is None:
                    first_becoming = effective if effective is not None else end
                segment = TrendSegment(
                    "becoming",
                    start=start,
                    end=end,
                    conditions=after.copy(),
                    changes=changes,
                    before=before,
                    after=after.copy(),
                    effective=effective,
                    tokens=[indicator] + list(tokens),
                    index=index,
                    indicator=indicator,
                    definite=definite,
                )
            elif indicator in ("TEMPO", "FCST"):
                # "TEMPO FMhhmm" without a TL leaves the end of the temporary
                # period implicit.  The trend forecast period is at most two
                # hours, so keep the overlay visible that long rather than
                # letting it vanish at the instant it begins.
                if (
                    indicator == "TEMPO"
                    and spec.get("fm") is not None
                    and spec.get("tl") is None
                    and start is not None
                    and end == start
                ):
                    end = start + datetime.timedelta(hours=2)
                    definite = False
                base = state.copy()
                overlay = _apply_trend_changes(base, changes)
                segment = TrendSegment(
                    "temporary" if indicator == "TEMPO" else "forecast",
                    start=start,
                    end=end,
                    conditions=overlay,
                    changes=changes,
                    before=base,
                    after=None,
                    effective=effective,
                    tokens=[indicator] + list(tokens),
                    index=index,
                    indicator=indicator,
                    definite=definite,
                )
            else:
                segment = TrendSegment(
                    indicator.lower(),
                    start=start,
                    end=end,
                    conditions=state.copy(),
                    changes=changes,
                    tokens=[indicator] + list(tokens),
                    index=index,
                    indicator=indicator,
                    definite=definite,
                )
            self._trend_segments.append(segment)
            index += 1

        # The observation segment lasts until the first permanent change is
        # fully established (or the last trend window closes).
        observation_end = first_becoming
        if observation_end is None:
            ends = [segment.end for segment in self._trend_segments[1:] if segment.end]
            observation_end = max(ends) if ends else None
        if observation_end is not None:
            self._trend_segments[0].end = observation_end

    @property
    def observed_conditions(self):
        """The conditions observed by the report, without any trend applied."""
        return Conditions.from_metar(self)

    @property
    def trend_segments(self):
        """The report's trend timeline as a :class:`TrendSegmentList`.

        The first segment is the observation.  Each BECMG group adds a
        permanent change and each TEMPO group adds a temporary overlay; the
        trend text itself remains available through :meth:`trend`.
        """
        return self._trend_segments

    @property
    def trend_sections(self):
        """The trend groups split by indicator: ``[{"type": ..., "groups": ...}]``."""
        return [
            {
                "type": section["indicator"],
                "indicator": section["indicator"],
                "groups": list(section["tokens"]),
                "text": " ".join([section["indicator"]] + section["tokens"]),
            }
            for section in self._trend_sections
        ]

    def trend_view(self):
        """Return a :class:`TrendView` for this report."""
        return TrendView(self)

    @property
    def trend_timeline(self):
        """Alias for :attr:`trend_segments`."""
        return self._trend_segments

    @property
    def trend_periods(self):
        """Alias for :attr:`trend_segments`."""
        return self._trend_segments

    def segments(self):
        """Return the report's trend timeline (observations and forecasts)."""
        return self._trend_segments

    def forecast_at(self, when, include_temporary=True):
        """Alias for :meth:`conditions_at`."""
        return self.conditions_at(when, include_temporary=include_temporary)

    def conditions(self, when, include_temporary=True):
        """Alias for :meth:`conditions_at`."""
        return self.conditions_at(when, include_temporary=include_temporary)

    def conditions_at(self, when, include_temporary=True):
        """Return the conditions at ``when`` as a :class:`TrendSnapshot`.

        ``when`` is a datetime (or a ``(hour, minute)`` pair resolved against
        the report's date, or an ``"HHMM"`` string).  Permanent BECMG changes
        take effect at their effective time; temporary TEMPO overlays apply
        only inside their own window and expire afterwards, at which point the
        underlying permanent state that is in force at that time is restored.
        When the report gives only a change window without an exact effective
        instant, the queried time has ``uncertain`` set and ``possible`` lists
        the alternatives the report supports rather than silently choosing
        one.
        """
        time = self._resolve_query_time(when)
        if time is None or not self._trend_segments:
            raise ValueError("cannot resolve the query time for this report")

        underlying = [self._trend_segments[0].conditions.copy()]
        established = underlying[0].copy()
        uncertain = False
        active_segments = []

        for segment in self._trend_segments[1:]:
            if segment.kind == "becoming":
                if segment.is_established(time):
                    established = segment.conditions.copy()
                    underlying = [established.copy()]
                elif segment.is_pending(time):
                    # The change may or may not have happened by now.
                    uncertain = True
                    underlying = [segment.conditions.copy()] + [
                        state.copy() for state in underlying
                    ]
                    active_segments.append(segment)
            elif segment.kind in ("temporary", "forecast"):
                if segment.contains(time):
                    active_segments.append(segment)

        active_temporary = [
            segment
            for segment in active_segments
            if segment.kind in ("temporary", "forecast")
        ]
        # ``prevailing`` is the state that would prevail without any temporary
        # overlay: the last definitely established state.
        prevailing = established.copy()

        possible_states = []
        if include_temporary and active_temporary:
            uncertain = True
            for base in underlying:
                state = base.copy()
                for segment in active_temporary:
                    state = _apply_trend_changes(state, segment.changes)
                possible_states.append(state)
            # The temporary phenomenon is possible, not certain, so the
            # unmodified underlying states remain alternatives as well.
            possible_states.extend(state.copy() for state in underlying)
            possible_states = _unique_conditions(possible_states)
            conditions = possible_states[0]
        else:
            possible_states = _unique_conditions(
                [state.copy() for state in underlying]
            )
            conditions = possible_states[0]

        if not uncertain:
            possible_states = [conditions]

        return TrendSnapshot(
            time=time,
            conditions=conditions,
            uncertain=uncertain,
            possible=possible_states,
            prevailing=prevailing,
            temporary=active_temporary,
            segments=active_segments,
            status="uncertain" if uncertain else "certain",
        )

    def _resolve_query_time(self, when):
        """Resolve a query time argument against the report's date."""
        if self.time is None:
            return None
        if isinstance(when, datetime.datetime):
            if when.tzinfo is not None:
                when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            return when
        if isinstance(when, datetime.time):
            return self._resolve_clock(when.hour, when.minute)
        if isinstance(when, str):
            text = when.strip().upper()
            if text.endswith("Z"):
                text = text[:-1]
            for prefix in ("AT", "FM", "TL"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
            if len(text) == 4 and text.isdigit():
                return self._resolve_clock(int(text[:2]), int(text[2:]))
            raise ValueError("cannot interpret %r as a trend time" % (when,))
        if isinstance(when, (tuple, list)) and len(when) == 2:
            return self._resolve_clock(int(when[0]), int(when[1]))
        raise ValueError("cannot interpret %r as a trend time" % (when,))

    def _resolve_clock(self, hour, minute):
        """Place an ``(hour, minute)`` query on the report's timeline.

        Trend groups carry no date, so a clock time is matched against the
        report's windows first.  That keeps a query on either side of midnight
        on the same timeline as the window it belongs to.  When no window
        contains the time, the nearest instant after the observation wins.
        """
        base = self.time
        candidates = [
            base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            + datetime.timedelta(days=offset)
            for offset in (-1, 0, 1)
        ]
        for candidate in candidates:
            for segment in self._trend_segments[1:]:
                if segment.start is None:
                    continue
                if segment.end is None:
                    if candidate >= segment.start:
                        return candidate
                elif segment.start <= candidate <= segment.end:
                    return candidate
        forward = [c for c in candidates if c >= base]
        if forward:
            return min(forward)
        return max(candidates)

    def _handleType(self, d):
        """
        Parse the report-type group.

        The following attributes are set:
            type   [string]
        """
        self.type = d["type"]

    def _handleCorrection(self, d):
        """
        Parse the correction group.

        The following attributes are set:
            correction   [string]
        """
        self.correction = d["cor"]

    def _handleStation(self, d):
        """
        Parse the station id group.

        The following attributes are set:
            station_id   [string]
        """
        self.station_id = d["station"]

    def _handleModifier(self, d):
        """
        Parse the report-modifier group.

        The following attributes are set:
            mod   [string]
        """
        mod = d["mod"]
        if mod == "CORR":
            mod = "COR"
        if mod == "NIL" or mod == "FINO":
            mod = "NO DATA"
        self.mod = mod

    def _handleTime(self, d):
        """
        Parse the observation-time group.

        The following attributes are set:
            time   [datetime]
            cycle  [int]
            _day   [int]
            _hour  [int]
            _min   [int]
        """
        self._day = int(d["day"])
        month_was_given = bool(self._month)
        if not self._month:
            self._month = self._now.month
            if self._day > self._now.day:
                if self._month == 1:
                    self._month = 12
                else:
                    self._month = self._month - 1
        if not self._year:
            self._year = self._now.year
            if self._month > self._now.month:
                self._year = self._year - 1
            elif self._month == self._now.month and self._day > self._now.day:
                self._year = self._year - 1
        # A METAR carries only the day of the month, so the month above is a
        # guess: this month, or last month if the day is still ahead of us.
        # Last month may simply not have that day -- a 31st report read on
        # March 5th lands on February 31st -- so step back one more. One step
        # is always enough: every 30-day month and February is preceded by a
        # 31-day month. A caller who passed an explicit month is left alone,
        # because for them an impossible date is an error rather than a guess.
        if (
            not month_was_given
            and self._month > 1
            and self._day > calendar.monthrange(self._year, self._month)[1]
        ):
            self._month -= 1
        self._hour = int(d["hour"])
        self._min = int(d["min"])
        self.time = datetime.datetime(
            self._year, self._month, self._day, self._hour, self._min
        )
        if self._min < 45:
            self.cycle = self._hour
        else:
            self.cycle = self._hour + 1

    def _handleWind(self, d):
        """
        Parse the wind and variable-wind groups.

        The following attributes are set:
            wind_dir           [direction]
            wind_speed         [speed]
            wind_gust          [speed]
            wind_dir_from      [int]
            wind_dir_to        [int]
        """
        wind_dir = d["dir"].replace("O", "0")
        if wind_dir != "VRB" and wind_dir != "///" and wind_dir != "MMM":
            self.wind_dir = direction(wind_dir)
        wind_speed = d["speed"].replace("O", "0")
        units = d["units"]
        # Ambiguous METAR when no wind speed units are provided
        if units is None and self.station_id is not None:
            # Assume US METAR sites are reporting in KT
            if len(self.station_id) == 3 or self.station_id.startswith("K"):
                units = "KT"
        # If units are still None, default to MPS
        if units is None:
            units = "MPS"
        if units == "KTS" or units == "K" or units == "T" or units == "LT":
            units = "KT"
        if wind_speed.startswith("P"):
            self.wind_speed = speed(wind_speed[1:], units, ">")
        elif not MISSING_RE.match(wind_speed):
            self.wind_speed = speed(wind_speed, units)
        if d["gust"]:
            wind_gust = d["gust"]
            if wind_gust.startswith("P"):
                self.wind_gust = speed(wind_gust[1:], units, ">")
            elif not MISSING_RE.match(wind_gust):
                self.wind_gust = speed(wind_gust, units)
        if d["varfrom"]:
            self.wind_dir_from = direction(d["varfrom"])
            self.wind_dir_to = direction(d["varto"])

    def _handleVisibility(self, d):
        """
        Parse the minimum and maximum visibility groups.

        The following attributes are set:
            vis          [distance]
            vis_dir      [direction]
            max_vis      [distance]
            max_vis_dir  [direction]
        """
        vis = d["vis"]
        vis_less = None
        vis_dir = None
        vis_units = "M"
        vis_dist = "10000"
        if d["dist"] and d["dist"] != "////":
            vis_dist = d["dist"]
            if d["dir"] and d["dir"] != "NDV":
                vis_dir = d["dir"]
        elif d["distu"]:
            vis_dist = d["distu"]
            if d["units"] and d["units"] != "U":
                vis_units = d["units"]
        if vis_dist == "9999":
            vis_dist = "10000"
            vis_less = ">"
        if self.vis:
            if vis_dir:
                self.max_vis_dir = direction(vis_dir)
            self.max_vis = distance(vis_dist, vis_units, vis_less)
        else:
            if vis_dir:
                self.vis_dir = direction(vis_dir)
            self.vis = distance(vis_dist, vis_units, vis_less)

    def _handleRunway(self, d):
        """
        Parse a runway visual range group.

        The following attributes are set:
            range   [list of tuples]
            . name  [string]
            . low   [distance]
            . high  [distance]
            . unit  [string]
            runway_tendencies [list of U, D, N or None, aligned with runway]
        """
        if d["name"] is None:
            return
        unit = d["unit"] if d["unit"] is not None else "M"
        if d["low"] == "////":
            return
        else:
            low = distance(d["low"], unit)
        if d["high"] is None:
            high = low
        else:
            high = distance(d["high"], unit)
        self.runway.append([d["name"], low, high, unit])
        tendency = d["tendency"].strip("/")
        self.runway_tendencies.append(tendency if tendency in ("U", "D", "N") else None)

    def _handleWeather(self, d):
        """
        Parse a present-weather group.

        The following attributes are set:
            weather    [list of tuples]
            .  intensity     [string]
            .  description   [string]
            .  precipitation [string]
            .  obscuration   [string]
            .  other         [string]
        """
        inteni = d["int"]
        if not inteni and d["int2"]:
            inteni = d["int2"]
        desci = d["desc"]
        preci = d["prec"]
        obsci = d["obsc"]
        otheri = d["other"]
        self.weather.append((inteni, desci, preci, obsci, otheri))

    def _handleSky(self, d):
        """
        Parse a sky-conditions group.

        The following attributes are set:
            sky        [list of tuples]
            .  cover   [string]
            .  height  [distance]
            .  cloud   [string]
        """
        height = d["height"]
        if not height or height == "///":
            height = None
        else:
            height = height.replace("O", "0")
            height = distance(int(height) * 100, "FT")
        cover = d["cover"]
        if cover == "SCK" or cover == "SKC" or cover == "CL":
            cover = "CLR"
        if cover == "0VC":
            cover = "OVC"
        cloud = d["cloud"]
        if cloud == "///":
            cloud = ""
        self.sky.append((cover, height, cloud))

    def _handleTemp(self, d):
        """
        Parse a temperature-dewpoint group.

        The following attributes are set:
            temp    temperature (Celsius) [float]
            dewpt   dew point (Celsius) [float]
        """
        temp = d["temp"]
        dewpt = d["dewpt"]
        if temp and temp != "//" and temp != "XX" and temp != "MM":
            self.temp = temperature(temp)
        if dewpt and dewpt != "//" and dewpt != "XX" and dewpt != "MM":
            self.dewpt = temperature(dewpt)

    def _handlePressure(self, d):
        """
        Parse an altimeter-pressure group.

        The following attributes are set:
            press    [int]
        """
        press = d["press"]
        if press != "////":
            press = float(press.replace("O", "0"))
            if d["unit"]:
                if d["unit"] == "A" or (d["unit2"] and d["unit2"] == "INS"):
                    self.press = pressure(press / 100, "IN")
                elif d["unit"] == "SLP":
                    if press < 500:
                        press = press / 10 + 1000
                    else:
                        press = press / 10 + 900
                    self.press = pressure(press, "HPA")
                    self._remarks.append("sea-level pressure %.1fhPa" % press)
                else:
                    self.press = pressure(press, "HPA")
            elif press > 2500:
                self.press = pressure(press / 100, "IN")
            else:
                self.press = pressure(press, "HPA")

    def _handleRecent(self, d):
        """
        Parse a recent-weather group.

        The following attributes are set:
            weather    [list of tuples]
            .  intensity     [string]
            .  description   [string]
            .  precipitation [string]
            .  obscuration   [string]
            .  other         [string]
        """
        desci = d["desc"]
        preci = d["prec"]
        obsci = d["obsc"]
        otheri = d["other"]
        self.recent.append(("", desci, preci, obsci, otheri))

    def _handleWindShear(self, d):
        """
        Parse wind-shear groups.

        The following attributes are set:
            windshear    [list of strings]
        """
        if d["name"]:
            self.windshear.append(d["name"])
        else:
            self.windshear.append("ALL")

    def _handleColor(self, d):
        """
        Parse (and ignore) the color groups.

        The following attributes are set:
            trend    [list of strings]
        """
        pass

    def _handleRunwayState(self, d):
        """
        Parse (and ignore) the runway state.

        The following attributes are set:
        """
        pass

    def _handleTrend(self, d):
        """
        Parse (and ignore) the trend groups.

        Each indicator (BECMG, TEMPO, FCST, NOSIG) opens a section; the
        following tokens belong to it until the next indicator.
        """
        if "trend" in d:
            indicator = d["trend"].upper()
            self._trend_groups.append(d["trend"])
            self._trend_sections.append({"indicator": indicator, "tokens": []})
        self._trend = True

    def _startRemarks(self, d):
        """
        Found the start of the remarks section.
        """
        self._remarks = []

    def _handleSealvlPressRemark(self, d):
        """
        Parse the sea-level pressure remark group.
        """
        value = float(d["press"]) / 10.0
        if value < 50:
            value += 1000
        else:
            value += 900
        self.press_sea_level = pressure(value, "HPA")

    def _handlePrecip24hrRemark(self, d):
        """
        Parse a 3-, 6- or 24-hour cumulative preciptation remark group.
        """
        value = float(d["precip"]) / 100.0
        if d["type"] == "6":
            if self.cycle in [3, 9, 15, 21]:
                self.precip_3hr = precipitation(value, "IN")
            else:
                self.precip_6hr = precipitation(value, "IN")
        else:
            self.precip_24hr = precipitation(value, "IN")

    def _handlePrecip1hrRemark(self, d):
        """Parse an hourly precipitation remark group."""
        value = float(d["precip"]) / 100.0
        self.precip_1hr = precipitation(value, "IN")

    def _handleTemp1hrRemark(self, d):
        """
        Parse a temperature & dewpoint remark group.

        These values replace the temp and dewpt from the body of the report.
        """
        value = float(d["temp"]) / 10.0
        if d["tsign"] == "1":
            value = -value
        self.temp = temperature(value)
        if d["dewpt"]:
            value2 = float(d["dewpt"]) / 10.0
            if d["dsign"] == "1":
                value2 = -value2
            self.dewpt = temperature(value2)

    def _handleTemp6hrRemark(self, d):
        """
        Parse a 6-hour maximum or minimum temperature remark group.
        """
        value = float(d["temp"]) / 10.0
        if d["sign"] == "1":
            value = -value
        if d["type"] == "1":
            self.max_temp_6hr = temperature(value, "C")
        else:
            self.min_temp_6hr = temperature(value, "C")

    def _handleTemp24hrRemark(self, d):
        """
        Parse a 24-hour maximum/minimum temperature remark group.
        """
        value = float(d["maxt"]) / 10.0
        if d["smaxt"] == "1":
            value = -value
        value2 = float(d["mint"]) / 10.0
        if d["smint"] == "1":
            value2 = -value2
        self.max_temp_24hr = temperature(value, "C")
        self.min_temp_24hr = temperature(value2, "C")

    def _handlePress3hrRemark(self, d):
        """
        Parse a pressure-tendency remark group.
        """
        value = float(d["press"]) / 10.0
        descrip = PRESSURE_TENDENCY[d["tend"]]
        self._remarks.append("3-hr pressure change %.1fhPa, %s" % (value, descrip))

    def _handlePeakWindRemark(self, d):
        """
        Parse a peak wind remark group.
        """
        peak_dir = int(d["dir"])
        peak_speed = int(d["speed"])
        self.wind_speed_peak = speed(peak_speed, "KT")
        self.wind_dir_peak = direction(peak_dir)
        peak_min = int(d["min"])
        if d["hour"]:
            peak_hour = int(d["hour"])
        else:
            peak_hour = self._hour
        self.peak_wind_time = datetime.datetime(
            self._year, self._month, self._day, peak_hour, peak_min
        )
        if self.peak_wind_time > self.time:
            if peak_hour > self._hour:
                self.peak_wind_time -= datetime.timedelta(hours=24)
            else:
                self.peak_wind_time -= datetime.timedelta(hours=1)
        self._remarks.append(
            "peak wind %dkt from %d degrees at %d:%02d"
            % (peak_speed, peak_dir, peak_hour, peak_min)
        )

    def _handleWindShiftRemark(self, d):
        """
        Parse a wind shift remark group.
        """
        if d["hour"]:
            wshft_hour = int(d["hour"])
        else:
            wshft_hour = self._hour
        wshft_min = int(d["min"])
        self.wind_shift_time = datetime.datetime(
            self._year, self._month, self._day, wshft_hour, wshft_min
        )
        if self.wind_shift_time > self.time:
            if wshft_hour > self._hour:
                self.wind_shift_time -= datetime.timedelta(hours=24)
            else:
                self.wind_shift_time -= datetime.timedelta(hours=1)
        text = "wind shift at %d:%02d" % (wshft_hour, wshft_min)
        if d["front"]:
            text += " (front)"
        self._remarks.append(text)

    def _handleLightningRemark(self, d):
        """
        Parse a lightning observation remark group.
        """
        parts = []
        if d["freq"]:
            parts.append(LIGHTNING_FREQUENCY[d["freq"]])
        parts.append("lightning")
        if d["type"]:
            ltg_types = []
            group = d["type"]
            while group:
                ltg_types.append(LIGHTNING_TYPE[group[:2]])
                group = group[2:]
            parts.append("(" + ",".join(ltg_types) + ")")
        if d["loc"]:
            parts.append(xlate_loc(d["loc"]))
        self._remarks.append(" ".join(parts))

    def _handleTSLocRemark(self, d):
        """
        Parse a thunderstorm location remark group.
        """
        text = "thunderstorm"
        if d["loc"]:
            text += " " + xlate_loc(d["loc"])
        if d["dir"]:
            text += " moving %s" % d["dir"]
        self._remarks.append(text)

    def _handleAutoRemark(self, d):
        """
        Parse an automatic station remark group.
        """
        if d["type"] == "1":
            self._remarks.append("Automated station")
        elif d["type"] == "2":
            self._remarks.append("Automated station (type 2)")

    def _handleSnowDepthRemark(self, d):
        """
        Parse the 4/ group snowdepth report
        """
        self.snowdepth = distance(float(d["snowdepth"]), "IN")
        self._remarks.append(" snowdepth %s" % (self.snowdepth,))

    def _handleIceAccretionRemark(self, d):
        """
        Parse the I/ group ice accretion report.
        """
        myattr = "ice_accretion_%shr" % (d["ice_accretion_hours"],)
        value = precipitation(float(d["ice_accretion_depth"]) / 100.0, "IN")
        setattr(self, myattr, value)

    def _unparsedRemark(self, d):
        """
        Handle otherwise unparseable remark groups.
        """
        self._unparsed_remarks.append(d["group"])

    # the list of handler functions to use (in order) to process a METAR report

    handlers = [
        (TYPE_RE, _handleType, False),
        (COR_RE, _handleCorrection, False),
        (STATION_RE, _handleStation, False),
        (TIME_RE, _handleTime, False),
        (MODIFIER_RE, _handleModifier, False),
        (WIND_RE, _handleWind, False),
        (VISIBILITY_RE, _handleVisibility, True),
        (RUNWAY_RE, _handleRunway, True),
        (WEATHER_RE, _handleWeather, True),
        (SKY_RE, _handleSky, True),
        (WIND_RE, _handleWind, False),
        (VISIBILITY_RE, _handleVisibility, True),
        (TEMP_RE, _handleTemp, False),
        (PRESS_RE, _handlePressure, True),
        (SEALVL_PRESS_RE, _handleSealvlPressRemark, False),
        (RECENT_RE, _handleRecent, True),
        (WINDSHEAR_RE, _handleWindShear, True),
        (COLOR_RE, _handleColor, True),
        (RUNWAYSTATE_RE, _handleRunwayState, True),
        (TREND_RE, _handleTrend, True),
        (REMARK_RE, _startRemarks, False),
    ]

    trend_handlers = [
        (TRENDTIME_RE, _handleTrend, True),
        (WIND_RE, _handleTrend, True),
        (VISIBILITY_RE, _handleTrend, True),
        (WEATHER_RE, _handleTrend, True),
        (SKY_RE, _handleTrend, True),
        (COLOR_RE, _handleTrend, True),
    ]

    # the list of patterns for the various remark groups,
    # paired with the handler functions to use to record the decoded remark.

    remark_handlers = [
        (AUTO_RE, _handleAutoRemark),
        (SEALVL_PRESS_RE, _handleSealvlPressRemark),
        (PEAK_WIND_RE, _handlePeakWindRemark),
        (WIND_SHIFT_RE, _handleWindShiftRemark),
        (LIGHTNING_RE, _handleLightningRemark),
        (TS_LOC_RE, _handleTSLocRemark),
        (TEMP_1HR_RE, _handleTemp1hrRemark),
        (PRECIP_1HR_RE, _handlePrecip1hrRemark),
        (PRECIP_24HR_RE, _handlePrecip24hrRemark),
        (PRESS_3HR_RE, _handlePress3hrRemark),
        (TEMP_6HR_RE, _handleTemp6hrRemark),
        (TEMP_24HR_RE, _handleTemp24hrRemark),
        (SNOWDEPTH_RE, _handleSnowDepthRemark),
        (ICE_ACCRETION_RE, _handleIceAccretionRemark),
        (UNPARSED_RE, _unparsedRemark),
    ]

    # functions that return text representations of conditions for output

    def string(self):
        """
        Return a human-readable version of the decoded report.
        """
        lines = []
        lines.append("station: %s" % self.station_id)
        if self.type:
            lines.append("type: %s" % self.report_type())
        if self.time:
            lines.append("time: %s" % self.time.ctime())
        if self.temp:
            lines.append("temperature: %s" % self.temp.string("C"))
        if self.dewpt:
            lines.append("dew point: %s" % self.dewpt.string("C"))
        if self.wind_speed:
            lines.append("wind: %s" % self.wind())
        if self.wind_speed_peak:
            lines.append("peak wind: %s" % self.peak_wind())
        if self.wind_shift_time:
            lines.append("wind shift: %s" % self.wind_shift())
        if self.vis:
            lines.append("visibility: %s" % self.visibility())
        if self.runway:
            lines.append("visual range: %s" % self.runway_visual_range())
        if self.press:
            lines.append("pressure: %s" % self.press.string("hPa"))
        if self.weather:
            lines.append("weather: %s" % self.present_weather())
        if self.sky:
            lines.append("sky: %s" % self.sky_conditions("\n     "))
        if self.press_sea_level:
            lines.append("sea-level pressure: %s" % self.press_sea_level.string("hPa"))
        if self.max_temp_6hr:
            lines.append("6-hour max temp: %s" % str(self.max_temp_6hr))
        if self.max_temp_6hr:
            lines.append("6-hour min temp: %s" % str(self.min_temp_6hr))
        if self.max_temp_24hr:
            lines.append("24-hour max temp: %s" % str(self.max_temp_24hr))
        if self.max_temp_24hr:
            lines.append("24-hour min temp: %s" % str(self.min_temp_24hr))
        if self.precip_1hr:
            lines.append("1-hour precipitation: %s" % str(self.precip_1hr))
        if self.precip_3hr:
            lines.append("3-hour precipitation: %s" % str(self.precip_3hr))
        if self.precip_6hr:
            lines.append("6-hour precipitation: %s" % str(self.precip_6hr))
        if self.precip_24hr:
            lines.append("24-hour precipitation: %s" % str(self.precip_24hr))
        if self.ice_accretion_1hr:
            lines.append("1-hour Ice Accretion: %s" % str(self.ice_accretion_1hr))
        if self.ice_accretion_3hr:
            lines.append("3-hour Ice Accretion: %s" % str(self.ice_accretion_3hr))
        if self.ice_accretion_6hr:
            lines.append("6-hour Ice Accretion: %s" % str(self.ice_accretion_6hr))
        if self._remarks:
            lines.append("remarks:")
            lines.append("- " + self.remarks("\n- "))
        if self._unparsed_remarks:
            lines.append("- " + " ".join(self._unparsed_remarks))
        lines.append("METAR: " + self.code)
        return "\n".join(lines)

    def report_type(self):
        """
        Return a textual description of the report type.
        """
        if self.type is None:
            text = "unknown report type"
        elif self.type in REPORT_TYPE:
            text = REPORT_TYPE[self.type]
        else:
            text = self.type + " report"
        if self.cycle:
            text += ", cycle %d" % self.cycle
        if self.mod:
            if self.mod in REPORT_TYPE:
                text += " (%s)" % REPORT_TYPE[self.mod]
            else:
                text += " (%s)" % self.mod
        if self.correction:
            text += " (%s)" % REPORT_TYPE[self.correction]
        return text

    def wind(self, units="KT"):
        """
        Return a textual description of the wind conditions.

        Units may be specified as "MPS", "KT", "KMH", or "MPH".
        """
        if self.wind_speed is None:
            return "missing"
        elif self.wind_speed.value() == 0.0:
            text = "calm"
        else:
            wind_speed = self.wind_speed.string(units)
            if not self.wind_dir:
                text = "variable at %s" % wind_speed
            elif self.wind_dir_from:
                text = "%s to %s at %s" % (
                    self.wind_dir_from.compass(),
                    self.wind_dir_to.compass(),
                    wind_speed,
                )
            else:
                text = "%s at %s" % (self.wind_dir.compass(), wind_speed)
            if self.wind_gust:
                text += ", gusting to %s" % self.wind_gust.string(units)
        return text

    def peak_wind(self, units="KT"):
        """
        Return a textual description of the peak wind conditions.

        Units may be specified as "MPS", "KT", "KMH", or "MPH".
        """
        if self.wind_speed_peak is None:
            return "missing"
        elif self.wind_speed_peak.value() == 0.0:
            text = "calm"
        else:
            wind_speed = self.wind_speed_peak.string(units)
            if not self.wind_dir_peak:
                text = wind_speed
            else:
                text = "%s at %s" % (self.wind_dir_peak.compass(), wind_speed)
                if self.peak_wind_time is not None:
                    text += " at %s" % self.peak_wind_time.strftime("%H:%M")
        return text

    def wind_shift(self, units="KT"):
        """
        Return a textual description of the wind shift time

        Units may be specified as "MPS", "KT", "KMH", or "MPH".
        """
        if self.wind_shift_time is None:
            return "missing"
        else:
            return self.wind_shift_time.strftime("%H:%M")

    def visibility(self, units=None):
        """
        Return a textual description of the visibility.

        Units may be statute miles ("SM") or meters ("M").
        """
        if self.vis is None:
            return "missing"
        if self.vis_dir:
            text = "%s to %s" % (self.vis.string(units), self.vis_dir.compass())
        else:
            text = self.vis.string(units)
        if self.max_vis:
            if self.max_vis_dir:
                text += "; %s to %s" % (
                    self.max_vis.string(units),
                    self.max_vis_dir.compass(),
                )
            else:
                text += "; %s" % self.max_vis.string(units)
        return text

    def runway_visual_range(self, units=None):
        """
        Return a textual description of the runway visual range.
        """
        lines = []
        tendency_text = {"U": "increasing", "D": "decreasing", "N": "no distinct tendency"}
        for index, (name, low, high, unit) in enumerate(self.runway):
            reportunits = unit if units is None else units
            if low != high:
                lines.append(
                    ("on runway %s, from %d to %s")
                    % (name, low.value(reportunits), high.string(reportunits))
                )
            else:
                lines.append("on runway %s, %s" % (name, low.string(reportunits)))
            if index < len(self.runway_tendencies):
                tendency = self.runway_tendencies[index]
                if tendency is not None:
                    lines[-1] += ", " + tendency_text[tendency]
        return "; ".join(lines)

    def present_weather(self):
        """
        Return a textual description of the present weather.
        """
        return self._weather(self.weather)

    def recent_weather(self):
        """
        Return a textual description of the recent weather.
        """
        return self._weather(self.recent)

    def _weather(self, weather):
        """
        Return a textual description of weather.
        """
        text_list = []
        for weatheri in weather:
            (inteni, desci, preci, obsci, otheri) = weatheri
            text_parts = []
            code_parts = []
            if inteni:
                code_parts.append(inteni)
                text_parts.append(WEATHER_INT[inteni])
            if desci:
                code_parts.append(desci)
                if desci != "SH" or not preci:
                    text_parts.append(WEATHER_DESC[desci[0:2]])
                    if len(desci) == 4:
                        text_parts.append(WEATHER_DESC[desci[2:]])
            if preci:
                code_parts.append(preci)
                if len(preci) == 2:
                    precip_text = WEATHER_PREC[preci]
                elif len(preci) == 4:
                    precip_text = WEATHER_PREC[preci[:2]] + " and "
                    precip_text += WEATHER_PREC[preci[2:]]
                elif len(preci) == 6:
                    precip_text = WEATHER_PREC[preci[:2]] + ", "
                    precip_text += WEATHER_PREC[preci[2:4]] + " and "
                    precip_text += WEATHER_PREC[preci[4:]]
                else:
                    precip_text = preci
                if desci == "TS":
                    text_parts.append("with")
                text_parts.append(precip_text)
                if desci == "SH":
                    text_parts.append(WEATHER_DESC[desci])
            if obsci:
                code_parts.append(obsci)
                text_parts.append(WEATHER_OBSC[obsci])

            if otheri:
                code_parts.append(otheri)
                text_parts.append(WEATHER_OTHER[otheri])
            code = " ".join(code_parts)
            if code in WEATHER_SPECIAL:
                text_list.append(WEATHER_SPECIAL[code])
            else:
                text_list.append(" ".join(text_parts))
        return "; ".join(text_list)

    def sky_conditions(self, sep="; "):
        """
        Return a textual description of the sky conditions.
        """
        text_list = []
        for skyi in self.sky:
            (cover, height, cloud) = skyi
            if cover in ["SKC", "CLR", "NSC"]:
                text_list.append(SKY_COVER[cover])
            else:
                if cloud:
                    what = CLOUD_TYPE.get(cloud, "unknown CLOUD_TYPE of %s" % (cloud,))
                elif SKY_COVER[cover].endswith(" "):
                    what = "clouds"
                else:
                    what = ""
                label = "%s %s" % (SKY_COVER[cover], what)
                # HACK here to account for 'empty' entries with above format
                label = " ".join(label.strip().split())
                if cover == "VV":
                    label += ", vertical visibility to %s" % (str(height),)
                else:
                    label += " at %s" % (str(height),)
                text_list.append(label)
        return sep.join(text_list)

    def trend(self):
        """
        Return the trend forecast groups
        """
        return " ".join(self._trend_groups)

    def remarks(self, sep="; "):
        """
        Return the decoded remarks.
        """
        return sep.join(self._remarks)
