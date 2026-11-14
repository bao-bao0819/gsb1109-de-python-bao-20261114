"""
Server-side handling of late-arriving ("make-up") trajectory suffixes.

Vehicles that reconnect after a gap upload only the missing tail of a trip,
encoded as a differential suffix relative to a point the server has already
archived. Each make-up package declares the identity of the prefix it
continues, the sequence number of its anchor point and the encoding
precision; the suffix is only appended when all three match the archive.
Packages that arrive before their prerequisite are staged and take effect
once the prerequisite shows up, replays of an identical package only add a
receipt record, and conflicting suffixes for the same prerequisite are kept
as branches for manual review.
"""
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .polyline import _py2_round, decode, encode

_CHAIN_VERSION = 'polyline-prefix-v1'


def _quantize(value: float, precision: int) -> int:
    return _py2_round(value * (10 ** precision))


def _genesis_id(trip_id: str, precision: int) -> str:
    token = f'{_CHAIN_VERSION}|{trip_id}|{precision}'
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _extend_id(prefix_id: str, lat: float, lon: float, precision: int) -> str:
    token = f'{prefix_id}|{_quantize(lat, precision)}|{_quantize(lon, precision)}'
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def prefix_identity(trip_id: str, points: List[Tuple[float, float]], precision: int = 5) -> str:
    """
    Compute the identity of a trajectory prefix for a trip.

    The identity is a hash chain over every quantized point, so two revisions
    that happen to end at the same coordinate still have different identities
    whenever their histories differ.

    :param trip_id: Identifier of the trip the points belong to.
    :param points: Ordered (lat, lon) points of the prefix.
    :param precision: Precision of the coordinates. The default value is 5.
    :return: Hex digest identifying exactly this prefix.
    """
    return extend_identity(_genesis_id(trip_id, precision), points, precision)


def extend_identity(identity: str, points: List[Tuple[float, float]], precision: int = 5) -> str:
    """
    Extend a prefix identity with additional points.

    :param identity: Identity of an existing prefix, e.g. from :func:`prefix_identity`.
    :param points: Ordered (lat, lon) points appended to that prefix.
    :param precision: Precision of the coordinates. The default value is 5.
    :return: Identity of the extended prefix.
    """
    for lat, lon in points:
        identity = _extend_id(identity, lat, lon, precision)
    return identity


def encode_suffix(anchor: Tuple[float, float], points: List[Tuple[float, float]],
                  precision: int = 5, geojson: bool = False) -> str:
    """
    Encode points as a differential suffix relative to an anchor point.

    The result is the tail of the encoding of ``[anchor] + points``, i.e. the
    first encoded point is the delta from the anchor rather than from the
    origin.

    >>> encode_suffix((38.5, -120.2), [(40.7, -120.95), (43.252, -126.453)])
    '_ulLnnqC_mqNvxq`@'

    :param anchor: Last point of the existing prefix, e.g. (38.5, -120.2).
    :param points: New points to encode relative to the anchor.
    :param precision: Precision of the coordinates to encode. The default value is 5.
    :param geojson: Set to True in order to encode (lon, lat) tuples.
    :return: The encoded differential suffix string.
    """
    if len(points) == 0:
        return ''

    if geojson is True:
        anchor = (anchor[1], anchor[0])
        points = [t[::-1] for t in points]

    full = encode([anchor] + list(points), precision)
    head = encode([anchor], precision)

    return full[len(head):]


def decode_suffix(anchor: Tuple[float, float], expression: str,
                  precision: int = 5, geojson: bool = False) -> List[Tuple[float, float]]:
    """
    Decode a differential suffix relative to an anchor point.

    >>> decode_suffix((38.5, -120.2), '_ulLnnqC_mqNvxq`@')
    [(40.7, -120.95), (43.252, -126.453)]

    :param anchor: Last point of the existing prefix the suffix continues.
    :param expression: Encoded differential suffix, e.g. from :func:`encode_suffix`.
    :param precision: Precision of the encoded coordinates. The default value is 5.
    :param geojson: Set output of tuples to (lon, lat).
    :return: List of absolute coordinate tuples.
    """
    if geojson is True:
        anchor = (anchor[1], anchor[0])

    deltas = decode(expression, precision)
    points = [(round(anchor[0] + lat, precision), round(anchor[1] + lon, precision))
              for lat, lon in deltas]

    if geojson is True:
        points = [t[::-1] for t in points]

    return points


