"""Add assets to the library: publish the selected node, ingest files from disk,
versions and thumbnails.
"""
import getpass
import os
import shutil
import tempfile

import hou

from . import (gsg, karma, library, material_bundle, megascans, schema, textures,
               thumbnails, variants)

# Files copied along with a geometry file of the same name (OBJ materials).
COMPANION_EXTS = {".obj": (".mtl",)}
THUMB_SUFFIXES = ("", "_preview", "_thumb", "_thumbnail", "_render")
THUMB_NAMES = {"preview", "thumb", "thumbnail"}


class IngestReport:
    def __init__(self):
        self.created = []   # (name, asset_dir)
        self.skipped = []   # (path, reason)
        self.failed = []    # (path, reason)
        self.warnings = []  # messages, each once
        self.created_types = {}  # asset_dir -> asset type

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    @property
    def summary(self):
        parts = ["%d added" % len(self.created)]
        if self.skipped:
            parts.append("%d skipped" % len(self.skipped))
        if self.failed:
            parts.append("%d failed" % len(self.failed))
        return ", ".join(parts)


def author():
    try:
        return getpass.getuser()
    except Exception:
        return ""


def check_writable(root):
    if root.get("read_only"):
        raise hou.OperationFailed("%s is marked read-only in the settings." % root["path"])
    path = root["expanded"]
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError as e:
            raise hou.OperationFailed("Can't create library root %s: %s" % (path, e))
    if not os.access(path, os.W_OK):
        raise hou.OperationFailed("Library root %s is not writable." % path)


def clean_category(category):
    parts = [schema.sanitize_name(p) for p in category.replace("\\", "/").split("/") if p.strip()]
    return "/".join(parts)


def asset_dir_for(root, asset_type, category, name):
    parts = [root["expanded"], schema.TYPE_FOLDERS[asset_type]]
    parts += category.split("/") if category else []
    return os.path.join(*(parts + [name]))


# --- collecting dropped files -------------------------------------------------------
def collect(paths):
    """Split files and folders (searched recursively) into (geometry, images, other)."""
    geo, images, other = [], [], []
    seen = set()

    def add(p):
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        seen.add(key)
        if schema.is_geometry(p):
            geo.append(p)
        elif schema.is_image(p):
            images.append(p)
        else:
            other.append(p)

    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in sorted(filenames):
                    add(os.path.join(dirpath, fn))
        elif os.path.isfile(p):
            add(p)
    return geo, images, other


def group_geometry(files):
    """Group files that share folder + name (Rock.abc, Rock.bgeo.sc) into one asset.

    Returns [(name, [files])] in input order.
    """
    groups, order = {}, []
    for f in files:
        key = (os.path.normcase(os.path.dirname(os.path.abspath(f))), schema.strip_ext(f).lower())
        if key not in groups:
            groups[key] = (schema.strip_ext(f), [])
            order.append(key)
        groups[key][1].append(f)
    return [groups[k] for k in order]


def find_thumbnail(geo_file, images, single_in_folder):
    """Image next to a geometry file that looks like its preview, or None."""
    folder = os.path.normcase(os.path.dirname(os.path.abspath(geo_file)))
    stem = schema.strip_ext(geo_file).lower()
    local = [i for i in images if os.path.normcase(os.path.dirname(os.path.abspath(i))) == folder]
    wanted = {stem + s for s in THUMB_SUFFIXES}
    for img in local:
        if schema.strip_ext(img).lower() in wanted:
            return img
    if single_in_folder:
        for img in local:
            if schema.strip_ext(img).lower() in THUMB_NAMES:
                return img
    return None


# --- geometry info ------------------------------------------------------------------
def geometry_info(path):
    """Bounds and counts read with hou.Geometry. Raises hou.Error if the file won't load."""
    geo = hou.Geometry()
    geo.loadFromFile(path)
    bbox = geo.boundingBox()
    info = {
        "bounds": [list(bbox.minvec()), list(bbox.maxvec())],
        "pointcount": int(geo.intrinsicValue("pointcount")),
        "polycount": int(geo.intrinsicValue("primitivecount")),
    }
    return info


