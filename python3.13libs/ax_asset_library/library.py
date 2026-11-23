"""Library scanning, index cache and search.

No hou or Qt imports: scan_roots() runs in a worker thread, and everything here is
tested with plain Python.

Layout scanned under each root:
    <root>/<type folder>/<category ...>/<asset folder>/asset.json
Folders starting with "." (.trash, .sync caches) are skipped, and the scan does not
descend into an asset folder once its asset.json is found.
"""
import hashlib
import json
import os
import re

from . import schema

INDEX_VERSION = 2  # bump when AssetRecord fields change: old caches are ignored


class AssetRecord:
    """Flat summary of one asset, cheap to filter and to store in the index cache."""

    FIELDS = ("key", "id", "name", "type", "category", "tags", "description", "author",
              "created", "modified", "asset_dir", "root", "current", "versions", "files",
              "missing", "thumb", "thumb_mtime", "info", "problems", "sidecar_mtime",
              "source_kind", "thumb_method", "version_list")

    __slots__ = FIELDS

    def __init__(self, **kw):
        for f in self.FIELDS:
            setattr(self, f, kw.get(f))
        for f in ("tags", "versions", "missing", "problems", "version_list"):
            if getattr(self, f) is None:
                setattr(self, f, [])
        for f in ("files", "info"):
            if getattr(self, f) is None:
                setattr(self, f, {})
        for f in ("name", "type", "category", "description", "author", "created", "modified",
                  "thumb", "source_kind", "thumb_method"):
            if getattr(self, f) is None:
                setattr(self, f, "")

    def to_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}

    @classmethod
    def from_dict(cls, d):
        return cls(**{f: d.get(f) for f in cls.FIELDS})

    @property
    def sidecar(self):
        return os.path.join(self.asset_dir, schema.SIDECAR)

    @property
    def broken(self):
        return bool(self.problems)

    def primary_key(self, order):
        return schema.pick_file(self.files, order)

    def primary_file(self, order):
        key = self.primary_key(order)
        return self.files.get(key, "") if key else ""

    def version(self, number):
        """{version, created, author, note, files (absolute)} or None."""
        for v in self.version_list:
            if v.get("version") == number:
                return v
        return None

    def files_for(self, number=None):
        """Absolute files of a version (current when number is None)."""
        if number is None or number == self.current:
            return self.files
        v = self.version(number)
        return v["files"] if v else {}

    def __repr__(self):
        return "<AssetRecord %s %s/%s>" % (self.type, self.category, self.name)


