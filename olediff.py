# -*- coding: utf-8 -*-
"""
olediff: delta manifest for two OLE compound files.

Compares the streams of two compound files and emits a manifest describing
how to turn the old directory into the new one:

- ``modified``:        streams whose path is unchanged but content differs
- ``moved``:           streams with identical content at a new path
- ``move_candidates``: identical content with several possible pairings
                       (kept as a group, never paired by traversal order)
- ``added`` / ``removed``: streams only present on one side

Matching is content-based (SHA-256), so reordered directory entries or
relocated streams are not reported as re-uploads.  When several copies of
the same content exist, copies at identical paths are paired first; only
the leftovers can form moves or candidate groups.

The manifest plus the payload (content of added/modified streams) is
sufficient to rebuild the new directory from the old compound file.

Usage:
    python olediff.py diff OLD NEW [-m manifest.json] [-p payload_dir]
    python olediff.py verify OLD NEW -m manifest.json [-p payload_dir]
"""

import argparse
import hashlib
import json
import os
import sys

import olefile

MANIFEST_VERSION = 1


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def read_ole(ole_path):
    """Return (streams, storages) for an OLE compound file.

    streams: dict mapping tuple(path_components) -> bytes
    storages: set of tuple(path_components)
    """
    streams = {}
    storages = set()
    ole = olefile.OleFileIO(ole_path)
    try:
        for path in ole.listdir(streams=True, storages=True):
            key = tuple(path)
            if ole.get_type(path) == olefile.STGTY_STREAM:
                streams[key] = ole.openstream(path).read()
            else:
                storages.add(key)
    finally:
        ole.close()
    return streams, storages


def compute_diff(old_streams, new_streams,
                 old_storages=frozenset(), new_storages=frozenset()):
    """Compute the delta manifest between two stream dictionaries."""
    old_by_hash = {}
    for path, data in old_streams.items():
        old_by_hash.setdefault(_hash(data), []).append(path)
    new_by_hash = {}
    for path, data in new_streams.items():
        new_by_hash.setdefault(_hash(data), []).append(path)

    unchanged = []
    moved = []
    move_candidates = []
    removed = []
    added = []

    # Pair identical content, same paths first.  Every path ends up
    # exactly one of: unchanged, moved/candidate, removed, added.
    for digest in sorted(set(old_by_hash) | set(new_by_hash)):
        old_paths = sorted(old_by_hash.get(digest, []))
        new_paths = sorted(new_by_hash.get(digest, []))
        old_rest = [p for p in old_paths if p not in new_paths]
        new_rest = [p for p in new_paths if p not in old_paths]
        unchanged.extend(p for p in old_paths if p in new_paths)

        if old_rest and new_rest:
            size = len(new_streams[new_rest[0]])
            if len(old_rest) == 1 and len(new_rest) == 1:
                moved.append({"from": list(old_rest[0]),
                              "to": list(new_rest[0]),
                              "sha256": digest, "size": size})
            else:
                # Several legal pairings: report the group, do not pair.
                move_candidates.append({
                    "sha256": digest, "size": size,
                    "from": [list(p) for p in old_rest],
                    "to": [list(p) for p in new_rest]})
        else:
            removed.extend(old_rest)
            added.extend(new_rest)

    # A path removed and re-added with different content is an in-place
    # modification, not a delete+create.
    modified = []
    still_removed = []
    added_by_path = {p: None for p in added}
    for path in sorted(removed):
        if path in added_by_path:
            data = new_streams[path]
            modified.append({"path": list(path), "sha256": _hash(data),
                             "size": len(data)})
        else:
            still_removed.append(path)
    removed = still_removed
    added = [p for p in added if p not in {tuple(e["path"]) for e in modified}]

    # Content required from the payload: added + modified streams.
    payload_hashes = set()
    for entry in modified:
        payload_hashes.add(entry["sha256"])
    for path in added:
        payload_hashes.add(_hash(new_streams[path]))

    return {
        "version": MANIFEST_VERSION,
        "unchanged": sorted(list(p) for p in unchanged),
        "modified": sorted(modified, key=lambda e: e["path"]),
        "moved": sorted(moved, key=lambda e: (e["to"], e["from"])),
        "move_candidates": sorted(move_candidates,
                                  key=lambda e: (e["sha256"])),
        "added": sorted([{"path": list(p), "sha256": _hash(new_streams[p]),
                          "size": len(new_streams[p])} for p in added],
                        key=lambda e: e["path"]),
        "removed": sorted(list(p) for p in removed),
        "storages_added": sorted(list(p) for p in new_storages - old_storages),
        "storages_removed": sorted(list(p) for p in old_storages - new_storages),
        "payload": sorted(payload_hashes),
    }