# --- ingest plan ------------------------------------------------------------------------
class IngestItem:
    """One asset to create: type, name, {key: source file}, optional thumbnail source."""

    def __init__(self, asset_type, name, files, thumb=None, extra=(), info=None,
                 category="", tags=(), source=None, tree=None):
        self.type = asset_type
        self.name = schema.sanitize_name(name)
        self.files = dict(files)
        self.thumb = thumb
        self.extra = list(extra)        # copied along, not recorded as version files
        self.info = dict(info or {})
        self.category = category        # sub-category under the one chosen in the dialog
        self.tags = list(tags)
        self.source = dict(source or {"kind": "ingest"})
        self.tree = tree                # folder copied whole, keeping relative paths

    @property
    def first(self):
        return next(iter(self.files.values()))

    def __repr__(self):
        return "<IngestItem %s %s %s>" % (self.type, self.name, sorted(self.files))


class IngestPlan:
    def __init__(self):
        self.items = []
        self.skipped = []   # (path, reason)
        self.megascans = 0  # number of Megascans asset folders found
        self.gsg = 0        # number of Greyscalegorilla asset folders found

    def by_type(self, asset_type):
        return [i for i in self.items if i.type == asset_type]

    def counts(self):
        out = {}
        for i in self.items:
            out[i.type] = out.get(i.type, 0) + 1
        return out


def image_size(path):
    """(w, h) of an image file, or None when Houdini can't read it."""
    try:
        res = hou.imageResolution(path)
    except hou.Error:
        return None
    return tuple(res) if res and res[0] > 0 else None


def plan_ingest(paths):
    """Decide which assets dropped files and folders make, without writing anything.

    Geometry files group by folder + name; image files form texture sets (materials) by
    channel names; other HDR files with a 2:1 aspect are HDRIs; the rest are images.
    Preview images become thumbnails instead of assets.
    """
    plan = IngestPlan()
    ms_assets, paths = megascans.find_assets(paths)
    plan.megascans = len(ms_assets)
    for asset in ms_assets:
        made = megascans.plan(asset)
        if not made:
            plan.skipped.append((asset.folder, "Megascans %s asset: nothing to import"
                                 % (asset.kind or "unknown")))
        for asset_type, name, files, info, extra in made:
            plan.items.append(IngestItem(
                asset_type, name, files, asset.preview, extra=extra, info=info,
                category=asset.category, tags=asset.tags,
                source={"kind": "megascans", "megascans_id": asset.id,
                        "origin_path": asset.folder.replace("\\", "/")}))
    gsg_assets, paths = gsg.find_assets(paths)
    plan.gsg = len(gsg_assets)
    for asset in gsg_assets:
        made = gsg.plan(asset)
        if not made:
            plan.skipped.append((asset.folder, "Greyscalegorilla %s asset: nothing to import"
                                 % (asset.kind or "unknown")))
        for asset_type, name, files, info, extra in made:
            plan.items.append(IngestItem(
                asset_type, name, files, asset.preview, extra=extra, info=info,
                category=asset.category, tags=asset.tags,
                source={"kind": "gsg", "gsg_id": asset.id,
                        "origin_path": asset.folder.replace("\\", "/")}))
    geo, images, other = collect(paths)
    companions = {os.path.normcase(os.path.splitext(os.path.abspath(g))[0] + ext)
                  for g in geo for ext in COMPANION_EXTS.get(schema.file_ext(g), ())}
    for p in other:
        if os.path.normcase(os.path.abspath(p)) not in companions:
            plan.skipped.append((p, "not a geometry or image file"))

    groups = group_geometry(geo)
    per_folder = {}
    for _, files in groups:
        d = os.path.normcase(os.path.dirname(os.path.abspath(files[0])))
        per_folder[d] = per_folder.get(d, 0) + 1
    used_images = set()
    for stem, files in groups:
        folder = os.path.normcase(os.path.dirname(os.path.abspath(files[0])))
        thumb = find_thumbnail(files[0], images, per_folder[folder] == 1)
        if thumb:
            used_images.add(os.path.normcase(os.path.abspath(thumb)))
        plan.items.append(IngestItem("geometry", stem,
                                     {schema.format_key(f): f for f in files}, thumb))

    rest = [i for i in images if os.path.normcase(os.path.abspath(i)) not in used_images]
    sets, leftovers, previews = textures.group(rest)
    for ts in sets:
        info = {"channels": sorted(ts.channels)}
        height = textures.megascans_height(ts.folder)
        if height is not None:
            info["displacement_scale"] = height
        plan.items.append(IngestItem("material", ts.name, ts.channels, ts.preview,
                                     extra=ts.extra, info=info))
    for p in previews:
        plan.skipped.append((p, "preview image without an asset"))
    for p in leftovers:
        size = image_size(p) if schema.file_ext(p) in textures.HDR_EXTS else None
        kind = "hdri" if textures.looks_like_hdri(p, size) else "image"
        info = {"resolution": list(size)} if size else {}
        plan.items.append(IngestItem(kind, schema.strip_ext(p), {kind: p}, info=info))
    return plan