# --- scanning -------------------------------------------------------------------
def norm_path(path):
    """Comparable form of a path (record keys are normalized sidecar paths)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


_norm = norm_path


def _record_from_sidecar(data, asset_dir, root, category, folder_type, mtime):
    problems = schema.validate(data)
    atype = data.get("type")
    if atype not in schema.ASSET_TYPES:
        atype = folder_type
    cur = schema.current_version(data) or {}
    files = {k: schema.resolve(asset_dir, v) for k, v in cur.get("files", {}).items()}
    thumb_info = data.get("thumbnail") or {}
    thumb_rel = thumb_info.get("file") or schema.THUMB
    version_list = [{"version": v["version"], "created": v.get("created", ""),
                     "author": v.get("author", ""), "note": v.get("note", ""),
                     "files": {k: schema.resolve(asset_dir, p) for k, p in v.get("files", {}).items()}}
                    for v in sorted(data.get("versions", []), key=lambda v: v["version"])]
    return AssetRecord(
        key=_norm(os.path.join(asset_dir, schema.SIDECAR)),
        id=data.get("id"), name=data.get("name") or os.path.basename(asset_dir),
        type=atype, category=category, tags=list(data.get("tags", [])),
        description=data.get("description", ""), author=data.get("author", ""),
        created=data.get("created", ""), modified=data.get("modified", ""),
        asset_dir=asset_dir, root=root, current=data.get("current", 0),
        versions=sorted(v["version"] for v in data.get("versions", [])),
        files=files, thumb=schema.resolve(asset_dir, thumb_rel),
        info=dict(data.get("info", {})), problems=problems, sidecar_mtime=mtime,
        source_kind=(data.get("source") or {}).get("kind", ""),
        thumb_method=thumb_info.get("method", ""), version_list=version_list,
    )


def _broken_record(asset_dir, root, category, folder_type, mtime, error):
    return AssetRecord(
        key=_norm(os.path.join(asset_dir, schema.SIDECAR)), id=None,
        name=os.path.basename(asset_dir), type=folder_type, category=category,
        asset_dir=asset_dir, root=root, thumb=os.path.join(asset_dir, schema.THUMB),
        problems=[str(error)], sidecar_mtime=mtime,
    )


def _refresh_dynamic(rec):
    """File-existence data that can change without the sidecar changing."""
    rec.missing = sorted(k for k, p in rec.files.items() if not os.path.exists(p))
    try:
        rec.thumb_mtime = os.path.getmtime(rec.thumb) if rec.thumb else None
    except OSError:
        rec.thumb_mtime = None


def load_record(asset_dir, root):
    """AssetRecord for one asset folder inside `root`, read straight from its asset.json."""
    rel = os.path.relpath(asset_dir, root).replace("\\", "/").split("/")
    folder_type = schema.FOLDER_TYPES.get(rel[0], "") if rel else ""
    category = "/".join(rel[1:-1])
    sidecar = os.path.join(asset_dir, schema.SIDECAR)
    mtime = os.path.getmtime(sidecar)
    rec = _record_from_sidecar(schema.load(sidecar), asset_dir, root, category, folder_type, mtime)
    _refresh_dynamic(rec)
    return rec


def scan_root(root, cached=None):
    """Scan one root. `cached` is {key: {"mtime", "record"}} from a previous scan.

    Returns (records, entries, errors); `entries` is the new cache content.
    Sidecars whose mtime is unchanged are not parsed again.
    """
    cached = cached or {}
    records, entries, errors = [], {}, []
    if not os.path.isdir(root):
        return records, entries, ["library root not found: %s" % root]

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = os.path.relpath(dirpath, root)
        parts = [] if rel == "." else rel.replace("\\", "/").split("/")
        if not parts:
            dirnames[:] = [d for d in dirnames if d in schema.FOLDER_TYPES]
            continue
        if schema.SIDECAR not in filenames:
            continue
        dirnames[:] = []  # never descend into an asset folder (v001/, textures/ ...)
        if len(parts) < 2:
            continue  # asset.json directly in a type folder: no asset folder, ignore
        folder_type = schema.FOLDER_TYPES.get(parts[0], "")
        category = "/".join(parts[1:-1])
        sidecar = os.path.join(dirpath, schema.SIDECAR)
        key = _norm(sidecar)
        try:
            mtime = os.path.getmtime(sidecar)
        except OSError as e:
            errors.append(str(e))
            continue

        entry = cached.get(key)
        rec = None
        if entry and entry.get("mtime") == mtime:
            try:
                rec = AssetRecord.from_dict(entry["record"])
                # Folder moved within the root since the cache was written: re-derive.
                if rec.category != category or rec.root != root:
                    rec = None
            except (KeyError, TypeError):
                rec = None
        if rec is None:
            try:
                data = schema.load(sidecar)
                rec = _record_from_sidecar(data, dirpath, root, category, folder_type, mtime)
            except schema.SidecarError as e:
                rec = _broken_record(dirpath, root, category, folder_type, mtime, e)
                errors.append(str(e))
        _refresh_dynamic(rec)
        records.append(rec)
        entries[key] = {"mtime": mtime, "record": rec.to_dict()}
    return records, entries, errors


MEGASCANS_THUMBS = "megascans_thumbs"


def _root_kind(root):
    """Roots are a path (library) or (path, kind) with kind "library", "megascans" or
    "gsg"."""
    if isinstance(root, (tuple, list)):
        return root[0], (root[1] or "library")
    return root, "library"


def megascans_thumb_dir(cache_dir):
    return os.path.join(cache_dir, MEGASCANS_THUMBS)


def index_path(cache_dir, root):
    digest = hashlib.sha1(_norm(root).encode("utf-8")).hexdigest()[:12]
    return os.path.join(cache_dir, "index_%s.json" % digest)


def load_index(cache_dir, root, kind="library"):
    """Cached entries for a root, or {} when there is no usable cache."""
    try:
        with open(index_path(cache_dir, root), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
        return {}
    if _norm(data.get("root", "")) != _norm(root) or data.get("kind", "library") != kind:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_index(cache_dir, root, entries, kind="library"):
    os.makedirs(cache_dir, exist_ok=True)
    path = index_path(cache_dir, root)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": INDEX_VERSION, "root": root, "kind": kind, "entries": entries}, f)
    os.replace(tmp, path)


def records_from_index(cache_dir, root, kind="library"):
    """Records straight from the cache, without touching the library (instant first paint)."""
    entries = load_index(cache_dir, root, kind)
    if kind in schema.VENDOR_KINDS:
        from . import megascans
        return megascans.records_from_entries(entries, root, megascans_thumb_dir(cache_dir))
    out = []
    for entry in entries.values():
        try:
            out.append(AssetRecord.from_dict(entry["record"]))
        except (KeyError, TypeError):
            pass
    return out


def dedupe_megascans(records):
    """Drop Megascans or GSG records whose id an earlier root already provided.

    The same vendor asset can sit in two roots (e.g. Bridge 'Downloaded' and a downloader
    folder). Its id (megascans:<asset id>:<type>, gsg:<code>:<type>) keys favorites, recents,
    scene nodes and the rendered thumbnail, so only the first root's copy is listed."""
    seen, out = set(), []
    for r in records:
        if r.source_kind in schema.VENDOR_KINDS and r.id:
            if r.id in seen:
                continue
            seen.add(r.id)
        out.append(r)
    return out


