"""GeoJSON reading and writing around :mod:`riverflow.model`.

Two layer conventions are accepted:

* two FeatureCollections, one of station features and one of reach features;
* one FeatureCollection mixing both, split by geometry type
  (``Point``-like features are stations, line features are reaches).

Property names are intentionally forgiving on input.  Output always uses the
same canonical keys so downstream reports stay stable:

* ``flow_min`` / ``flow_max`` on every reconciled feature,
* ``reconcile_status`` of ``ok`` or ``infeasible``,
* ``reconcile_error`` with a locatable message on infeasible features.
"""

import json

from .model import INF, Reach, Station, reconcile

INFLOW_SYNONYMS = {"inflow", "in", "source", "headwater", "入流", "入流口", "入口"}
OUTFLOW_SYNONYMS = {"outflow", "out", "sink", "mouth", "出流", "出流口", "出口"}

KIND_KEYS = ("type", "kind", "role", "node_type", "station_type", "类型")
ENDPOINT_PAIRS = (
    ("from", "to"),
    ("source", "target"),
    ("upstream", "downstream"),
    ("from_station", "to_station"),
    ("start", "end"),
    ("起点", "终点"),
)
SINGLE_FLOW_KEYS = ("flow", "discharge", "q", "流量", "日流量")
PASS_FLOW_KEYS = (
    "through", "through_flow", "total", "total_flow", "pass_flow",
    "过流", "过流量", "总量", "总流量")
RANGE_KEYS = ("flow_range", "flow_interval", "range", "interval", "流量区间")
MIN_KEYS = ("flow_min", "min_flow", "q_min", "lower", "min", "最小流量")
MAX_KEYS = ("flow_max", "max_flow", "q_max", "upper", "max", "最大流量")
ID_KEYS = ("id", "station_id", "site_id", "reach_id", "编号")


def _feature_id(feature):
    if feature.get("id") is not None:
        return str(feature["id"])
    props = feature.get("properties") or {}
    for key in ID_KEYS:
        if key in props and key != "id":
            return str(props[key])
    if "id" in props:
        return str(props["id"])
    return None


def _as_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "" or value.lower() in ("null", "none", "inf", "infinity"):
            return None if value.lower() not in ("inf", "infinity") else INF
    return float(value)


def _read_bounds(props, extra_single_keys=()):
    """Extract ``(lower, upper)`` flow bounds from arbitrary property names."""
    single_keys = SINGLE_FLOW_KEYS + tuple(extra_single_keys)
    lower = upper = None
    for key in MIN_KEYS:
        if key in props:
            lower = _as_float(props[key])
            break
    for key in MAX_KEYS:
        if key in props:
            upper = _as_float(props[key])
            break
    for key in RANGE_KEYS:
        value = props.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            lower = _as_float(value[0]) if lower is None else lower
            upper = _as_float(value[1]) if upper is None else upper
            break
    if lower is None and upper is None:
        for key in single_keys:
            if key in props:
                value = _as_float(props[key])
                lower = upper = value
                break
    return lower, upper


def _read_kind(props):
    for key in KIND_KEYS:
        raw = props.get(key)
        if raw is None:
            continue
        token = str(raw).strip().lower()
        if token in INFLOW_SYNONYMS:
            return "inflow"
        if token in OUTFLOW_SYNONYMS:
            return "outflow"
        if token in ("interior", "junction", "node", "内点", "节点", "站"):
            return "interior"
    return "interior"


def _endpoints(props):
    for from_key, to_key in ENDPOINT_PAIRS:
        if from_key in props and to_key in props:
            return str(props[from_key]), str(props[to_key])
    return None, None


def _is_line_feature(feature):
    geometry = feature.get("geometry") or {}
    return geometry.get("type") in ("LineString", "MultiLineString")


def _is_point_feature(feature):
    geometry = feature.get("geometry") or {}
    return geometry.get("type") in ("Point", "MultiPoint", None)


def split_features(features):
    """Split a mixed feature list into ``(station_features, reach_features)``."""
    stations, reaches, unknown = [], [], []
    for feature in features:
        props = feature.get("properties") or {}
        has_endpoints = _endpoints(props) != (None, None)
        if _is_line_feature(feature) or has_endpoints:
            reaches.append(feature)
        elif _is_point_feature(feature):
            stations.append(feature)
        else:
            unknown.append(feature)
    return stations, reaches, unknown


