"""Greyscalegorilla (GSG) library folders: read their metadata, browse them in place.

No hou or Qt imports, so it is tested with plain Python. Layout (one folder per asset, a
small JSON metadata file .gsga/.gsgm next to the files):
    hdris/GSG_HC006_A003_<Name>/<file>.exr .hdr [.exr.rat] <folder>_preview.jpg <Name>.gsga
    materials/GSG_MC036_A001_<Name>/<folder>_4k_basecolor.tif ... _preview.jpg <Name>.gsgm
    textures/GSG_GC001_A009_<Name>/<folder>.jpg _preview.jpg <Name>.gsga   (gobos)
    models/<...>/ geometry files (+ textures)
The scan reuses megascans.scan_root (cached records, nothing written into the library).
"""
import json
import os
import re

from . import megascans, schema

# Folder name -> GSG kind; scan_root walks into these instead of treating them as assets.
KIND_FOLDERS = {"hdris": "hdri", "hdri": "hdri", "materials": "material",
                "material": "material", "textures": "texture", "texture": "texture",
                "models": "model", "model": "model"}
# GSG "type" in the metadata -> kind
TYPE_KINDS = {"hdri": "hdri", "material": "material", "gobo": "texture", "texture": "texture",
              "model": "model", "3d": "model"}
META_EXTS = (".gsga", ".gsgm")
HDRI_ORDER = (".exr", ".hdr", ".rat")       # .exr.rat is Houdini's mipmapped copy of the .exr
IGNORED = {"thumbs.db", "desktop.ini", ".ds_store"}
_PREVIEW = re.compile(r"_preview\.(jpe?g|png)$", re.IGNORECASE)


class GsgAsset:
    # Category when the metadata has no subtype: none (the sidebar already groups by type).
    fallback_category = ""

    def __init__(self, folder, data, files=None, kind_hint=""):
        self.folder = folder
        self.data = data
        self._files = files
        base = os.path.basename(folder.rstrip("/\\"))
        code = "_".join(str(data.get(k) or "") for k in ("productCode", "assetcode")).strip("_")
        self.id = code or base
        self.name = str(data.get("name") or base)
        self.kind = TYPE_KINDS.get(str(data.get("type") or "").lower(), kind_hint or "")
        subtype = str(data.get("subtype") or "").strip()
        self.categories = [subtype] if subtype else []
        raw = data.get("tags") or []
        tags = raw.split(",") if isinstance(raw, str) else list(raw)
        self.tags = [str(t).strip().lower() for t in tags if str(t).strip()]
        if subtype and subtype.lower() not in self.tags:
            self.tags.append(subtype.lower())
        try:
            self.physical_size = float(data.get("physicalsize") or 0)
        except (TypeError, ValueError):
            self.physical_size = 0.0

    @property
    def files(self):
        if self._files is None:
            self._files = megascans.list_files(self.folder)
        return self._files

    @property
    def content(self):
        """Asset files: no metadata, previews or OS clutter."""
        out = []
        for f in self.files:
            base = os.path.basename(f).lower()
            if base in IGNORED or base.endswith(META_EXTS) or _PREVIEW.search(base):
                continue
            out.append(f)
        return out

    @property
    def library_name(self):
        """'Cork Chips 03' + MC036_A001 -> Cork_Chips_03_MC036_A001."""
        return schema.sanitize_name("%s_%s" % (self.name, self.id))

    @property
    def category(self):
        return "/".join(schema.sanitize_name(c.lower()) for c in self.categories)

    @property
    def preview(self):
        for f in self.files:
            if _PREVIEW.search(os.path.basename(f)):
                return f
        return None

    def __repr__(self):
        return "<GsgAsset %s %s %s>" % (self.kind, self.id, self.name)