def plan_from_records(recs, records_all):
    """IngestPlan that copies browsed vendor assets (watch folders, Megascans, GSG) into the
    library, keeping their ids and a geometry record's linked material.

    A USD component's whole folder is copied (`tree`), so its sublayers and textures stay
    relative to each other."""
    plan = IngestPlan()
    seen = set()
    for rec in recs:
        if not library.browsed_in_place(rec):
            continue
        linked = variants.linked_material(rec, records_all) \
            if rec.type == "geometry" else None
        for item_rec in [rec] + ([linked] if linked is not None else []):
            if item_rec.key in seen:
                continue
            seen.add(item_rec.key)
            info = {k: v for k, v in (item_rec.info or {}).items()
                    if k not in ("preview", "base_tags", "base_description",
                                 "component_dir", "asset_id")}
            thumb = item_rec.thumb if item_rec.thumb and os.path.isfile(item_rec.thumb) \
                else None
            plan.items.append(IngestItem(
                item_rec.type, item_rec.name, item_rec.files, thumb=thumb, info=info,
                category=item_rec.category, tags=item_rec.tags,
                source={"kind": item_rec.source_kind, "origin_path": item_rec.asset_dir},
                tree=(item_rec.info or {}).get("component_dir")))
    return plan


# --- writing assets ---------------------------------------------------------------------
def _write_asset(root, category, item, tags, copy, read_info, report):
    category = "/".join(c for c in (category, clean_category(item.category)) if c)
    asset_dir = asset_dir_for(root, item.type, category, item.name)
    if os.path.exists(asset_dir):
        report.skipped.append((item.first, "%s already exists in the library" % item.name))
        return None
    os.makedirs(asset_dir)
    try:
        _fill_asset(asset_dir, category, item, tags, copy, read_info, report)
    except BaseException:
        shutil.rmtree(asset_dir, ignore_errors=True)  # no half-written assets in the library
        raise
    report.created.append((item.name, asset_dir))
    report.created_types[asset_dir] = item.type
    return asset_dir


def _copy_into(vdir, src, used):
    """Copy src into vdir; a second file with the same name gets a numeric suffix."""
    base = os.path.basename(src)
    name, n = base, 1
    while name.lower() in used:
        stem, ext = schema.strip_ext(base), schema.file_ext(base)
        name = "%s_%d%s" % (stem, n, ext)
        n += 1
    used.add(name.lower())
    dst = os.path.join(vdir, name)
    shutil.copy2(src, dst)
    return dst


