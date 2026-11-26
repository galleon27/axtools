"""Watch folders: browse a plain folder of models, USD components or textures in place.

No hou or Qt imports, so it is tested with plain Python. Unlike Megascans and GSG folders
there is no metadata file; the file names decide what an asset is:
    cat/Chair/Chair.usd geo.usd mtl.usd maps/...     one USD component
    props/Rock.abc Rock.bgeo.sc Rock_preview.jpg    geometry, formats grouped by name
    tex/Brick_basecolor.jpg Brick_roughness.jpg     texture sets become materials
A folder can hold several assets and asset folders can sit inside each other, so the scan
descends past a folder that also holds assets (megascans.scan_root's `descend` entry).

Ids use `folder:` as the prefix. Geometry and its model textures share one asset id, so
library.companion_keys and variants.linked_material link them like a vendor model.
"""
import hashlib
import os

from . import megascans, schema, textures

USD_EXTS = {".usd", ".usda", ".usdc", ".usdz"}
SUBLAYER_STEMS = {"geo", "mtl", "payload", "proxy", "sim_proxy", "base", "contrib",
                  "material", "materials"}
TEXTURE_DIRS = {"maps", "textures", "texture", "tex"}
NON_ASSET_DIRS = {"thumbs", "previews", ".trash"} | TEXTURE_DIRS
# Copied from publish.py, which imports hou.
THUMB_SUFFIXES = ("", "_preview", "_thumb", "_thumbnail", "_render")
THUMB_NAMES = {"preview", "thumb", "thumbnail"}
_IGNORED = {"thumbs.db", "desktop.ini", ".ds_store"}


class FolderAsset:
    """One folder that read_asset recognized: its id, its plan items and the drop hints."""

    tags = []
    category = ""
    kind = "folder"
    fallback_category = ""
    browse_category = None

    def __init__(self, folder, asset_id):
        self.folder = folder
        self.id = asset_id
        self.preview = None
        self.items = []        # [(asset type, name, {key: file}, info, extra files)]
        self.descend = False
        self.component = False
        self.single_geometry = False

    def __repr__(self):
        return "<FolderAsset %s %d item(s)>" % (self.id, len(self.items))


def _folder_id(folder):
    base = os.path.basename(folder.rstrip("/\\")) or "folder"
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8"))
    return schema.sanitize_name(base) + "_" + digest.hexdigest()[:8]


def _stem(path):
    return schema.strip_ext(path)


def _own_id(info, asset, name, with_models):
    """Id keys of an item: it shares the folder's id with the models (linked, hidden as a
    companion) or gets an id of its own (listed on its own, never linked)."""
    if with_models:
        info["folder_id"] = asset.id
    else:
        info["asset_id"] = info["folder_id"] = "%s_%s" % (asset.id, schema.sanitize_name(name))


def _unique_variations(items):
    """Give every item of a repeated type its own variation (the record id suffix)."""
    counts = {}
    for asset_type, _n, _f, _i, _e in items:
        counts[asset_type] = counts.get(asset_type, 0) + 1
    used = {}
    for asset_type, name, _files, info, _extra in items:
        if counts[asset_type] < 2 or info.get("variation"):
            continue
        base = schema.sanitize_name(name)
        variation, n = base, 2
        while (asset_type, variation) in used:
            variation = "%s_%d" % (base, n)
            n += 1
        used[(asset_type, variation)] = True
        info["variation"] = variation