def read_asset(folder, names=None, kind_hint=None):
    """GsgAsset for a folder holding a .gsga/.gsgm metadata file, or None.

    kind_hint: the kind when the metadata has no known type; by default from the parent
    folder name (hdris, materials, textures, models)."""
    if names is None:
        try:
            names = os.listdir(folder)
        except OSError:
            return None
    if kind_hint is None:
        parent = os.path.basename(os.path.dirname(folder.rstrip("/\\"))).lower()
        kind_hint = KIND_FOLDERS.get(parent, "")
    for n in sorted(names):
        if not n.lower().endswith(META_EXTS):
            continue
        try:
            with open(os.path.join(folder, n), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and (data.get("productCode") or data.get("assetcode")
                                       or data.get("type")):
            return GsgAsset(folder, data, kind_hint=kind_hint)
    return None


def find_assets(paths):
    """GSG asset folders in `paths` (searched recursively). Returns (assets, other paths):
    other paths are the given files and folders that hold no GSG asset."""
    assets, other = [], []
    for p in paths:
        if not os.path.isdir(p):
            other.append(p)
            continue
        found = []
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            a = read_asset(dirpath, filenames)
            if a is not None:
                found.append(a)
                dirnames[:] = []          # don't descend into an asset folder
        if found:
            assets.extend(found)
        else:
            other.append(p)
    return assets, other


def looks_like_library(path):
    """True for a folder with GSG kind folders (hdris, materials, ...) whose asset folders
    hold a .gsga/.gsgm file. Checks a few asset folders of up to four kind folders."""
    try:
        kinds = [e for e in os.scandir(path) if e.is_dir() and e.name.lower() in KIND_FOLDERS]
    except OSError:
        return False
    for kind in kinds[:4]:
        try:
            samples = [e for e in os.scandir(kind.path) if e.is_dir()][:5]
        except OSError:
            continue
        for s in samples:
            if read_asset(s.path) is not None:
                return True
    return False


def _hdri(asset, info):
    files = [f for f in asset.content if schema.file_ext(f) in HDRI_ORDER]
    if not files:
        return []
    files.sort(key=lambda f: (HDRI_ORDER.index(schema.file_ext(f)), os.path.basename(f).lower()))
    return [("hdri", asset.library_name, {"hdri": files[0]}, dict(info), files[1:])]


def _material(asset, files, info):
    images = [f for f in files if schema.is_image(f) and schema.file_ext(f) != ".rat"]
    ts = megascans.texture_set(images, asset.library_name)
    if not ts:
        return []
    used = set(ts.channels.values())
    extra = [f for f in images if f not in used]      # anisotropy, sheen, scattering, ...
    minfo = dict(info, channels=sorted(ts.channels))
    res = megascans._resolution(ts.channels)
    if res:
        minfo["resolution"] = res
    if asset.physical_size > 0:
        minfo["physical_size"] = asset.physical_size
    return [("material", asset.library_name, ts.channels, minfo, extra)]


def _image(asset, info):
    images = [f for f in asset.content if schema.is_image(f) and schema.file_ext(f) != ".rat"]
    if not images:
        return []

    def size(f):
        try:
            return os.path.getsize(f)
        except OSError:
            return 0
    images.sort(key=lambda f: (-size(f), os.path.basename(f).lower()))   # largest = the gobo
    return [("image", asset.library_name, {"image": images[0]}, dict(info), images[1:])]


def plan(asset):
    """[(asset type, name, {key: file}, info, extra files)] for one GSG asset.

    HDRIs: the .exr (else .hdr). Materials: base color/roughness/normal/height...; maps the
    builders don't wire (anisotropy, sheen, scattering) are kept as extra files. Gobo
    textures: an image asset. Models: geometry, plus a material when there are textures."""
    info = {"gsg_id": asset.id}
    if asset.kind == "hdri":
        return _hdri(asset, info)
    if asset.kind == "material":
        return _material(asset, asset.content, info)
    if asset.kind == "texture":
        return _image(asset, info)
    if asset.kind == "model":
        out = []
        meshes = megascans.mesh_files(asset.content)
        if meshes:
            out.append(("geometry", asset.library_name, meshes, dict(info), []))
        return out + _material(asset, asset.content, info)
    return []


def records_for(asset, root, thumb_dir, thumbs, mtime):
    return megascans.build_records(asset, plan(asset), root, thumb_dir, thumbs, mtime,
                                   "gsg", "Greyscalegorilla")


def scan_root(root, cached=None, thumb_dir=""):
    """Records for every GSG asset under `root`, without writing anything there."""
    return megascans.scan_root(root, cached, thumb_dir, reader=read_asset,
                               recorder=records_for, kind_folders=KIND_FOLDERS)