def _fill_asset(asset_dir, category, item, tags, copy, read_info, report):
    vdir = os.path.join(asset_dir, schema.version_label(1))
    os.makedirs(vdir)
    data = schema.new_asset(item.name, item.type, category=category,
                            tags=list(tags) + item.tags, author=author())
    data["source"].update(origin_path=item.first.replace("\\", "/"))
    data["source"].update(item.source)
    # A USD component is copied with its sublayers and textures, so relative paths survive.
    if copy and item.tree:
        shutil.copytree(item.tree, vdir, dirs_exist_ok=True)

    version_files, sizes, used = {}, {}, set()
    for key, f in item.files.items():
        if copy and item.tree:
            dst = os.path.join(vdir, os.path.relpath(os.path.abspath(f),
                                                     os.path.abspath(item.tree)))
        elif copy:
            dst = _copy_into(vdir, f, used)
            for ext in COMPANION_EXTS.get(schema.file_ext(f), ()):
                comp = os.path.splitext(f)[0] + ext
                if os.path.isfile(comp):
                    _copy_into(vdir, comp, used)
        else:
            dst = os.path.abspath(f)
        version_files[key] = schema.relpath(asset_dir, dst)
        sizes[key] = os.path.getsize(dst)
    extra = []
    for f in item.extra:
        dst = _copy_into(vdir, f, used) if copy else os.path.abspath(f)
        extra.append(schema.relpath(asset_dir, dst))
    schema.add_version(data, version_files, note="ingested", author=data["author"])
    data["info"].update(item.info)
    data["info"]["file_sizes"] = sizes
    data["info"]["formats"] = sorted({schema.format_key(f) for f in item.files.values()})
    if extra:
        data["info"]["extra_files"] = extra

    if item.type == "geometry" and read_info:
        try:
            data["info"].update(geometry_info(item.first))
            if "import_scale" in data["info"]:
                data["info"]["import_scale"] = megascans.check_scale(data["info"])
        except hou.Error as e:
            report.warn("%s: could not read geometry info (%s)" % (item.name, e.instanceMessage()))
    elif item.type == "material":
        size = image_size(item.files.get("basecolor") or item.first)
        if size:
            data["info"]["resolution"] = list(size)

    # Thumbnail: the preview image, or for images/HDRIs the file itself.
    thumb_src = item.thumb or (item.first if item.type in ("image", "hdri") else None)
    if thumb_src:
        try:
            thumbnails.make_thumbnail(thumb_src, os.path.join(asset_dir, schema.THUMB))
            data["thumbnail"].update(file=schema.THUMB, method="image", generated=schema.now_iso())
        except Exception as e:  # a bad preview image must not fail the ingest
            report.warn("%s: thumbnail failed (%s)" % (item.name, e))

    schema.save(os.path.join(asset_dir, schema.SIDECAR), data)


def ingest(plan, root, category="", tags=None, copy=True, read_info=True, types=None):
    """Write the assets of an IngestPlan under `root` (a prefs root dict).

    `types` limits which asset types are written (None = all).
    """
    check_writable(root)
    category = clean_category(category)
    tags = schema.clean_tags(tags or [])
    report = IngestReport()
    report.skipped.extend(plan.skipped)
    items = [i for i in plan.items if types is None or i.type in types]
    with hou.InterruptableOperation("Adding assets to the library",
                                    open_interrupt_dialog=len(items) > 3) as op:
        for n, item in enumerate(items):
            try:
                _write_asset(root, category, item, tags, copy, read_info, report)
            except OSError as e:
                report.failed.append((item.first, str(e)))
            try:
                op.updateProgress(float(n + 1) / len(items))
            except hou.OperationInterrupted:
                report.warn("Stopped by user after %d of %d." % (n + 1, len(items)))
                break
    return report


def ingest_geometry(paths, root, category="", tags=None, copy=True, read_info=True):
    """Geometry-only ingest (kept for scripts written against phase 1)."""
    return ingest(plan_ingest(paths), root, category, tags, copy, read_info, types={"geometry"})


def set_thumbnail(rec, image_path, read_only=False):
    """Replace an asset's thumbnail with a square crop of `image_path`."""
    target = megascans_thumb(rec)
    if target is not None:
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            thumbnails.make_thumbnail(image_path, target)
        except (thumbnails.ThumbnailError, OSError) as e:
            raise hou.OperationFailed(str(e))
        return
    if read_only:
        raise hou.OperationFailed("%s is in a read-only library root." % rec.name)
    try:
        thumbnails.store_thumbnail(rec.asset_dir, image_path, "image")
    except thumbnails.ThumbnailError as e:
        raise hou.OperationFailed(str(e))


