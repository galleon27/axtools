"""Megascans downloads: find asset folders, read their metadata, browse them in place.

No hou or Qt imports, so it is tested with plain Python.

Two layouts are recognized. Quixel Bridge, with full metadata in <id>.json:
    Downloaded/surface/rock_cliff_wdeledb/wdeledb.json, wdeledb_4K_Albedo.jpg, ...
    Downloaded/3d/nature_tree_wgqvdhf/wgqvdhf.json, wgqvdhf_LOD0.fbx, textures ...
    Downloaded/3dplant/<name>_xefmcgrqx/xefmcgrqx.json, Var1/..LOD0.fbx, Textures/Atlas/...
and downloader folders whose <id>.json holds only {"assetID": ...}; the name is in the
folder name and the kind in the parent folder:
    megascans_dl/3d/vcsaeeyfa_Burnt Log/vcsaeeyfa.json, vcsaeeyfa_LOD0.fbx, ...
    megascans_dl/surfaces/uhpjdeyn_Fresh Windswept Snow/uhpjdeyn.json, ...

publish.plan_ingest turns assets into library assets (batch import). scan_root turns a
whole Megascans folder into AssetRecords without writing anything into it (a "Megascans
root" in the settings); thumbnails for those go to a cache folder in the user prefs.
"""
import datetime
import json
import os
import re

from . import schema, textures

# Megascans asset kind -> what the library makes of it
SURFACE_KINDS = {"surface", "atlas", "decal", "imperfection", "displacement"}
MESH_KINDS = {"3d"}
PLANT_KINDS = {"3dplant"}
SKIP_DIRS = {"thumbs", "previews", "billboard"}
KIND_ALIASES = {
    "3d": "3d", "3dasset": "3d", "3dassets": "3d", "models": "3d",
    "3dplant": "3dplant", "3dplants": "3dplant", "plant": "3dplant", "plants": "3dplant",
    "surface": "surface", "surfaces": "surface",
    "atlas": "atlas", "atlases": "atlas",
    "decal": "decal", "decals": "decal",
    "imperfection": "imperfection", "imperfections": "imperfection",
    "displacement": "displacement", "displacements": "displacement",
    "brush": "brush", "brushes": "brush",
}
META_KEYS = ("height", "length", "width", "dimensions")
FBX_CM_SCALE = 0.01  # Megascans FBX/OBJ files are in centimeters
MAX_DEPTH = 4        # how deep scan_root looks for asset folders below the root
CACHE_VERSION = 3    # bump when plan()/records_for() change: cached records are rebuilt


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _in_skip_dir(rel):
    parts = rel.replace("\\", "/").lower().split("/")[:-1]
    return any(p in SKIP_DIRS for p in parts)