class ReceiveStatus(str, Enum):
    """Outcome of feeding a make-up package to a :class:`TripArchive`."""
    APPLIED = 'applied'
    STAGED = 'staged'
    DUPLICATE = 'duplicate'
    BRANCHED = 'branched'
    REJECTED = 'rejected'


@dataclass(frozen=True)
class MakeupPackage:
    """
    A late-arriving make-up package carrying a differential suffix.

    :param trip_id: Trip the package belongs to.
    :param prefix_id: Identity of the prefix the suffix continues, as computed
        by the sender with :func:`prefix_identity`/:func:`extend_identity`.
    :param start_seq: Sequence number of the anchor point (0-based index of
        the last prefix point within the trip).
    :param suffix: Encoded differential suffix, see :func:`encode_suffix`.
    :param precision: Precision the suffix is encoded with.
    :param package_id: Optional unique package identifier used to detect replays.
    """
    trip_id: str
    prefix_id: str
    start_seq: int
    suffix: str
    precision: int = 5
    package_id: Optional[str] = None


@dataclass
class Receipt:
    """Record of a single package reception, including replays."""
    number: int
    package_id: Optional[str]
    status: str


@dataclass
class Branch:
    """A suffix kept aside for review because its prerequisite was already continued."""
    prefix_id: str
    start_seq: int
    precision: int
    suffix: str
    package_id: Optional[str]
    reason: str
    decoded: List[Tuple[float, float]]


@dataclass
class Rejection:
    """A package that was refused because it does not match the archive."""
    package: MakeupPackage
    reason: str


@dataclass
class ReceiveResult:
    """Result of :meth:`TripArchive.receive`."""
    status: ReceiveStatus
    receipt: int
    applied: int
    detail: str = ''