def set_current_version(rec, number, read_only=False):
    """Make an existing version the one that imports by default."""
    if read_only:
        raise hou.OperationFailed("%s is in a read-only library root." % rec.name)
    sidecar = os.path.join(rec.asset_dir, schema.SIDECAR)
    data = schema.load(sidecar)
    try:
        schema.set_current(data, number)
    except ValueError as e:
        raise hou.OperationFailed(str(e))
    schema.save(sidecar, data)


# --- publish the selected node ----------------------------------------------------------
PUBLISH_FORMATS = ("bgeo.sc", "usd", "abc")  # bgeo.sc is always written


class PublishResult:
    def __init__(self, asset_dir, version, created, geo_file, bounds):
        self.asset_dir, self.version, self.created = asset_dir, version, created
        self.geo_file, self.bounds = geo_file, bounds
        self.warnings = []


def publishable_node(nodes=None):
    """Last selected SOP node or Geometry object, or None."""
    nodes = hou.selectedNodes() if nodes is None else nodes
    for node in reversed(nodes):
        cat = node.type().category()
        if cat == hou.sopNodeTypeCategory():
            return node
        if cat == hou.objNodeTypeCategory() and hasattr(node, "displayNode") and node.displayNode():
            return node
    return None


def default_name(node):
    """Asset name suggestion: the Geometry object's name for SOPs named like 'output0'."""
    name = node.name()
    if node.type().category() == hou.sopNodeTypeCategory() and \
            (name.lower().startswith(("output", "out", "null", "file")) or name[-1:].isdigit()):
        parent = node.parent()
        if parent is not None and parent.type().category() == hou.objNodeTypeCategory():
            name = parent.name()
    return schema.sanitize_name(name)


def node_geometry(node):
    """Cooked geometry of a SOP, or of a Geometry object's display SOP (object space)."""
    sop = node
    if node.type().category() == hou.objNodeTypeCategory():
        sop = node.displayNode()
    if sop is None or sop.type().category() != hou.sopNodeTypeCategory():
        raise hou.OperationFailed("Select a SOP node or a Geometry object.")
    geo = sop.geometry()
    if geo is None:
        errors = "; ".join(sop.errors()) or "it does not cook"
        raise hou.OperationFailed("No geometry from %s: %s" % (sop.path(), errors))
    return geo


def prepare_geometry(src, center=False, ground=False, unpack=False):
    """Copy of `src`, optionally unpacked, centered on X/Z and moved to Y=0."""
    geo = hou.Geometry()
    geo.merge(src)
    if unpack:
        verb = hou.sopNodeTypeCategory().nodeVerb("unpack")
        out = hou.Geometry()
        verb.execute(out, [geo])
        geo = out
    if center or ground:
        bb = geo.boundingBox()
        c = bb.center()
        t = hou.Vector3(-c[0] if center else 0.0, -bb.minvec()[1] if ground else 0.0,
                        -c[2] if center else 0.0)
        geo.transform(hou.hmath.buildTranslate(t))
    return geo


def existing_asset(root, category, name):
    """Sidecar data of the asset at that place, or None."""
    sidecar = os.path.join(asset_dir_for(root, "geometry", clean_category(category),
                                         schema.sanitize_name(name)), schema.SIDECAR)
    if not os.path.isfile(sidecar):
        return None
    try:
        return schema.load(sidecar)
    except schema.SidecarError:
        return None


