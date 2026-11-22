"""Megascans (Quixel Bridge) downloads: find asset folders and read their metadata.

No hou or Qt imports, so it is tested with plain Python. publish.plan_ingest turns the
result into library assets.

A Megascans asset folder holds <id>.json next to its files:
    Downloaded/surface/rock_cliff_wdeledb/wdeledb.json, wdeledb_4K_Albedo.jpg, ...
    Downloaded/3d/nature_tree_wgqvdhf/wgqvdhf.json, wgqvdhf_LOD0.fbx, textures ...
    Downloaded/3dplant/<name>_xefmcgrqx/xefmcgrqx.json, Var1/..LOD0.fbx, Textures/Atlas/...
"""
import json
import os
import re

from . import schema, textures

# Megascans asset kind -> what the library makes of it
SURFACE_KINDS = {"surface", "atlas", "decal", "imperfection", "displacement"}
MESH_KINDS = {"3d"}
PLANT_KINDS = {"3dplant"}
SKIP_DIRS = {"thumbs", "previews", "billboard"}


class MegascansAsset:
    def __init__(self, folder, data):
        self.folder = folder
        self.data = data
        self.id = str(data.get("id") or "")
        self.name = str(data.get("name") or (data.get("semanticTags") or {}).get("name") or self.id)
        cats = [str(c) for c in data.get("categories") or []]
        self.kind = cats[0].lower() if cats else ""
        self.categories = cats[1:]
        self.tags = [str(t) for t in data.get("tags") or []]
        self.meta = {m.get("key"): m.get("value") for m in data.get("meta") or []
                     if isinstance(m, dict) and m.get("key")}

    @property
    def library_name(self):
        """Readable and unique: 'Rotten Tree Stump' + id -> Rotten_Tree_Stump_wgspfio."""
        return schema.sanitize_name("%s_%s" % (self.name, self.id) if self.id else self.name)

    @property
    def category(self):
        """Lower case: Megascans mixes 'Nature/Rock' and 'nature/rock'."""
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
        for n in ("%s_Preview.png" % self.id, "%s_Preview.jpg" % self.id):
            p = os.path.join(self.folder, n)
            if os.path.isfile(p):
                return p
        prev = os.path.join(self.folder, "previews")
        if os.path.isdir(prev):
            for n in sorted(os.listdir(prev)):
                if schema.is_image(n):
                    return os.path.join(prev, n)
        return None

    def __repr__(self):
        return "<MegascansAsset %s %s %s>" % (self.kind, self.id, self.name)


def read_asset(folder):
    """MegascansAsset for a folder with a Megascans <id>.json, or None."""
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for n in names:
        if not n.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, n), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id") and data.get("categories") and \
                os.path.splitext(n)[0] == str(data["id"]):
            return MegascansAsset(folder, data)
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
        for dirpath, dirnames, _files in os.walk(p):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            a = read_asset(dirpath)
            if a is not None:
                found.append(a)
                dirnames[:] = []          # don't descend into an asset folder
        if found:
            assets.extend(found)
        else:
            other.append(p)
    return assets, other


def _files(folder, recurse=False):
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS] if recurse else []
        out.extend(os.path.join(dirpath, f) for f in sorted(filenames))
    return out


def _lod_rank(path):
    m = re.search(r"lod(\d+)", os.path.basename(path).lower())
    return int(m.group(1)) if m else -1


def mesh_files(files):
    """{format key: file} of the most detailed LOD per format (no LOD tag = most detailed)."""
    best = {}
    for f in files:
        if not schema.is_geometry(f):
            continue
        key = schema.format_key(f)
        if key not in best or _lod_rank(f) < _lod_rank(best[key]):
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


FBX_CM_SCALE = 0.01  # Megascans FBX/OBJ files are in centimeters


def _mesh_info(info, meshes, size_m):
    out = dict(info)
    if any(k in ("fbx", "obj") for k in meshes):
        out["import_scale"] = FBX_CM_SCALE
    if size_m:
        out["megascans_size_m"] = size_m
    return out


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


def plan(asset):
    """[(asset type, name, {key: file}, info, extra files)] for one Megascans asset.

    Surfaces: one material. 3D assets: geometry (most detailed LOD) + material. Plants:
    one geometry asset per variation + the atlas material. Other kinds: nothing.
    """
    out = []
    info = {"megascans_id": asset.id}
    if asset.kind in SURFACE_KINDS:
        ts = texture_set(_files(asset.folder), asset.library_name)
        if ts:
            minfo = dict(info, channels=sorted(ts.channels))
            if asset.displacement_scale is not None:
                minfo["displacement_scale"] = asset.displacement_scale
            out.append(("material", asset.library_name, ts.channels, minfo, ts.extra))
    elif asset.kind in MESH_KINDS:
        files = _files(asset.folder)
        meshes = mesh_files(files)
        if meshes:
            out.append(("geometry", asset.library_name, meshes,
                        _mesh_info(info, meshes, asset.size_m()), []))
        ts = texture_set(files, asset.library_name)
        if ts:
            out.append(("material", asset.library_name, ts.channels,
                        dict(info, channels=sorted(ts.channels)), ts.extra))
    elif asset.kind in PLANT_KINDS:
        for var in sorted(d for d in os.listdir(asset.folder)
                          if re.fullmatch(r"var\d+", d, re.IGNORECASE)):
            meshes = mesh_files(_files(os.path.join(asset.folder, var)))
            if meshes:
                out.append(("geometry", "%s_%s" % (asset.library_name, var), meshes,
                            _mesh_info(dict(info, variation=var), meshes, asset.size_m(var)), []))
        tex = os.path.join(asset.folder, "Textures", "Atlas")
        ts = texture_set(_files(tex if os.path.isdir(tex) else asset.folder), asset.library_name)
        if ts:
            out.append(("material", asset.library_name, ts.channels,
                        dict(info, channels=sorted(ts.channels)), ts.extra))
    return out