def load_layers(path):
    """Load one GeoJSON file and return ``(station_features, reach_features)``."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("type") == "FeatureCollection":
        features = payload.get("features", [])
    elif payload.get("type") == "Feature":
        features = [payload]
    else:
        raise ValueError("{} is not a FeatureCollection or Feature".format(path))
    stations, reaches, unknown = split_features(features)
    if unknown:
        raise ValueError("{} features have neither point nor line geometry".format(
            len(unknown)))
    return stations, reaches


def build_model(station_features, reach_features):
    """Convert GeoJSON features into :class:`Station` / :class:`Reach` objects."""
    stations = []
    for feature in station_features:
        props = feature.get("properties") or {}
        identifier = _feature_id(feature)
        kind = _read_kind(props)
        lower, upper = _read_bounds(props)
        if kind == "inflow":
            ext_lower = lower if lower is not None else 0.0
            ext_upper = upper if upper is not None else INF
            stations.append(
                Station(identifier, ext_lower, ext_upper, kind))
        elif kind == "outflow":
            # Outflow readings are positive magnitudes; the model stores
            # the signed external exchange, so negate them.
            ext_lower = -(upper if upper is not None else INF)
            ext_upper = -(lower if lower is not None else 0.0)
            stations.append(
                Station(identifier, ext_lower, ext_upper, kind))
        else:
            # Interior stations are conservative; a reading bounds the
            # total flow passing through ("only the total is known").
            pass_lower, pass_upper = _read_bounds(
                props, extra_single_keys=PASS_FLOW_KEYS)
            stations.append(Station(
                identifier, 0.0, 0.0, kind,
                pass_lower if pass_lower is not None else 0.0,
                pass_upper if pass_upper is not None else INF))

    reaches = []
    for feature in reach_features:
        props = feature.get("properties") or {}
        identifier = _feature_id(feature)
        source, target = _endpoints(props)
        lower, upper = _read_bounds(props)
        reaches.append(Reach(
            identifier, source, target,
            lower if lower is not None else 0.0,
            upper if upper is not None else INF))
    return stations, reaches


def _json_number(value):
    if value == INF or value == -INF:
        return None
    return value


def _magnitude_bounds(signed_lower, signed_upper):
    """Convert signed exchange bounds into ordered discharge magnitudes."""
    low = _json_number(abs(signed_lower))
    high = _json_number(abs(signed_upper))
    values = [value for value in (low, high) if value is not None]
    if not values:
        return None, None
    return min(values), max(values)


def write_back(station_features, reach_features, results, structural_errors):
    """Annotate the original features in place with reconciled flow bounds."""
    error_by_element = {
        error["element"]: error["message"] for error in structural_errors
    }

    def annotate(feature, bounds, signed):
        props = feature.setdefault("properties", {})
        identifier = _feature_id(feature)
        if identifier in error_by_element:
            props["reconcile_status"] = "error"
            props["reconcile_error"] = error_by_element[identifier]
            return
        result = None
        for candidate in results:
            if identifier in (candidate.station_ids
                              if bounds is None else candidate.reach_ids):
                result = candidate
                break
        if result is None:
            props["reconcile_status"] = "error"
            props["reconcile_error"] = "not reconciled"
            return
        if not result.feasible:
            props["reconcile_status"] = "infeasible"
            props["reconcile_error"] = result.violation.message
            return
        props["reconcile_status"] = "ok"
        if bounds is None:
            lower, upper = result.station_bounds[identifier]
            if signed:
                lower, upper = _magnitude_bounds(lower, upper)
        else:
            lower, upper = result.reach_bounds[identifier]
        props["flow_min"] = lower
        props["flow_max"] = upper

    for feature in station_features:
        annotate(feature, None, signed=True)

    for feature in reach_features:
        annotate(feature, True, signed=False)


def build_report(results, structural_errors):
    """Serializable reconciliation report, including violation certificates."""
    components = []
    for result in results:
        entry = {
            "stations": result.station_ids,
            "reaches": result.reach_ids,
            "feasible": result.feasible,
        }
        if result.feasible:
            entry["reach_bounds"] = {
                identifier: [_json_number(lower), _json_number(upper)]
                for identifier, (lower, upper) in result.reach_bounds.items()
            }
            entry["station_bounds"] = {
                identifier: list(_magnitude_bounds(lower, upper))
                for identifier, (lower, upper)
                in result.station_bounds.items()
            }
        else:
            violation = result.violation
            entry["violation"] = {
                "message": violation.message,
                "cut": violation.cut,
                "core": violation.core,
            }
        components.append(entry)
    return {
        "structural_errors": structural_errors,
        "components": components,
        "infeasible_component_count": sum(
            1 for result in results if not result.feasible),
    }


def reconcile_layers(station_features, reach_features):
    """Full pipeline: features in, annotated features and a report out."""
    stations, reaches = build_model(station_features, reach_features)
    results, structural_errors = reconcile(stations, reaches)
    write_back(station_features, reach_features, results, structural_errors)
    report = build_report(results, structural_errors)
    return report


def dump_feature_collection(features, path):
    payload = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