def publish_node(node, root, name, category="", tags=None, description="", note="",
                 formats=("bgeo.sc",), center=False, ground=False, unpack=False,
                 thumbnail_image=None, asset_dir=None, materials=False):
    """Save a node's geometry as a new asset, or as a new version of the asset of that name.

    `asset_dir` targets an existing asset folder directly (new version of a known asset).
    Returns a PublishResult. Raises hou.OperationFailed when nothing could be saved.
    """
    check_writable(root)
    name = schema.sanitize_name(name)
    category = clean_category(category)
    if asset_dir is None:
        asset_dir = asset_dir_for(root, "geometry", category, name)
    elif not os.path.isfile(os.path.join(asset_dir, schema.SIDECAR)):
        raise hou.OperationFailed("No asset.json in %s." % asset_dir)
    sidecar = os.path.join(asset_dir, schema.SIDECAR)
    created = not os.path.exists(sidecar)
    if created:
        if os.path.exists(asset_dir):
            raise hou.OperationFailed("%s exists but has no asset.json." % asset_dir)
        data = schema.new_asset(name, "geometry", category=category, tags=tags,
                                description=description, author=author())
    else:
        try:
            data = schema.load(sidecar)
        except schema.SidecarError as e:
            raise hou.OperationFailed(str(e))
        if data.get("type") != "geometry":
            raise hou.OperationFailed("%s is a %s asset, not geometry." % (name, data.get("type")))
        data["tags"] = schema.clean_tags(data["tags"] + schema.clean_tags(tags or []))
        if description:
            data["description"] = description

    geo = prepare_geometry(node_geometry(node), center, ground, unpack)
    number = schema.next_version(data)
    vdir = os.path.join(asset_dir, schema.version_label(number))
    if os.path.exists(vdir):
        raise hou.OperationFailed("%s already exists; check the asset folder." % vdir)

    os.makedirs(vdir)
    result = None
    try:
        files, sizes, warnings = {}, {}, []
        materials_info = None
        if materials:
            sop = node
            if node.type().category() == hou.objNodeTypeCategory():
                sop = node.displayNode()
            materials_info, mat_warnings, rename = material_bundle.write(vdir, sop, geo)
            warnings.extend(mat_warnings)
            if materials_info:
                material_bundle.rewrite_attr(geo, rename)
        for fmt in ["bgeo.sc"] + [f for f in formats if f != "bgeo.sc"]:
            path = os.path.join(vdir, "%s.%s" % (name, fmt))
            try:
                geo.saveToFile(path)
            except hou.Error as e:
                if fmt == "bgeo.sc":
                    raise hou.OperationFailed("Could not write %s: %s" % (path, e.instanceMessage()))
                warnings.append("%s not written: %s" % (fmt, e.instanceMessage()))
                continue
            files[fmt] = schema.relpath(asset_dir, path)
            sizes[fmt] = os.path.getsize(path)

        if materials_info:
            # The sidecar stores paths relative to the asset folder, not the version folder.
            label = schema.version_label(number)
            materials_info = {
                "file": label + "/" + materials_info["file"],
                "names": list(materials_info["names"]),
                "textures": [label + "/" + p for p in materials_info["textures"]],
            }
        schema.add_version(data, files, note=note, author=author(), number=number,
                           materials=materials_info)
        bb = geo.boundingBox()
        bounds = [list(bb.minvec()), list(bb.maxvec())]
        data["info"].update(bounds=bounds, file_sizes=sizes, formats=sorted(files),
                            pointcount=int(geo.intrinsicValue("pointcount")),
                            polycount=int(geo.intrinsicValue("primitivecount")))
        hip = hou.hipFile.path().replace("\\", "/")
        data["source"].update(kind="publish", origin_path="%s:%s" % (hip, node.path()))
        schema.save(sidecar, data)
        result = PublishResult(asset_dir, number, created,
                               schema.resolve(asset_dir, files["bgeo.sc"]), bounds)
        result.warnings = warnings
    except BaseException:
        shutil.rmtree(asset_dir if created else vdir, ignore_errors=True)
        raise

    if thumbnail_image:
        try:
            thumbnails.store_thumbnail(asset_dir, thumbnail_image, "image")
        except Exception as e:  # the asset is saved; only the picture failed
            result.warnings.append("thumbnail failed: %s" % e)
    return result


# --- Karma thumbnails -------------------------------------------------------------------
RENDERABLE_TYPES = schema.RENDERABLE_TYPES