def read_asset(folder, names=None, hint=None):
    """FolderAsset for a folder holding models, a USD component or textures, or None."""
    if names is None:
        try:
            names = os.listdir(folder)
        except OSError:
            return None
    files, dirs = [], []
    for n in names:
        if n.startswith("."):
            continue
        path = os.path.join(folder, n)
        if os.path.isdir(path):
            dirs.append(n)
        elif os.path.isfile(path):
            files.append(path)
    files.sort()
    base = os.path.basename(folder.rstrip("/\\"))

    asset = FolderAsset(folder, _folder_id(folder))
    all_images = [f for f in files
                  if schema.is_image(f) and os.path.basename(f).lower() not in _IGNORED]
    usd = [f for f in files if schema.file_ext(f) in USD_EXTS]
    short_usd = [f for f in usd if schema.strip_ext(f).lower() in SUBLAYER_STEMS]

    # 1. USD component: a USD file named like its folder (Component Builder layout).
    entry = next((f for f in usd if schema.strip_ext(f).lower() == base.lower()), None)
    used_usd = set(short_usd)
    if entry is not None:
        asset.component = True
        used_usd.add(entry)
        asset.items.append(("geometry", base, {schema.format_key(entry): entry},
                            {"folder_id": asset.id, "component_dir": folder}, []))

    # 2. Geometry, formats with the same stem grouped into one asset.
    groups = {}
    for f in files:
        if not schema.is_geometry(f) or f in used_usd:
            continue
        groups.setdefault(schema.strip_ext(f).lower(), []).append(f)
    for stem_low, group in groups.items():
        name = _stem(group[0])
        asset.items.append(("geometry", name,
                            {schema.format_key(f): f for f in group},
                            {"folder_id": asset.id}, []))

    # 3/4. Images: texture sets, HDRIs and single images.
    geo_stems = [_stem(f).lower() for _k, _n, fl, _i, _e in asset.items
                 for f in fl.values()]
    thumb = _find_thumb(all_images, geo_stems, len(asset.items) == 1)
    used_images = {thumb} if thumb else set()
    images = [f for f in all_images if f not in used_images]
    sets, leftovers, _previews = textures.group(images)
    geo_items = [item for item in asset.items if item[0] == "geometry"]
    geo_names = {item[1].lower() for item in geo_items}
    for ts in sets:
        # A texture set belongs to a model when it is named like it, or when it is the only
        # set next to the only model; any other set is its own asset.
        mine = ts.name.lower() in geo_names or (len(geo_items) == 1 and len(sets) == 1)
        info = {"channels": sorted(ts.channels)}
        _own_id(info, asset, ts.name, mine)
        res = megascans._resolution(ts.channels)
        if res:
            info["resolution"] = res
        if ts.preview:
            info["preview"] = ts.preview
        asset.items.append(("material", ts.name, ts.channels, info, ts.extra))

    # 4. Model textures: a maps/ (textures/) subfolder belongs to the geometry above.
    if groups or entry is not None:
        for d in sorted(dirs, key=str.lower):
            if d.lower() not in TEXTURE_DIRS:
                continue
            sub = os.path.join(folder, d)
            try:
                sub_names = os.listdir(sub)
            except OSError:
                continue
            sub_images = [os.path.join(sub, n) for n in sorted(sub_names)
                          if schema.is_image(os.path.join(sub, n))
                          and os.path.isfile(os.path.join(sub, n))]
            sub_sets, _left, _prev = textures.group(sub_images)
            if not sub_sets:
                continue
            ts = sub_sets[0]
            info = {"channels": sorted(ts.channels), "folder_id": asset.id}
            res = megascans._resolution(ts.channels)
            if res:
                info["resolution"] = res
            asset.items.append(("material", ts.name, ts.channels, info, ts.extra))
            break

    for p in leftovers:
        kind = "hdri" if textures.looks_like_hdri(p) else "image"
        info = {}
        _own_id(info, asset, _stem(p), False)
        asset.items.append((kind, _stem(p), {kind: p}, info, []))

    if not asset.items:
        return None

    _unique_variations(asset.items)

    # 5. A thumbnail image is a preview, not an asset.
    if thumb:
        if len(asset.items) == 1:
            asset.preview = thumb
        else:
            matched = False
            for _k, _n, files_of, info, _e in asset.items:
                if _thumb_matches(thumb, [schema.strip_ext(f).lower() for f in files_of.values()]):
                    info["preview"] = thumb
                    matched = True
                    break
            geos = [item for item in asset.items if item[0] == "geometry"]
            if not matched and len(geos) == 1:   # props/thumbnail.png previews the model
                geos[0][3]["preview"] = thumb

    asset.single_geometry = sum(1 for item in asset.items if item[0] == "geometry") == 1
    # 6. Keep walking when plain subfolders hold more assets.
    asset.descend = bool([d for d in dirs if d.lower() not in NON_ASSET_DIRS]) \
        and not asset.component
    return asset


def _thumb_matches(thumb, stems):
    stem = schema.strip_ext(thumb).lower()
    return any(stem == s + suffix for s in stems for suffix in THUMB_SUFFIXES)


def _find_thumb(images, geo_stems, single):
    """The image that previews the folder's geometry, or None."""
    if not geo_stems:
        return None
    for f in images:
        if _thumb_matches(f, geo_stems):
            return f
    if single:
        for f in images:
            if schema.strip_ext(f).lower() in THUMB_NAMES:
                return f
    return None


def plan(asset):
    return asset.items


def records_for(asset, root, thumb_dir, thumbs, mtime):
    """AssetRecords for one watch folder, with the folder's path as the category."""
    folder = asset.folder
    rel_dir = os.path.dirname(folder.rstrip("/\\")) if (asset.component or asset.single_geometry) \
        else folder
    try:
        rel = os.path.relpath(rel_dir, root)
    except ValueError:
        rel = ""
    if rel.startswith(".."):        # the root itself holds the asset
        rel = ""
    asset.browse_category = "" if rel in ("", ".") else rel.replace("\\", "/")
    return megascans.build_records(asset, plan(asset), root, thumb_dir, thumbs, mtime,
                                   "folder", "Folder")


def looks_like_library(path):
    """True for a plain folder of models, USD components or textures.

    False for a Megascans or GSG download and for an asset library root (asset.json files
    below it), so Settings picks the right root kind."""
    if megascans.looks_like_download(path) or _looks_like_gsg(path):
        return False
    seen = 0
    try:
        walker = os.walk(path)
        for dirpath, dirnames, filenames in walker:
            if os.path.normpath(dirpath) == os.path.normpath(path):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")
                               and d.lower() not in NON_ASSET_DIRS]
            else:
                if schema.SIDECAR in filenames:
                    return False
                rel = os.path.relpath(dirpath, path)
                if len(rel.split(os.sep)) >= 3:
                    dirnames[:] = []
                else:
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")
                                   and d.lower() not in NON_ASSET_DIRS]
            seen += 1
            if seen > 200:
                return False
            if read_asset(dirpath, filenames) is not None:
                return True
    except OSError:
        return False
    return False


def _looks_like_gsg(path):
    from . import gsg
    return gsg.looks_like_library(path)


def scan_root(root, cached=None, thumb_dir="", progress=None, on_batch=None, cancelled=None):
    """Records for every watch-folder asset under `root`, without writing anything there."""
    return megascans.scan_root(root, cached, thumb_dir, reader=read_asset,
                               recorder=records_for, kind_folders={},
                               skip_dirs=NON_ASSET_DIRS, include_root=True, progress=progress,
                               on_batch=on_batch, cancelled=cancelled)