def scan_roots(roots, cache_dir, use_cache=True):
    """Scan every root and update their index files.

    `roots` holds expanded paths, or (path, kind) pairs where kind "megascans" or "gsg"
    reads a Megascans download or a Greyscalegorilla library in place (megascans.scan_root,
    gsg.scan_root; rendered thumbnails share one cache folder, ids differ by prefix).
    Returns (records, errors).
    Safe to call from a worker thread.
    """
    records, errors = [], []
    for item in roots:
        root, kind = _root_kind(item)
        cached = load_index(cache_dir, root, kind) if use_cache else {}
        if kind in schema.VENDOR_KINDS:
            from . import gsg, megascans
            scan = gsg.scan_root if kind == "gsg" else megascans.scan_root
            recs, entries, errs = scan(root, cached, megascans_thumb_dir(cache_dir))
        else:
            recs, entries, errs = scan_root(root, cached)
        records.extend(recs)
        errors.extend(errs)
        if entries or cached:
            try:
                save_index(cache_dir, root, entries, kind)
            except OSError as e:
                errors.append("could not write index cache: %s" % e)
    return dedupe_megascans(records), errors


def clear_index(cache_dir, roots):
    for item in roots:
        root, _kind = _root_kind(item)
        try:
            os.remove(index_path(cache_dir, root))
        except OSError:
            pass


# --- scope (sidebar) ---------------------------------------------------------------
def in_scope(rec, root=None, asset_type=None, category=None):
    if root is not None and _norm(rec.root) != _norm(root):
        return False
    if asset_type is not None and rec.type != asset_type:
        return False
    if category:
        return rec.category == category or rec.category.startswith(category + "/")
    return True


def _vendor_prefix(rec):
    return tuple(rec.id.split(":")[:2]) if rec.id else None


def companion_keys(records):
    """Keys of vendor material records that belong to a geometry record of the same asset
    (a Megascans 3D asset's or plant's textures, a GSG model's material). Imports use them
    through variants.linked_material(); the panel does not list them."""
    geo = {_vendor_prefix(r) for r in records
           if r.type == "geometry" and r.source_kind in schema.VENDOR_KINDS and r.id}
    return {r.key for r in records
            if r.type == "material" and r.source_kind in schema.VENDOR_KINDS and r.id
            and _vendor_prefix(r) in geo}


def browsable(records):
    """Records the panel lists: all but companion_keys()."""
    hidden = companion_keys(records)
    return [r for r in records if r.key not in hidden] if hidden else list(records)


def tree_counts(records):
    """{(root, type, category prefix): count}; type None = whole root, category "" = whole type."""
    counts = {}
    for r in records:
        keys = [(r.root, None, ""), (r.root, r.type, "")]
        if r.category:
            parts = r.category.split("/")
            keys += [(r.root, r.type, "/".join(parts[:i])) for i in range(1, len(parts) + 1)]
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    return counts


def categories(records, root=None, asset_type=None):
    """Sorted category paths (including parents) used by records in that scope."""
    out = set()
    for r in records:
        if in_scope(r, root, asset_type) and r.category:
            parts = r.category.split("/")
            out.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(out, key=str.lower)