def _queue(asset_dir, name, workdir, writer, settings, key=None, target=None):
    queue = thumbnails.karma_queue()
    queue.renderer = settings.get("thumb_renderer")
    queue.timeout_s = settings.get("thumb_timeout")
    res, samples = settings.get("thumb_resolution"), settings.get("thumb_samples")
    key = key or library.norm_path(os.path.join(asset_dir, schema.SIDECAR))
    return queue.add(key, name, workdir, lambda stage, image: writer(stage, image, res, samples),
                     asset_dir=None if target else asset_dir, target=target)


def megascans_thumb(rec):
    """Thumbnail file of a record from a Megascans or GSG root (in the prefs cache), else None."""
    if not library.browsed_in_place(rec):
        return None
    from . import prefs
    return megascans.thumb_path(library.megascans_thumb_dir(prefs.cache_dir()), rec.id)


THUMB_RESOLUTION = "2K"    # texture set used when a thumbnail shows a material on geometry


def _thumb_maps(maps):
    """The channels a thumbnail material wires (no displacement), with existing files."""
    return {k: v for k, v in (maps or {}).items()
            if k in textures.WIRED and k != "displacement" and v and os.path.isfile(v)}


def linked_material_maps(rec, records):
    """{channel: file} of the material linked to a geometry record, at THUMB_RESOLUTION (or
    the nearest lower set), or {} when it has none."""
    mrec = variants.linked_material(rec, records or [])
    if mrec is None:
        return {}
    channels = mrec.files_for(mrec.current)
    sets = variants.resolution_sets(channels)
    picked = variants.pick_resolution(list(sets), THUMB_RESOLUTION)
    return _thumb_maps(sets[picked] if picked is not None else channels)


def render_thumbnail(asset_dir, name, geo_file, bounds, settings, key=None, target=None,
                     maps=None):
    """Queue a Karma thumbnail render for a geometry file. Returns True when queued.

    Formats husk can't read as a layer (obj, fbx, ply) are converted to bgeo.sc first,
    and bounds are read from the file when the sidecar has none. `maps` ({channel: file})
    is the material shown on the geometry.
    """
    maps = _thumb_maps(maps)
    workdir = tempfile.mkdtemp(prefix="ax_thumb_")
    try:
        layer = geo_file
        if karma.needs_conversion(geo_file) or not karma.is_renderable(bounds):
            geo = hou.Geometry()
            geo.loadFromFile(geo_file)
            bb = geo.boundingBox()
            bounds = [list(bb.minvec()), list(bb.maxvec())]
            if karma.needs_conversion(geo_file):
                layer = os.path.join(workdir, "asset.bgeo.sc")
                geo.saveToFile(layer)
        if not karma.is_renderable(bounds):
            raise hou.OperationFailed("%s has no geometry to render." % name)
        return _queue(asset_dir, name, workdir,
                      lambda st, im, r, sm: karma.write_stage(st, layer, bounds, im, r, sm,
                                                              maps=maps),
                      settings, key, target)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def render_material_thumbnail(asset_dir, name, maps, settings, key=None, target=None):
    """Queue a shader-ball render. `maps` = {channel: absolute file}."""
    maps = _thumb_maps(maps)
    if not maps:
        raise hou.OperationFailed("%s has no texture channels to render." % name)
    workdir = tempfile.mkdtemp(prefix="ax_thumb_")
    try:
        return _queue(asset_dir, name, workdir,
                      lambda st, im, r, sm: karma.write_material_stage(st, maps, im, r, sm),
                      settings, key, target)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def render_hdri_thumbnail(asset_dir, name, hdri_file, settings, key=None, target=None):
    workdir = tempfile.mkdtemp(prefix="ax_thumb_")
    try:
        return _queue(asset_dir, name, workdir,
                      lambda st, im, r, sm: karma.write_hdri_stage(st, hdri_file, im, r, sm),
                      settings, key, target)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def render_asset_thumbnail(asset_type, asset_dir, name, files, info, settings, key=None,
                           target=None, material_maps=None):
    """Queue the Karma thumbnail that fits the asset type. `files` = {key: absolute path};
    `material_maps` ({channel: file}) is shown on geometry."""
    missing = [k for k, p in files.items() if not os.path.isfile(p)]
    if asset_type == "geometry":
        order = [f for f in settings.get("geo_format_order") if f in karma.LAYER_FORMATS]
        fkey = schema.pick_file({k: p for k, p in files.items() if k not in missing},
                                order + list(settings.get("geo_format_order")))
        if not fkey:
            raise hou.OperationFailed("%s: geometry file not found." % name)
        return render_thumbnail(asset_dir, name, files[fkey], (info or {}).get("bounds"),
                                settings, key, target, maps=material_maps)
    if asset_type == "material":
        return render_material_thumbnail(
            asset_dir, name, {k: p for k, p in files.items() if k not in missing}, settings,
            key, target)
    if asset_type == "hdri":
        if "hdri" in missing or "hdri" not in files:
            raise hou.OperationFailed("%s: HDRI file not found." % name)
        return render_hdri_thumbnail(asset_dir, name, files["hdri"], settings, key, target)
    raise hou.OperationFailed("%s: Karma thumbnails are for geometry, materials and HDRIs; "
                              "images use the image itself." % name)


