"""Add assets to the library: publish the selected node, ingest files from disk,
versions and thumbnails.
"""
import getpass
import os
import shutil
import tempfile

import hou

from . import karma, schema, thumbnails

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


# --- ingest ---------------------------------------------------------------------------
def _write_asset(root, category, name, files, tags, copy, read_info, thumb_src, report):
    asset_dir = asset_dir_for(root, "geometry", category, name)
    if os.path.exists(asset_dir):
        report.skipped.append((files[0], "%s already exists in the library" % name))
        return None
    os.makedirs(asset_dir)
    try:
        _fill_asset(asset_dir, root, category, name, files, tags, copy, read_info, thumb_src, report)
    except BaseException:
        shutil.rmtree(asset_dir, ignore_errors=True)  # no half-written assets in the library
        raise
    report.created.append((name, asset_dir))
    return asset_dir


def _fill_asset(asset_dir, root, category, name, files, tags, copy, read_info, thumb_src, report):
    vdir = os.path.join(asset_dir, schema.version_label(1))
    os.makedirs(vdir)
    data = schema.new_asset(name, "geometry", category=category, tags=tags, author=author())
    data["source"].update(kind="ingest", origin_path=files[0].replace("\\", "/"))

    version_files, sizes = {}, {}
    for f in files:
        if copy:
            dst = os.path.join(vdir, os.path.basename(f))
            shutil.copy2(f, dst)
            for ext in COMPANION_EXTS.get(schema.file_ext(f), ()):
                comp = os.path.splitext(f)[0] + ext
                if os.path.isfile(comp):
                    shutil.copy2(comp, os.path.join(vdir, os.path.basename(comp)))
        else:
            dst = os.path.abspath(f)
        key = schema.format_key(f)
        version_files[key] = schema.relpath(asset_dir, dst)
        sizes[key] = os.path.getsize(dst)
    schema.add_version(data, version_files, note="ingested", author=data["author"])
    data["info"]["file_sizes"] = sizes
    data["info"]["formats"] = sorted(version_files)

    if read_info:
        try:
            data["info"].update(geometry_info(files[0]))
        except hou.Error as e:
            report.warn("%s: could not read geometry info (%s)" % (name, e.instanceMessage()))

    if thumb_src:
        try:
            thumbnails.make_thumbnail(thumb_src, os.path.join(asset_dir, schema.THUMB))
            data["thumbnail"].update(file=schema.THUMB, method="image", generated=schema.now_iso())
        except Exception as e:  # a bad preview image must not fail the ingest
            report.warn("%s: thumbnail failed (%s)" % (name, e))

    schema.save(os.path.join(asset_dir, schema.SIDECAR), data)


def ingest_geometry(paths, root, category="", tags=None, copy=True, read_info=True):
    """Create one geometry asset per file group in `paths` under `root` (a prefs root dict)."""
    check_writable(root)
    category = clean_category(category)
    tags = schema.clean_tags(tags or [])
    report = IngestReport()
    geo, images, other = collect(paths)
    companions = {os.path.normcase(os.path.splitext(os.path.abspath(g))[0] + ext)
                  for g in geo for ext in COMPANION_EXTS.get(schema.file_ext(g), ())}
    for p in other:
        if os.path.normcase(os.path.abspath(p)) not in companions:
            report.skipped.append((p, "not a geometry file"))
    groups = group_geometry(geo)
    per_folder = {}
    for _, files in groups:
        d = os.path.normcase(os.path.dirname(os.path.abspath(files[0])))
        per_folder[d] = per_folder.get(d, 0) + 1

    with hou.InterruptableOperation("Adding assets to the library",
                                    open_interrupt_dialog=len(groups) > 3) as op:
        for i, (stem, files) in enumerate(groups):
            name = schema.sanitize_name(stem)
            folder = os.path.normcase(os.path.dirname(os.path.abspath(files[0])))
            thumb = find_thumbnail(files[0], images, per_folder[folder] == 1)
            try:
                _write_asset(root, category, name, files, tags, copy, read_info, thumb, report)
            except OSError as e:
                report.failed.append((files[0], str(e)))
            try:
                op.updateProgress(float(i + 1) / len(groups))
            except hou.OperationInterrupted:
                report.warn("Stopped by user after %d of %d." % (i + 1, len(groups)))
                break
    return report


def set_thumbnail(rec, image_path, read_only=False):
    """Replace an asset's thumbnail with a square crop of `image_path`."""
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
                 thumbnail_image=None, asset_dir=None):
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

        schema.add_version(data, files, note=note, author=author(), number=number)
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
def render_thumbnail(asset_dir, name, geo_file, bounds, settings):
    """Queue a Karma thumbnail render for a geometry file. Returns True when queued.

    Formats husk can't read as a layer (obj, fbx, ply) are converted to bgeo.sc first,
    and bounds are read from the file when the sidecar has none.
    """
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
        queue = thumbnails.karma_queue()
        queue.renderer = settings.get("thumb_renderer")
        queue.timeout_s = settings.get("thumb_timeout")
        return queue.add(asset_dir, name, layer, bounds, workdir,
                         settings.get("thumb_resolution"), settings.get("thumb_samples"))
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def render_record_thumbnail(rec, settings):
    root = settings.root_for(rec.root)
    if root and root["read_only"]:
        raise hou.OperationFailed("%s is in a read-only library root." % rec.name)
    if rec.type != "geometry":
        raise hou.OperationFailed("Karma thumbnails are for geometry assets.")
    order = [f for f in settings.get("geo_format_order") if f in karma.LAYER_FORMATS]
    path = rec.primary_file(order + list(settings.get("geo_format_order")))
    if not path or not os.path.isfile(path):
        raise hou.OperationFailed("%s: geometry file not found." % rec.name)
    return render_thumbnail(rec.asset_dir, rec.name, path, (rec.info or {}).get("bounds"), settings)