class MegascansAsset:
    def __init__(self, folder, data, json_stem="", files=None, kind_hint=""):
        self.folder = folder
        self.data = data
        self.id = str(data.get("id") or data.get("assetID") or json_stem or "")
        self._files = files            # absolute paths, every file in the asset folder
        base = os.path.basename(folder.rstrip("/\\"))
        name = data.get("name") or (data.get("semanticTags") or {}).get("name")
        if not name:
            if self.id and base.startswith(self.id + "_"):
                name = base[len(self.id) + 1:]          # "vcsaeeyfa_Burnt Log"
            elif self.id and base.endswith("_" + self.id):
                name = base[:-len(self.id) - 1]          # "rock_cliff_wdeledb"
            else:
                name = base
        self.name = str(name)
        cats = [str(c) for c in data.get("categories") or []]
        self.kind = KIND_ALIASES.get(cats[0].lower(), cats[0].lower()) if cats else kind_hint
        self.categories = cats[1:]
        self.tags = [str(t) for t in data.get("tags") or []]
        meta = data.get("meta") or []
        if isinstance(meta, dict):     # the trimmed form kept in the scan cache
            self.meta = dict(meta)
        else:
            self.meta = {m.get("key"): m.get("value") for m in meta
                         if isinstance(m, dict) and m.get("key")}
        if not self.kind:
            self.kind = self._infer_kind()

    # ---- files -------------------------------------------------------------------------------
    @property
    def files(self):
        if self._files is None:
            self._files = list_files(self.folder)
        return self._files

    def rel(self, path):
        return os.path.relpath(path, self.folder).replace("\\", "/")

    @property
    def content(self):
        """Files outside Thumbs/previews/Billboard folders."""
        return [f for f in self.files if not _in_skip_dir(self.rel(f))]

    def _infer_kind(self):
        """Kind for assets in folders like 'unknown': meshes -> 3d, texture set -> surface."""
        if any(schema.is_geometry(f) for f in self.content):
            return "3d"
        if texture_set(self.content, "x") is not None:
            return "surface"
        return ""

    # ---- metadata ------------------------------------------------------------------------------
    @property
    def library_name(self):
        """Readable and unique: 'Rotten Tree Stump' + id -> Rotten_Tree_Stump_wgspfio."""
        return schema.sanitize_name("%s_%s" % (self.name, self.id) if self.id else self.name)

    @property
    def category(self):
        """Bridge categories in lower case (Megascans mixes 'Nature/Rock' and 'nature/rock')."""
        return "/".join(schema.sanitize_name(c.lower()) for c in self.categories if c.strip())

    def size_m(self, variation=None):
        """Largest dimension in meters from the metadata, or None."""
        dims = []
        if variation is None:
            dims = [self.meta.get(k) for k in ("length", "width", "height")]
        else:
            num = int(re.sub(r"\D", "", variation) or 0)
            for d in self.meta.get("dimensions") or []:
                if isinstance(d, dict) and d.get("variation") == num:
                    dims = [d.get(k) for k in ("length", "width", "height")]
        vals = []
        for v in dims:
            m = re.search(r"[0-9]*\.?[0-9]+", str(v or ""))
            if m:
                vals.append(float(m.group(0)))
        return max(vals) if vals else None

    @property
    def displacement_scale(self):
        """Displacement height in meters, for surfaces only ('height' on a 3D asset is the
        object's size, not a displacement depth)."""
        if self.kind not in SURFACE_KINDS:
            return None
        m = re.search(r"[0-9]*\.?[0-9]+", str(self.meta.get("height", "")))
        return float(m.group(0)) if m else None

    @property
    def preview(self):
        wanted = {("%s_preview.png" % self.id).lower(), ("%s_preview.jpg" % self.id).lower()}
        for f in self.files:
            if os.path.basename(f).lower() in wanted:
                return f
        for f in self.files:
            if self.rel(f).lower().startswith("previews/") and schema.is_image(f):
                return f
        return None

    def __repr__(self):
        return "<MegascansAsset %s %s %s>" % (self.kind, self.id, self.name)


def looks_like_download(path, _depth=0):
    """True for a Megascans download: a folder with kind folders (3d, surfaces, ...) whose
    asset folders hold a Megascans <id>.json, or Bridge's 'Downloaded' folder.

    A folder name alone isn't enough ('models' or 'plants' are common in projects), so a
    few asset folders of up to three kind folders are checked."""
    try:
        entries = [e for e in os.scandir(path) if e.is_dir()]
    except OSError:
        return False
    if _depth == 0:
        for e in entries:
            if e.name.lower() == "downloaded" and looks_like_download(e.path, 1):
                return True
    kinds = [e for e in entries if e.name.lower() in KIND_ALIASES][:3]
    for kind in kinds:
        try:
            samples = [e for e in os.scandir(kind.path) if e.is_dir()][:5]
        except OSError:
            continue
        for s in samples:
            if read_asset(s.path) is not None:
                return True
    return False


def list_files(folder):
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        out.extend(os.path.join(dirpath, f) for f in sorted(filenames))
    return out


def _json_candidates(folder, names):
    """JSON file names in an asset folder that may be its <id>.json, best first."""
    base = os.path.basename(folder.rstrip("/\\")).lower()
    js = [n for n in names if n.lower().endswith(".json")]
    def score(n):
        stem = os.path.splitext(n)[0].lower()
        return 0 if (base.startswith(stem + "_") or base.endswith("_" + stem)) else 1
    return sorted(js, key=score)