def render_record_thumbnail(rec, settings, records=None):
    """Queue a record's thumbnail. Geometry shows its linked material, looked up in
    `records` (the panel's records)."""
    target = megascans_thumb(rec)   # Megascans roots: thumbnail goes to the prefs cache
    root = settings.root_for(rec.root)
    if target is None and root and root["read_only"]:
        raise hou.OperationFailed("%s is in a read-only library root." % rec.name)
    maps = linked_material_maps(rec, records) if rec.type == "geometry" else None
    return render_asset_thumbnail(rec.type, rec.asset_dir, rec.name, rec.files, rec.info,
                                  settings, key=rec.key, target=target, material_maps=maps)


def render_created(report, settings, only_missing=True):
    """Queue Karma thumbnails for assets an ingest just created.

    Geometry and materials without a thumbnail get one; HDRIs always do (their ingest
    thumbnail is only a crop of the map). Problems are added to the report.
    """
    loaded = []
    for name, asset_dir in report.created:
        asset_type = report.created_types.get(asset_dir)
        if asset_type not in RENDERABLE_TYPES:
            continue
        try:
            data = schema.load(os.path.join(asset_dir, schema.SIDECAR))
            files = {k: schema.resolve(asset_dir, p)
                     for k, p in schema.current_version(data)["files"].items()}
        except (schema.SidecarError, OSError, KeyError, TypeError) as e:
            report.warn("%s: thumbnail render not started (%s)" % (name, e))
            continue
        loaded.append((name, asset_dir, asset_type, data, files))

    # Geometry shows the material created with it (same Megascans or GSG id).
    by_vendor_id = {}
    for _name, _dir, asset_type, data, files in loaded:
        info = data.get("info") or {}
        for k in ("megascans_id", "gsg_id"):
            if asset_type == "material" and info.get(k):
                sets = variants.resolution_sets(files)
                picked = variants.pick_resolution(list(sets), THUMB_RESOLUTION)
                by_vendor_id.setdefault((k, info[k]),
                                        sets[picked] if picked is not None else files)

    for name, asset_dir, asset_type, data, files in loaded:
        if only_missing and asset_type != "hdri" and (data.get("thumbnail") or {}).get("file"):
            continue
        maps = None
        if asset_type == "geometry":
            info = data.get("info") or {}
            maps = next((by_vendor_id[(k, info[k])] for k in ("megascans_id", "gsg_id")
                         if info.get(k) and (k, info[k]) in by_vendor_id), None)
        try:
            render_asset_thumbnail(asset_type, asset_dir, name, files, data["info"], settings,
                                   material_maps=maps)
        except (hou.Error, schema.SidecarError, OSError, ImportError, KeyError, TypeError) as e:
            report.warn("%s: thumbnail render not started (%s)" % (
                name, e.instanceMessage() if isinstance(e, hou.Error) else e))