def apply_diff(old_streams, manifest, payload):
    """Rebuild the new stream dict from old streams, manifest and payload.

    payload: dict mapping sha256 hex digest -> bytes for added/modified
    streams.  Raises ValueError if required content is missing.
    """
    result = dict(old_streams)

    def take(digest, hint):
        if digest in payload:
            return payload[digest]
        raise ValueError("missing payload content %s for %s" % (digest, hint))

    # Collect moved content before deleting anything (paths may be reused
    # by a different stream in the new directory, e.g. swaps).
    relocations = []  # (content, target_path)
    drop = set()
    for entry in manifest["moved"]:
        src = tuple(entry["from"])
        if src not in old_streams:
            raise ValueError("move source missing: %r" % (src,))
        relocations.append((old_streams[src], tuple(entry["to"])))
        drop.add(src)
    for group in manifest["move_candidates"]:
        sources = [tuple(p) for p in group["from"]]
        for src in sources:
            if src not in old_streams:
                raise ValueError("move source missing: %r" % (src,))
        # Content is identical for the whole group; any source works, so
        # the rebuilt directory is independent of how pairs are chosen.
        content = old_streams[sources[0]]
        drop.update(sources)
        for target in group["to"]:
            relocations.append((content, tuple(target)))

    for path in manifest["removed"]:
        drop.add(tuple(path))
    for path in drop:
        result.pop(path, None)

    for content, target in relocations:
        result[target] = content
    for entry in manifest["modified"]:
        result[tuple(entry["path"])] = take(entry["sha256"], entry["path"])
    for entry in manifest["added"]:
        result[tuple(entry["path"])] = take(entry["sha256"], entry["path"])
    return result


def build_payload(new_streams, manifest):
    """Return {sha256: bytes} for every added/modified stream."""
    by_hash = {}
    for data in new_streams.values():
        by_hash[_hash(data)] = data
    return {digest: by_hash[digest] for digest in manifest["payload"]}


def _write_payload_dir(payload_dir, payload):
    os.makedirs(payload_dir, exist_ok=True)
    for digest, data in payload.items():
        with open(os.path.join(payload_dir, digest), "wb") as fp:
            fp.write(data)


def _read_payload_dir(payload_dir, digests):
    payload = {}
    for digest in digests:
        with open(os.path.join(payload_dir, digest), "rb") as fp:
            payload[digest] = fp.read()
    return payload


def _cmd_diff(args):
    old_streams, old_storages = read_ole(args.old)
    new_streams, new_storages = read_ole(args.new)
    manifest = compute_diff(old_streams, new_streams,
                            old_storages, new_storages)
    payload = build_payload(new_streams, manifest)
    if args.payload_dir:
        _write_payload_dir(args.payload_dir, payload)
    elif manifest["payload"]:
        print("warning: manifest references payload content but no "
              "--payload-dir was given", file=sys.stderr)
    text = json.dumps(manifest, indent=2, sort_keys=False)
    if args.manifest:
        with open(args.manifest, "w") as fp:
            fp.write(text + "\n")
    else:
        print(text)
    summary = {k: len(manifest[k]) for k in
               ("unchanged", "modified", "moved", "move_candidates",
                "added", "removed")}
    print("summary: %s" % summary, file=sys.stderr)
    return 0


def _cmd_verify(args):
    old_streams, _ = read_ole(args.old)
    new_streams, _ = read_ole(args.new)
    with open(args.manifest) as fp:
        manifest = json.load(fp)
    payload = _read_payload_dir(args.payload_dir, manifest["payload"]) \
        if args.payload_dir else {}
    rebuilt = apply_diff(old_streams, manifest, payload)
    if rebuilt == new_streams:
        print("OK: manifest + payload rebuild %s from %s"
              % (args.new, args.old))
        return 0
    old_keys, new_keys = set(rebuilt), set(new_streams)
    for path in sorted(new_keys - old_keys):
        print("missing after rebuild: %r" % (path,), file=sys.stderr)
    for path in sorted(old_keys - new_keys):
        print("unexpected after rebuild: %r" % (path,), file=sys.stderr)
    for path in sorted(old_keys & new_keys):
        if rebuilt[path] != new_streams[path]:
            print("content mismatch: %r" % (path,), file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="delta manifest between two OLE compound files")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("diff", _cmd_diff), ("verify", _cmd_verify)):
        p = sub.add_parser(name, help=func.__doc__)
        p.add_argument("old", help="old compound file")
        if name == "verify":
            p.add_argument("new", help="new compound file")
        else:
            p.add_argument("new", help="new compound file")
        p.add_argument("-m", "--manifest",
                       help="manifest JSON path (default: stdout for diff)")
        p.add_argument("-p", "--payload-dir",
                       help="directory with added/modified stream content")
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