class TripArchive:
    """
    Archive of the committed trajectory of a single trip.

    Tracks the prefix identity chain of the archived points and decides
    whether an incoming :class:`MakeupPackage` is appended, staged, kept as a
    branch or rejected.
    """

    def __init__(self, trip_id: str, precision: int = 5):
        self.trip_id = trip_id
        self.precision = precision
        self._points: List[Tuple[float, float]] = []
        self._prefix_ids: List[str] = []
        self._pending: Dict[Tuple[str, int], List[MakeupPackage]] = {}
        self._branches: List[Branch] = []
        self._receipts: List[Receipt] = []
        self._rejections: List[Rejection] = []
        self._committed: Dict[Tuple[str, int], str] = {}
        self._known_content = set()
        self._seen_packages = set()

    @property
    def points(self) -> List[Tuple[float, float]]:
        """Committed trajectory points, in sequence order."""
        return list(self._points)

    @property
    def head_seq(self) -> int:
        """Sequence number of the last committed point, or -1 when empty."""
        return len(self._points) - 1

    @property
    def head_prefix_id(self) -> Optional[str]:
        """Identity of the committed prefix up to the head, or None when empty."""
        return self._prefix_ids[-1] if self._prefix_ids else None

    @property
    def pending(self) -> Dict[Tuple[str, int], List[MakeupPackage]]:
        """Staged packages keyed by (prefix identity, start sequence)."""
        return {key: list(packages) for key, packages in self._pending.items()}

    @property
    def branches(self) -> List[Branch]:
        """Conflicting suffixes kept for manual review."""
        return list(self._branches)

    @property
    def receipts(self) -> List[Receipt]:
        """One record per received package, replays included."""
        return list(self._receipts)

    @property
    def rejections(self) -> List[Rejection]:
        """Packages refused for not matching the archive."""
        return list(self._rejections)

    def load_base(self, points: List[Tuple[float, float]]) -> None:
        """
        Load the initially archived revision of the trip.

        Staged packages whose prerequisite is satisfied by this base take
        effect immediately.

        :param points: Ordered (lat, lon) points of the archived revision.
        :raises ValueError: If the archive already holds committed points.
        """
        if self._points:
            raise ValueError('archive already holds a base revision')

        self._points = [tuple(point) for point in points]
        identity = _genesis_id(self.trip_id, self.precision)
        self._prefix_ids = []
        for lat, lon in self._points:
            identity = _extend_id(identity, lat, lon, self.precision)
            self._prefix_ids.append(identity)

        self._flush_pending()

    def receive(self, package: MakeupPackage) -> ReceiveResult:
        """
        Feed a make-up package into the archive.

        Every call adds a receipt. The package is appended only when its
        declared prefix identity, start sequence and precision match the
        archive; otherwise it is staged until its prerequisite arrives, kept
        as a branch when the prerequisite was already continued differently,
        or rejected when it contradicts the archived revision.
        """
        before = len(self._points)
        receipt = Receipt(len(self._receipts) + 1, package.package_id, '')
        self._receipts.append(receipt)

        def done(status: ReceiveStatus, detail: str = '') -> ReceiveResult:
            receipt.status = status.value
            return ReceiveResult(status, receipt.number, len(self._points) - before, detail)

        content_key = (package.prefix_id, package.start_seq, package.precision, package.suffix)
        if package.package_id is not None and package.package_id in self._seen_packages:
            return done(ReceiveStatus.DUPLICATE, 'package already received')
        if content_key in self._known_content:
            self._mark_seen(package)
            return done(ReceiveStatus.DUPLICATE, 'identical suffix already recorded')

        self._mark_seen(package)

        if package.trip_id != self.trip_id:
            return self._reject(package, done, 'package belongs to a different trip')
        if package.precision != self.precision:
            return self._reject(package, done, 'declared precision does not match archive')
        if package.start_seq < 0:
            return self._reject(package, done, 'negative start sequence')

        self._known_content.add(content_key)
        head_seq = len(self._points) - 1

        if package.start_seq <= head_seq:
            if self._prefix_ids[package.start_seq] != package.prefix_id:
                return self._reject(package, done,
                                    'prefix identity does not match archived revision')
            key = (package.prefix_id, package.start_seq)
            committed = self._committed.get(key)
            if committed is None and package.start_seq == head_seq:
                self._apply_one(package)
                self._flush_pending()
                return done(ReceiveStatus.APPLIED, 'suffix appended')
            self._add_branch(package, 'prerequisite already continued by another suffix')
            return done(ReceiveStatus.BRANCHED, 'kept as branch for review')

        self._pending.setdefault((package.prefix_id, package.start_seq), []).append(package)
        return done(ReceiveStatus.STAGED, 'prerequisite not yet archived')

    def _mark_seen(self, package: MakeupPackage) -> None:
        if package.package_id is not None:
            self._seen_packages.add(package.package_id)

    def _reject(self, package: MakeupPackage, done, reason: str) -> ReceiveResult:
        self._rejections.append(Rejection(package, reason))
        return done(ReceiveStatus.REJECTED, reason)

    def _apply_one(self, package: MakeupPackage) -> None:
        anchor = self._points[package.start_seq]
        new_points = decode_suffix(anchor, package.suffix, package.precision)
        self._committed[(package.prefix_id, package.start_seq)] = package.suffix
        for lat, lon in new_points:
            self._points.append((lat, lon))
            self._prefix_ids.append(_extend_id(self._prefix_ids[-1], lat, lon, package.precision))

    def _add_branch(self, package: MakeupPackage, reason: str) -> None:
        anchor = self._points[package.start_seq] if package.start_seq < len(self._points) else None
        decoded = decode_suffix(anchor, package.suffix, package.precision) if anchor else []
        self._branches.append(Branch(package.prefix_id, package.start_seq, package.precision,
                                     package.suffix, package.package_id, reason, decoded))

    def _flush_pending(self) -> None:
        while self._points:
            key = (self._prefix_ids[-1], len(self._points) - 1)
            packages = self._pending.pop(key, None)
            if not packages:
                break
            for alternative in packages[1:]:
                self._add_branch(alternative, 'conflicting suffix for the same prerequisite')
            self._apply_one(packages[0])

        if not self._points:
            return
        for key in list(self._pending):
            prefix_id, start_seq = key
            if start_seq < len(self._points):
                packages = self._pending.pop(key)
                for package in packages:
                    if self._prefix_ids[start_seq] == prefix_id:
                        self._add_branch(package, 'archive already continued past anchor')
                    else:
                        reason = 'prefix identity does not match archived revision'
                        self._rejections.append(Rejection(package, reason))
