from geojson.codec import dump, dumps, load, loads, GeoJSONEncoder
from geojson.utils import coords, map_coords
from geojson.geometry import Point, LineString, Polygon
from geojson.geometry import MultiLineString, MultiPoint, MultiPolygon
from geojson.geometry import GeometryCollection
from geojson.feature import Feature, FeatureCollection
from geojson.base import GeoJSON
from geojson.equivalence import normalize_geometry, canonical_geometry
from geojson.equivalence import canonical_ring, canonical_polygon
from geojson.equivalence import geometry_key, geometries_equal
from geojson.equivalence import merge_by_geometry, group_by_geometry
from geojson.equivalence import deduplicate_geometries
from geojson.equivalence import merge_to_features, dedupe_features
from geojson._version import __version__, __version_info__

__all__ = (
        ["dump", "dumps", "load", "loads", "GeoJSONEncoder"] +
        ["coords", "map_coords"] +
        ["Point", "LineString", "Polygon"] +
        ["MultiLineString", "MultiPoint", "MultiPolygon"] +
        ["GeometryCollection"] +
        ["Feature", "FeatureCollection"] +
        ["GeoJSON"] +
        ["normalize_geometry", "canonical_geometry", "canonical_ring",
         "canonical_polygon", "geometry_key", "geometries_equal",
         "merge_by_geometry", "group_by_geometry", "deduplicate_geometries",
         "merge_to_features", "dedupe_features"] +
        ["__version__", "__version_info__"]
)
