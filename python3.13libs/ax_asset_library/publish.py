"""Add assets to the library: ingest geometry files from disk, set thumbnails.

Saving the current selection as an asset (publish) comes in phase 2.
"""
import getpass
import os
import shutil

import hou

from . import schema, thumbnails

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
    sidecar = os.path.join(rec.asset_dir, schema.SIDECAR)
    data = schema.load(sidecar)
    try:
        thumbnails.make_thumbnail(image_path, os.path.join(rec.asset_dir, schema.THUMB))
    except thumbnails.ThumbnailError as e:
        raise hou.OperationFailed(str(e))
    data["thumbnail"].update(file=schema.THUMB, method="image", generated=schema.now_iso())
    schema.save(sidecar, data)