# --- search ---------------------------------------------------------------------
TYPE_ALIASES = {
    "geo": "geometry", "geometry": "geometry", "model": "geometry", "mesh": "geometry",
    "mat": "material", "material": "material", "tex": "material", "texture": "material",
    "hdri": "hdri", "hdr": "hdri", "env": "hdri",
    "img": "image", "image": "image",
}
QUERY_FIELDS = ("type", "tag", "cat", "name", "fmt", "fav", "label")
_YES = ("1", "yes", "y", "true", "on")

_TOKEN = re.compile(r'(-)?(?:([A-Za-z]+):)?(?:"([^"]*)"?|(\S+))')


class Query:
    """Parsed search text.

    Free words must all match (name, tags, category or description). Field tokens:
        type:hdri   tag:rock   cat:trees   name:oak   fmt:abc
    fav:1 keeps favorites (asset ids in `favorites`); label:red, label:any and label:none
    filter color labels (asset ids in `labels`).
    A leading '-' excludes: -tag:wip, -broken. Quotes group words: tag:"red oak".
    Several values for the same field are OR-ed (type:geo type:hdri).
    """

    def __init__(self, text="", favorites=None, labels=None):
        self.text = text
        self.favorites = favorites or set()
        self.labels = labels or {}
        self.terms, self.neg_terms = [], []
        self.fields, self.neg_fields = {}, {}
        for m in _TOKEN.finditer(text or ""):
            neg, field, quoted, bare = m.group(1), m.group(2), m.group(3), m.group(4)
            value = quoted if quoted is not None else bare
            if value is None:
                continue
            value = value.strip().lower()
            if field and field.lower() not in QUERY_FIELDS:
                value = ("%s:%s" % (field, value)).lower()
                field = None
            if not value:
                continue
            if field:
                field = field.lower()
                if field == "type":
                    value = TYPE_ALIASES.get(value, value)
                (self.neg_fields if neg else self.fields).setdefault(field, []).append(value)
            else:
                (self.neg_terms if neg else self.terms).append(value)

    @property
    def empty(self):
        return not (self.terms or self.neg_terms or self.fields or self.neg_fields)

    def _field_match(self, rec, field, value):
        if field == "fav":
            return (rec.id in self.favorites) == (value in _YES)
        if field == "type":
            return rec.type == value
        if field == "tag":
            return any(value in t for t in rec.tags)
        if field == "cat":
            return value in rec.category.lower()
        if field == "name":
            return value in rec.name.lower()
        if field == "fmt":
            v = value.lstrip(".")
            return any(k == v or k.startswith(v) for k in rec.files)
        if field == "label":
            label = self.labels.get(rec.id or "", "")
            if value in ("any", "1"):
                return bool(label)
            if value in ("none", "0"):
                return not label
            return label == value
        return False

    @staticmethod
    def _haystack(rec):
        return " ".join([rec.name, rec.category, rec.description] + list(rec.tags)).lower()

    def matches(self, rec):
        if self.empty:
            return True
        hay = self._haystack(rec)
        for t in self.terms:
            if t == "broken":
                if not (rec.broken or rec.missing):
                    return False
            elif t not in hay:
                return False
        for t in self.neg_terms:
            if t == "broken":
                if rec.broken or rec.missing:
                    return False
            elif t in hay:
                return False
        for field, values in self.fields.items():
            if not any(self._field_match(rec, field, v) for v in values):
                return False
        for field, values in self.neg_fields.items():
            if any(self._field_match(rec, field, v) for v in values):
                return False
        return True


def sort_records(records, mode="name", labels=None):
    """Sort by name, or newest first by "added"/"modified" (ties stay in name order).

    "label" sorts by the COLOR_LABELS order, unlabeled last. Needs the labels ({id: key}).
    """
    out = sorted(records, key=lambda r: (r.name.lower(), r.category.lower()))
    if mode == "type":
        order = {t: i for i, t in enumerate(schema.ASSET_TYPES)}
        out.sort(key=lambda r: order.get(r.type, 99))
    elif mode == "category":
        out.sort(key=lambda r: (r.category == "", r.category.lower()))
    elif mode == "added":
        out.sort(key=lambda r: r.created or "", reverse=True)
    elif mode == "modified":
        out.sort(key=lambda r: r.modified or "", reverse=True)
    elif mode == "label":
        order = {key: i for i, key in enumerate(schema.COLOR_LABEL_KEYS)}
        labels = labels or {}
        out.sort(key=lambda r: order.get(labels.get(r.id or "", ""), len(order)))
    return out
