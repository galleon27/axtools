"""Library edits: rename, move, delete to trash, tags and description.

No hou or Qt imports, so everything here is tested with plain Python. Callers check
read-only roots (prefs) before calling; functions raise ManageError with a readable reason.

Moving or renaming changes an asset's folder, so absolute paths to it in .hip files break.
Nodes in the open scene can be relinked afterwards (scene.relink).
"""
import datetime
import os
import shutil

from . import schema

TRASH = ".trash"


class ManageError(Exception):
    pass


def _sidecar(asset_dir):
    return os.path.join(asset_dir, schema.SIDECAR)


def _load(asset_dir):
    try:
        return schema.load(_sidecar(asset_dir))
    except schema.SidecarError as e:
        raise ManageError(str(e))


def type_dir(root, asset_type):
    return os.path.join(root, schema.TYPE_FOLDERS[asset_type])


def clean_category(category):
    parts = [schema.sanitize_name(p) for p in category.replace("\\", "/").split("/") if p.strip()]
    return "/".join(parts)


def _move_folder(src, dst):
    if os.path.exists(dst):
        raise ManageError("%s already exists." % dst)
    parent = os.path.dirname(dst)
    os.makedirs(parent, exist_ok=True)
    try:
        os.rename(src, dst)          # same drive: instant, atomic
    except OSError:
        try:
            shutil.move(src, dst)    # other drive
        except OSError as e:
            raise ManageError("Could not move %s: %s" % (src, e))


def _prune_empty(folder, stop):
    """Remove empty category folders left behind, up to (not including) `stop`."""
    inside = os.path.normcase(os.path.abspath(stop)) + os.sep
    folder = os.path.abspath(folder)
    while os.path.normcase(folder).startswith(inside):  # strictly inside `stop`
        try:
            os.rmdir(folder)          # only succeeds when empty
        except OSError:
            return
        folder = os.path.dirname(folder)


def set_meta(asset_dir, tags=None, description=None):
    """Replace tags and/or description (None = leave as is)."""
    data = _load(asset_dir)
    if tags is not None:
        data["tags"] = schema.clean_tags(tags)
    if description is not None:
        data["description"] = description.strip()
    schema.save(_sidecar(asset_dir), data)
    return data


def rename(asset_dir, new_name):
    """Rename an asset (folder and name in asset.json). Returns the new folder."""
    name = schema.sanitize_name(new_name)
    if not new_name.strip():
        raise ManageError("Enter a name.")
    data = _load(asset_dir)
    dst = os.path.join(os.path.dirname(asset_dir), name)
    if os.path.normcase(dst) != os.path.normcase(asset_dir):
        _move_folder(asset_dir, dst)
    elif name != os.path.basename(asset_dir):
        tmp = asset_dir + ".renaming"     # case-only rename on Windows
        _move_folder(asset_dir, tmp)
        _move_folder(tmp, dst)
    data["name"] = name
    schema.save(_sidecar(dst), data)
    return dst


def move(asset_dir, asset_type, src_root, dst_root, category):
    """Move an asset to another category (and/or root). Returns the new folder."""
    category = clean_category(category)
    parts = [type_dir(dst_root, asset_type)] + (category.split("/") if category else [])
    dst = os.path.join(*(parts + [os.path.basename(asset_dir)]))
    if os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(asset_dir)):
        return asset_dir
    old_parent = os.path.dirname(asset_dir)
    _move_folder(asset_dir, dst)
    data = _load(dst)
    data["category"] = category
    schema.save(_sidecar(dst), data)
    _prune_empty(old_parent, type_dir(src_root, asset_type))
    return dst


def rename_category(root, asset_type, category, new_leaf):
    """Rename a category folder. Returns [(old asset_dir, new asset_dir)] for moved assets."""
    if not str(new_leaf).strip() or "/" in str(new_leaf):
        raise ManageError("Enter a category name without slashes.")
    name = schema.sanitize_name(new_leaf)
    parts = [p for p in category.replace("\\", "/").split("/") if p]
    if not parts:
        raise ManageError("Pick a category first.")
    if name == parts[-1]:
        return []
    parent = os.path.join(type_dir(root, asset_type), *parts[:-1])
    src = os.path.join(parent, parts[-1])
    dst = os.path.join(parent, name)
    if not os.path.isdir(src):
        raise ManageError("%s does not exist." % src)
    if os.path.normcase(src) != os.path.normcase(dst):
        _move_folder(src, dst)
    else:
        tmp = src + ".renaming"     # case-only rename on Windows
        _move_folder(src, tmp)
        _move_folder(tmp, dst)

    new_category = "/".join(parts[:-1] + [name])
    moved = []
    for folder, dirs, files in os.walk(dst):
        if schema.SIDECAR in files:
            data = _load(folder)
            rel = os.path.relpath(folder, dst).replace("\\", "/")
            cat_rel = os.path.dirname(rel)
            data["category"] = "/".join(p for p in (new_category, cat_rel) if p)
            schema.save(_sidecar(folder), data)
            old = src if rel == "." else os.path.join(src, *rel.split("/"))
            moved.append((old, folder))
            dirs[:] = []            # asset folders have no categories below them
    return moved


def trash(asset_dir, root, asset_type):
    """Move an asset to <root>/.trash (the scanner skips it). Returns the trash folder."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(root, TRASH, "%s_%s" % (stamp, os.path.basename(asset_dir)))
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(root, TRASH, "%s_%s_%d" % (stamp, os.path.basename(asset_dir), n))
        n += 1
    old_parent = os.path.dirname(asset_dir)
    _move_folder(asset_dir, dst)
    _prune_empty(old_parent, type_dir(root, asset_type))
    return dst


def trash_dir(root):
    return os.path.join(root, TRASH)