def read_asset(folder, names=None, kind_hint=None):
    """MegascansAsset for a folder with a Megascans <id>.json, or None.

    kind_hint: the asset kind when the JSON has none; by default from the parent folder
    name (3d, surfaces, atlases, ...).
    """
    if names is None:
        try:
            names = os.listdir(folder)
        except OSError:
            return None
    if kind_hint is None:
        parent = os.path.basename(os.path.dirname(folder.rstrip("/\\"))).lower()
        kind_hint = KIND_ALIASES.get(parent, "")
    for n in _json_candidates(folder, names):
        try:
            with open(os.path.join(folder, n), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        stem = os.path.splitext(n)[0]
        aid = str(data.get("id") or data.get("assetID") or "")
        if aid and aid == stem and (data.get("categories") or "assetID" in data):
            return MegascansAsset(folder, data, stem, kind_hint=kind_hint)
    return None


def find_assets(paths):
    """Megascans asset folders in `paths` (searched recursively). Returns (assets, other paths):
    other paths are the given files and folders that hold no Megascans asset."""
    assets, other = [], []
    for p in paths:
        if not os.path.isdir(p):
            other.append(p)
            continue
        found = []
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            a = read_asset(dirpath, filenames + dirnames)
            if a is not None:
                found.append(a)
                dirnames[:] = []          # don't descend into an asset folder
        if found:
            assets.extend(found)
        else:
            other.append(p)
    return assets, other


# --- asset -> library items -------------------------------------------------------------------
def lod_rank(path):
    m = re.search(r"lod(\d+)", os.path.basename(path).lower())
    return int(m.group(1)) if m else -1


_lod_rank = lod_rank


def mesh_files(files):
    """{format key: file} of the most detailed LOD per format (no LOD tag = most detailed)."""
    best = {}
    for f in files:
        if not schema.is_geometry(f):
            continue
        key = schema.format_key(f)
        if key not in best or lod_rank(f) < lod_rank(best[key]):
            best[key] = f
    return best


def texture_set(files, name):
    """Best TextureSet from a list of image files (highest resolution wins), or None."""
    images = [f for f in files if schema.is_image(f) and not textures.is_preview(f)]
    sets, _left, _prev = textures.group(images)
    if not sets:
        return None
    ts = max(sets, key=lambda s: len(s.channels))
    ts.name = name
    return ts


def _mesh_info(info, meshes, size_m):
    out = dict(info)
    if any(k in ("fbx", "obj") for k in meshes):
        out["import_scale"] = FBX_CM_SCALE
    if size_m:
        out["megascans_size_m"] = size_m
    return out


def _resolution(channels):
    """[w, h] from the '4K' word in the base color file name, if any."""
    f = channels.get("basecolor") or next(iter(channels.values()), "")
    m = re.search(r"(?:^|[_\-.])(\d{1,2})k(?:[_\-.]|$)", os.path.basename(f).lower())
    return [int(m.group(1)) * 1024] * 2 if m else None


def check_scale(info):
    """Confirm or correct info["import_scale"] once the file's bounds are known: the scaled
    size should match the size Megascans lists. Returns the scale to use."""
    scale = info.get("import_scale")
    size, bounds = info.get("megascans_size_m"), info.get("bounds")
    if not scale or not size or not bounds:
        return scale
    extent = max(bounds[1][i] - bounds[0][i] for i in range(3))
    for candidate in (scale, 1.0):
        if extent > 0 and abs(extent * candidate - size) / size < 0.25:
            return candidate
    return scale


def _material(asset, files, info):
    ts = texture_set(files, asset.library_name)
    if not ts:
        return []
    minfo = dict(info, channels=sorted(ts.channels))
    res = _resolution(ts.channels)
    if res:
        minfo["resolution"] = res
    if asset.displacement_scale is not None:
        minfo["displacement_scale"] = asset.displacement_scale
    return [("material", asset.library_name, ts.channels, minfo, ts.extra)]


def plan(asset):
    """[(asset type, name, {key: file}, info, extra files)] for one Megascans asset.

    Surfaces: one material. 3D assets: geometry (most detailed LOD) + material. Plants:
    one geometry asset per variation + the atlas material. Other kinds: nothing.
    """
    info = {"megascans_id": asset.id, "vendor_kind": asset.kind}
    if asset.kind == "atlas":
        size = asset.size_m()
        if size is not None:
            info["megascans_size_m"] = size
    content = asset.content
    top = [f for f in content if "/" not in asset.rel(f)]
    if asset.kind in SURFACE_KINDS:
        return _material(asset, top, info)
    if asset.kind in MESH_KINDS:
        out = []
        meshes = mesh_files(top)
        if meshes:
            out.append(("geometry", asset.library_name, meshes,
                        _mesh_info(info, meshes, asset.size_m()), []))
        return out + _material(asset, top, info)
    if asset.kind in PLANT_KINDS:
        out, variations = [], {}
        for f in content:
            first = asset.rel(f).split("/")[0]
            if re.fullmatch(r"var\d+", first, re.IGNORECASE):
                variations.setdefault(first, []).append(f)
        for var in sorted(variations, key=lambda v: int(re.sub(r"\D", "", v) or 0)):
            meshes = mesh_files(variations[var])
            if meshes:
                out.append(("geometry", "%s_%s" % (asset.library_name, var), meshes,
                            _mesh_info(dict(info, variation=var), meshes, asset.size_m(var)), []))
        atlas = [f for f in content if asset.rel(f).lower().startswith("textures/atlas/")]
        return out + _material(asset, atlas or top, info)
    return []


# --- browsing a Megascans folder in place ------------------------------------------------------
def record_id(asset_id, asset_type, variation=None, prefix="megascans"):
    """Stable id for favorites, recents and scene nodes: <prefix>:<id>:<type>[:<var>]."""
    return ":".join([prefix, asset_id, asset_type] + ([variation] if variation else []))


def thumb_path(thumb_dir, rid):
    return os.path.join(thumb_dir, schema.sanitize_name(rid.replace(":", "_")) + ".jpg")


def records_for(asset, root, thumb_dir, thumbs, mtime):
    """AssetRecords for one Megascans asset."""
    return build_records(asset, plan(asset), root, thumb_dir, thumbs, mtime,
                         "megascans", "Megascans")


def build_records(asset, items, root, thumb_dir, thumbs, mtime, prefix, note):
    """AssetRecords for one asset of a library browsed in place (Megascans, GSG).

    `items` is the asset's plan() output; `asset` needs .id .folder .preview .tags
    .category .kind. `thumbs`: {file name: mtime} in thumb_dir."""
    from .library import AssetRecord
    created = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ") if mtime else ""
    preview = asset.preview
    out = []
    for asset_type, name, files, info, _extra in items:
        rid = record_id(asset.id, asset_type, info.get("variation"), prefix)
        cache = thumb_path(thumb_dir, rid)
        cached = thumbs.get(os.path.basename(cache))
        thumb = cache if cached or not preview else preview
        files = dict(files)
        if preview:
            info = dict(info, preview=preview)  # fallback when a rendered thumbnail is deleted
        out.append(AssetRecord(
            key=_norm(asset.folder) + "|" + rid, id=rid, name=name, type=asset_type,
            category=asset.category or getattr(asset, "fallback_category", asset.kind),
            tags=list(asset.tags),
            created=created, modified=created, asset_dir=asset.folder, root=root,
            current=1, versions=[1], files=files, missing=[], thumb=thumb,
            thumb_mtime=cached or (mtime if preview else None), info=dict(info),
            problems=[], sidecar_mtime=mtime, source_kind=prefix,
            thumb_method="karma" if cached else ("image" if preview else ""),
            version_list=[{"version": 1, "created": created, "author": "", "note": note,
                           "files": files}]))
    return out


def _apply_thumb(rec, thumb_dir, thumbs):
    """Point a record at its rendered thumbnail in the cache when there is one now, else at
    the Megascans preview image, else at nothing (type letter placeholder)."""
    name = os.path.basename(thumb_path(thumb_dir, rec.id))
    if name in thumbs:
        rec.thumb = os.path.join(thumb_dir, name)
        rec.thumb_mtime = thumbs[name]
        rec.thumb_method = "karma"
    elif rec.thumb_method == "karma":          # the rendered file was deleted
        preview = (rec.info or {}).get("preview")
        if preview:
            rec.thumb, rec.thumb_mtime, rec.thumb_method = preview, rec.sidecar_mtime, "image"
        else:
            rec.thumb, rec.thumb_mtime, rec.thumb_method = os.path.join(thumb_dir, name), None, ""
    return rec


_DROP = ("version_list", "root", "missing", "problems", "versions", "current")


def _pack(rec):
    """Record dict for the cache, without fields that are the same for every Megascans asset."""
    d = rec.to_dict()
    for k in _DROP:
        d.pop(k, None)
    return d


def _unpack(d, root):
    from .library import AssetRecord
    rec = AssetRecord.from_dict(d)
    rec.root, rec.current, rec.versions = root, 1, [1]
    rec.version_list = [{"version": 1, "created": rec.created, "author": "", "note": "Megascans",
                         "files": rec.files}]
    return rec


def _entry_records(entry, root, thumb_dir, thumbs):
    """Records of a current cache entry."""
    return [_apply_thumb(_unpack(d, root), thumb_dir, thumbs) for d in entry["records"]]


def content_subdirs(folder):
    """{relative path: mtime} of the asset's content folders (Var1/, Textures/Atlas/, ...).

    Adding or replacing files in a nested folder changes that folder's modification time but
    not the asset folder's, so the scan cache compares these too. Thumbs/previews/Billboard
    folders don't affect the records and are left out."""
    out = {}
    for dirpath, dirnames, _files in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for d in dirnames:
            p = os.path.join(dirpath, d)
            try:
                out[os.path.relpath(p, folder).replace("\\", "/")] = os.stat(p).st_mtime
            except OSError:
                pass
    return out


def _subdirs_unchanged(entry):
    subdirs = entry.get("subdirs")
    if subdirs is None:
        # Entries from before subfolders were tracked: only plants (Var*/, Textures/Atlas/)
        # depend on nested folders, so only those are read again.
        return not any("variation" in (d.get("info") or {}) for d in entry["records"])
    for rel, mtime in subdirs.items():
        try:
            if os.stat(os.path.join(entry["folder"], rel)).st_mtime != mtime:
                return False
        except OSError:
            return False
    return True


def _entry_current(entry, mtime=None):
    """A cache entry is reused when its folder (and its content subfolders, when `mtime` is
    given: the scan) are unchanged and its records are current; otherwise the folder is
    read again (the cache keeps no file lists)."""
    if not (entry is not None and entry.get("version") == CACHE_VERSION
            and isinstance(entry.get("records"), list) and entry.get("folder")):
        return False
    if mtime is None:
        return True
    return entry.get("mtime") == mtime and _subdirs_unchanged(entry)


def records_from_entries(entries, root, thumb_dir):
    """Records straight from a scan cache, without touching the Megascans folder."""
    thumbs = _thumb_index(thumb_dir) if thumb_dir else {}
    out = []
    for entry in entries.values():
        if not _entry_current(entry):
            continue
        try:
            out.extend(_entry_records(entry, root, thumb_dir, thumbs))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _thumb_index(thumb_dir):
    out = {}
    try:
        with os.scandir(thumb_dir) as it:
            for e in it:
                if e.is_file():
                    out[e.name] = e.stat().st_mtime
    except OSError:
        pass
    return out


def scan_root(root, cached=None, thumb_dir="", reader=None, recorder=None, kind_folders=None):
    """Records for every asset `reader` recognizes under `root` (Megascans by default),
    without writing anything there. `recorder(asset, root, thumb_dir, thumbs, mtime)` makes
    the records; folders named in `kind_folders` (3d, surfaces, ...) are walked into.

    `cached` is {folder key: entry} from a previous scan. An asset folder whose modification
    time is unchanged is not listed again, so a rescan of a big library only reads the
    folders above the assets. Returns (records, entries, errors).
    """
    reader = reader or read_asset
    recorder = recorder or records_for
    kind_folders = KIND_ALIASES if kind_folders is None else kind_folders
    cached = cached or {}
    records, entries, errors = [], {}, []
    if not os.path.isdir(root):
        return records, entries, ["Folder not found: %s" % root]
    thumbs = _thumb_index(thumb_dir) if thumb_dir else {}

    def visit(folder, depth, kind_hint):
        try:
            with os.scandir(folder) as it:
                subdirs = [e for e in it if e.is_dir() and not e.name.startswith(".")]
        except OSError as e:
            errors.append(str(e))
            return
        for e in sorted(subdirs, key=lambda d: d.name.lower()):
            hint = kind_folders.get(e.name.lower(), kind_hint)
            key = _norm(e.path)
            try:
                mtime = e.stat().st_mtime
            except OSError:
                continue
            if e.name.lower() in kind_folders:     # 3d, surfaces, ...: a kind folder, not an asset
                if depth < MAX_DEPTH:
                    visit(e.path, depth + 1, hint)
                continue
            entry = cached.get(key)
            if _entry_current(entry, mtime):
                entries[key] = entry
                try:
                    records.extend(_entry_records(entry, root, thumb_dir, thumbs))
                except (KeyError, TypeError, ValueError, OSError) as err:
                    errors.append("%s: %s" % (e.path, err))
                continue
            else:
                try:
                    names = os.listdir(e.path)
                except OSError as err:
                    errors.append(str(err))
                    continue
                asset = reader(e.path, names, hint)
                if asset is None:
                    if depth < MAX_DEPTH:
                        visit(e.path, depth + 1, hint)
                    continue
                entry = {"mtime": mtime, "folder": e.path,
                         "subdirs": content_subdirs(e.path)}
            entries[key] = entry
            try:
                recs = recorder(asset, root, thumb_dir, thumbs, mtime)
            except (OSError, ValueError, KeyError) as err:
                errors.append("%s: %s" % (e.path, err))
                continue
            entry["version"] = CACHE_VERSION
            entry["records"] = [_pack(r) for r in recs]
            records.extend(recs)

    visit(root, 1, "")
    return records, entries, errors
