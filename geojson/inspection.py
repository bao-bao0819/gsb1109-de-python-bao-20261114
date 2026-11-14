"""Matching of inspection observations against an out-and-back route.

The inspection car traverses the whole route on the outbound pass and then
returns along the same geometry on the inbound pass, so every interior point
of the route is visited twice.  When inspection records are imported, each
ordered observation (a position fix plus a cumulative odometer reading) is
matched to plausible route positions.  Two independent tolerances are used:

* ``position_tolerance`` -- maximum accepted distance between the fix and the
  route, in metres;
* ``mileage_tolerance`` -- maximum accepted difference between the recorded
  cumulative mileage and the mileage implied by a pass and route position,
  also in metres.

A single observation can have two candidate positions (one per pass).  When
the following observations leave only one feasible sequence, the observation
is attributed to that single pass.  If the complete record still cannot
distinguish the two passes, both position sets are retained and report
builders count the observation once instead of treating it as two
inspections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

_EARTH_RADIUS_METERS = 6_371_008.8
_STATION_EPSILON = 1e-9

OUTBOUND = "outbound"
INBOUND = "inbound"


def haversine(point_a, point_b):
    """Great-circle distance between two ``(longitude, latitude)`` points."""
    lon_a, lat_a = (math.radians(value) for value in point_a[:2])
    lon_b, lat_b = (math.radians(value) for value in point_b[:2])
    d_lon = lon_b - lon_a
    d_lat = lat_b - lat_a
    arc = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(d_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(arc)))


def _coerce_coordinates(route):
    """Extract ``(longitude, latitude)`` points from a route-like object."""
    if hasattr(route, "get") and "coordinates" in route:
        coordinates = route["coordinates"]
        if (
            coordinates
            and isinstance(coordinates[0], (list, tuple))
            and coordinates[0]
            and isinstance(coordinates[0][0], (list, tuple))
        ):
            # MultiLineString: join the individual lines into one polyline.
            coordinates = [point for line in coordinates for point in line]
    elif isinstance(route, Sequence) and route and isinstance(
        route[0], (list, tuple)
    ):
        coordinates = route
    else:
        raise ValueError("route must be a LineString-like sequence of positions")
    points = [tuple(point[:2]) for point in coordinates]
    if len(points) < 2:
        raise ValueError("a route needs at least two positions")
    return points


@dataclass(frozen=True)
class Projection:
    """Result of snapping a fix onto the route geometry."""

    station: float          # cumulative distance along the route, in metres
    offset: float           # distance from the fix to the route, in metres


class InspectionRoute:
    """A linear route with cumulative arc-length stations.

    Stations start at zero on the first vertex and grow along the vertex
    order.  :attr:`length` is the full route length; the inbound pass covers
    stations from ``length`` back to zero.
    """

    def __init__(self, coordinates):
        self.points = _coerce_coordinates(coordinates)
        stations = [0.0]
        segments = []
        total = 0.0
        for start, end in zip(self.points, self.points[1:]):
            segment_length = haversine(start, end)
            segments.append((start, end, segment_length))
            total += segment_length
            stations.append(total)
        self._segments = tuple(segments)
        self.stations = tuple(stations)
        self.length = total

    def project(self, point):
        """Snap a fix to the closest route point."""
        best = None
        for index, (start, end, segment_length) in enumerate(self._segments):
            along, offset = self._project_on_segment(
                point, start, end, segment_length
            )
            projection = Projection(self.stations[index] + along, offset)
            if best is None or projection.offset < best.offset:
                best = projection
        return best

    @staticmethod
    def _project_on_segment(point, start, end, segment_length):
        if segment_length == 0.0:
            return 0.0, haversine(point, start)
        lat0 = math.radians(start[1])
        scale = _EARTH_RADIUS_METERS
        point_x = (
            (math.radians(point[0]) - math.radians(start[0]))
            * math.cos(lat0) * scale
        )
        point_y = (math.radians(point[1]) - math.radians(start[1])) * scale
        vector_x = (
            (math.radians(end[0]) - math.radians(start[0]))
            * math.cos(lat0) * scale
        )
        vector_y = (math.radians(end[1]) - math.radians(start[1])) * scale
        ratio = (point_x * vector_x + point_y * vector_y) / (
            vector_x * vector_x + vector_y * vector_y
        )
        ratio = min(1.0, max(0.0, ratio))
        offset = math.hypot(
            point_x - ratio * vector_x, point_y - ratio * vector_y
        )
        return ratio * segment_length, offset


@dataclass(frozen=True)
class Observation:
    """One ordered inspection record.

    :param identifier: unique identifier of the observation.
    :param point: position fix as ``(longitude, latitude)``.
    :param mileage: cumulative odometer reading at the fix, in metres.
    """

    identifier: object
    point: tuple
    mileage: float

    def __post_init__(self):
        object.__setattr__(self, "point", tuple(self.point[:2]))
        object.__setattr__(self, "mileage", float(self.mileage))


@dataclass(frozen=True)
class Candidate:
    """A feasible route position for one observation on one pass."""

    pass_name: str
    station: float                 # position on the shared route geometry
    implied_mileage: float         # cumulative mileage implied by the pass
    position_residual: float       # fix-to-route distance, in metres
    mileage_residual: float        # recorded mileage minus implied mileage


@dataclass(frozen=True)
class MatchedObservation:
    """An observation with the candidates left after sequence filtering."""

    identifier: object
    point: tuple
    mileage: float
    candidates: tuple

    @property
    def is_ambiguous(self):
        return len(self.candidates) > 1

    @property
    def pass_names(self):
        return tuple(candidate.pass_name for candidate in self.candidates)


@dataclass(frozen=True)
class ReportRow:
    """One report line per observation, never one line per candidate."""

    identifier: object
    longitude: float
    latitude: float
    recorded_mileage: float
    ambiguous: bool
    candidate_count: int
    candidate_pass_names: tuple
    pass_name: Optional[str]
    station: Optional[float]
    implied_mileage: Optional[float]
    position_residual: float
    mileage_residual: Optional[float]


@dataclass(frozen=True)
class MatchResult:
    """Outcome of matching an ordered inspection record."""

    observations: tuple
    feasible_pass_sets: tuple

    @property
    def is_ambiguous(self):
        return len(self.feasible_pass_sets) > 1

    def candidates_for(self, pass_name):
        """Candidate positions attributed to one pass, in observation order."""
        return tuple(
            next(
                candidate
                for candidate in observation.candidates
                if candidate.pass_name == pass_name
            )
            for observation in self.observations
            if any(candidate.pass_name == pass_name
                   for candidate in observation.candidates)
        )

    def report_rows(self):
        """Build one :class:`ReportRow` per observation.

        Ambiguous observations keep a single row (with both candidate pass
        names) so the same observation is never counted as two inspections.
        """
        rows = []
        for observation in self.observations:
            first = observation.candidates[0]
            if observation.is_ambiguous:
                row = ReportRow(
                    identifier=observation.identifier,
                    longitude=observation.point[0],
                    latitude=observation.point[1],
                    recorded_mileage=observation.mileage,
                    ambiguous=True,
                    candidate_count=len(observation.candidates),
                    candidate_pass_names=observation.pass_names,
                    pass_name=None,
                    station=None,
                    implied_mileage=None,
                    position_residual=first.position_residual,
                    mileage_residual=None,
                )
            else:
                row = ReportRow(
                    identifier=observation.identifier,
                    longitude=observation.point[0],
                    latitude=observation.point[1],
                    recorded_mileage=observation.mileage,
                    ambiguous=False,
                    candidate_count=1,
                    candidate_pass_names=(first.pass_name,),
                    pass_name=first.pass_name,
                    station=first.station,
                    implied_mileage=first.implied_mileage,
                    position_residual=first.position_residual,
                    mileage_residual=first.mileage_residual,
                )
            rows.append(row)
        return tuple(rows)


class UnmatchedObservationError(ValueError):
    """Raised when no pass can host an observation within the tolerances."""

    def __init__(self, observation, position_residual, mileage_residuals):
        self.identifier = observation.identifier
        self.position_residual = position_residual
        self.mileage_residuals = dict(mileage_residuals)
        super().__init__(
            f"observation {observation.identifier!r} has no candidate within "
            f"tolerance (route offset {position_residual:.3f} m, mileage "
            f"residuals {self.mileage_residuals})"
        )


@dataclass(frozen=True)
class _Pass:
    name: str
    direction: int  # +1 for outbound stations 0 -> length, -1 for the return

    def implied_mileage(self, station, route_length):
        if self.direction > 0:
            return station
        return 2.0 * route_length - station

    def is_monotonic(self, previous_station, station):
        if self.direction > 0:
            return station + _STATION_EPSILON >= previous_station
        return station <= previous_station + _STATION_EPSILON


class InspectionMatcher:
    """Match ordered inspection observations to an out-and-back route."""

    def __init__(self, route, position_tolerance, mileage_tolerance):
        if position_tolerance < 0 or mileage_tolerance < 0:
            raise ValueError("tolerances must be non-negative")
        self.route = (
            route if isinstance(route, InspectionRoute)
            else InspectionRoute(route)
        )
        self.position_tolerance = float(position_tolerance)
        self.mileage_tolerance = float(mileage_tolerance)
        self._passes = (_Pass(OUTBOUND, 1), _Pass(INBOUND, -1))

    def match(self, observations):
        """Match an ordered sequence of observations.

        :param observations: :class:`Observation` objects or
            ``(identifier, point, mileage)`` tuples in acquisition order.
        :return: a :class:`MatchResult` holding one or two feasible passes.
        :raises UnmatchedObservationError: if an observation fits neither
            pass inside the configured tolerances.
        """
        records = [self._coerce_observation(raw) for raw in observations]
        if not records:
            return MatchResult((), ())
        candidates_per_observation = [
            self._candidates_for(observation) for observation in records
        ]
        sequences = self._feasible_sequences(candidates_per_observation)
        if not sequences:
            raise ValueError("observations cannot form a monotonic pass sequence")
        feasible_pass_sets = tuple(sorted({
            tuple(candidate.pass_name for candidate in sequence)
            for sequence in sequences
        }))
        matched = []
        for index, (observation, candidates) in enumerate(
            zip(records, candidates_per_observation)
        ):
            supported = {sequence[index].pass_name for sequence in sequences}
            kept = tuple(
                candidate for candidate in candidates
                if candidate.pass_name in supported
            )
            matched.append(MatchedObservation(
                identifier=observation.identifier,
                point=observation.point,
                mileage=observation.mileage,
                candidates=kept,
            ))
        return MatchResult(tuple(matched), feasible_pass_sets)

    def _candidates_for(self, observation):
        projection = self.route.project(observation.point)
        if projection.offset > self.position_tolerance + _STATION_EPSILON:
            raise UnmatchedObservationError(
                observation, projection.offset,
                {pass_.name: float("nan") for pass_ in self._passes},
            )
        candidates = []
        residuals = {}
        for pass_ in self._passes:
            implied = pass_.implied_mileage(
                projection.station, self.route.length
            )
            residual = observation.mileage - implied
            residuals[pass_.name] = residual
            if abs(residual) <= self.mileage_tolerance + _STATION_EPSILON:
                candidates.append(Candidate(
                    pass_name=pass_.name,
                    station=projection.station,
                    implied_mileage=implied,
                    position_residual=projection.offset,
                    mileage_residual=residual,
                ))
        if not candidates:
            raise UnmatchedObservationError(
                observation, projection.offset, residuals
            )
        return tuple(candidates)

    def _feasible_sequences(self, candidates_per_observation):
        """Keep candidate chains that follow one physical round trip.

        A chain stays on a single monotonic pass, or switches once from the
        outbound pass to the inbound pass at the turnaround point.  The
        reverse switch never happens during one round trip.  Implied mileage
        must never decrease, mirroring the cumulative odometer readings.
        """
        states = [(  # (candidate chain, already switched to inbound?)
            (), False,
        )]
        for candidates in candidates_per_observation:
            updated = []
            for sequence, switched in states:
                for candidate in candidates:
                    extended = self._extends(sequence, switched, candidate)
                    if extended is not None:
                        updated.append(
                            (sequence + (candidate,), extended)
                        )
            states = updated
            if not states:
                return ()
        return tuple(sequence for sequence, _switched in states)

    @staticmethod
    def _extends(sequence, switched, candidate):
        if not sequence:
            return switched
        previous = sequence[-1]
        if candidate.implied_mileage + _STATION_EPSILON < (
            previous.implied_mileage
        ):
            return None
        if candidate.pass_name == previous.pass_name:
            direction = 1 if candidate.pass_name == OUTBOUND else -1
            if _Pass(candidate.pass_name, direction).is_monotonic(
                previous.station, candidate.station
            ):
                return switched
            return None
        if switched or previous.pass_name != OUTBOUND:
            return None
        # The only physical transition is outbound -> inbound; odometer
        # monotonicity already guards the turnaround ordering.
        return True

    @staticmethod
    def _coerce_observation(raw):
        if isinstance(raw, Observation):
            return raw
        identifier, point, mileage = raw
        return Observation(identifier, point, mileage)


def match_inspection_records(
    route, observations, position_tolerance, mileage_tolerance
):
    """Convenience wrapper around :class:`InspectionMatcher`."""
    return InspectionMatcher(
        route, position_tolerance, mileage_tolerance
    ).match(observations)
