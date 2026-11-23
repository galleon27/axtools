"""asset.json sidecar: defaults, read/write, versions, validation.

No hou or Qt imports: this module runs in scan worker threads and in plain-Python tests.
"""
import datetime
import json
import os
import re
import uuid

SCHEMA_VERSION = 1
SIDECAR = "asset.json"
THUMB = "thumb.jpg"
PREVIEW = "preview.jpg"

ASSET_TYPES = ("geometry", "material", "hdri", "image")
# Type -> top-level folder under a library root.
TYPE_FOLDERS = {"geometry": "geometry", "material": "materials", "hdri": "hdri", "image": "images"}
FOLDER_TYPES = {v: k for k, v in TYPE_FOLDERS.items()}
TYPE_LABELS = {"geometry": "Geometry", "material": "Material", "hdri": "HDRI", "image": "Image"}
# Types that get a Karma-rendered thumbnail (images use the image itself).
RENDERABLE_TYPES = ("geometry", "material", "hdri")

# Multi-part extensions first so ".bgeo.sc" wins over ".sc".
_MULTI_EXTS = (".bgeo.sc", ".bgeo.gz", ".bgeo.lzma", ".bgeo.bz2", ".geo.sc", ".geo.gz")
GEO_EXTS = {".bgeo.sc", ".bgeo.gz", ".bgeo.lzma", ".bgeo.bz2", ".bgeo", ".geo", ".geo.sc", ".geo.gz",
            ".abc", ".fbx", ".obj", ".ply", ".usd", ".usda", ".usdc", ".usdz"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tga", ".tif", ".tiff", ".bmp", ".webp", ".exr", ".hdr", ".rat"}
QT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tga", ".tif", ".tiff", ".bmp", ".webp"}


class SidecarError(Exception):
    """asset.json is missing, not JSON, or not an object."""


def now_iso():
    """Current UTC time as an ISO 8601 string: '2026-11-23T14:05:09Z'."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_ext(path):
    """Lower-case extension including multi-part ones: 'a/Rock.bgeo.sc' -> '.bgeo.sc'."""
    low = os.path.basename(path).lower()
    for ext in _MULTI_EXTS:
        if low.endswith(ext):
            return ext
    return os.path.splitext(low)[1]


def strip_ext(path):
    """File name without its (multi-part) extension."""
    base = os.path.basename(path)
    ext = file_ext(base)
    return base[: len(base) - len(ext)] if ext else base


def format_key(path):
    """Key used in a version's `files` dict for a geometry file: '.bgeo.sc' -> 'bgeo.sc'."""
    return file_ext(path).lstrip(".")


def is_geometry(path):
    return file_ext(path) in GEO_EXTS


def is_image(path):
    return file_ext(path) in IMAGE_EXTS


def sanitize_name(name):
    """Folder/node-safe name: spaces and odd characters become '_'."""
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "asset"


def version_label(number):
    return "v%03d" % int(number)


def new_asset(name, asset_type, category="", tags=None, description="", author=""):
    if asset_type not in ASSET_TYPES:
        raise ValueError("Unknown asset type: %r" % asset_type)
    stamp = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "id": uuid.uuid4().hex,
        "name": name,
        "type": asset_type,
        "category": category.strip("/"),
        "tags": clean_tags(tags or []),
        "description": description,
        "author": author,
        "created": stamp,
        "modified": stamp,
        "current": 0,
        "versions": [],
        "source": {"kind": "", "origin_path": "", "megascans_id": "", "url": ""},
        "info": {},
        "thumbnail": {"file": "", "method": "", "generated": ""},
    }


def clean_tags(tags):
    """Lower-case, stripped, de-duplicated tags in their original order."""
    if isinstance(tags, str):
        tags = re.split(r"[,;]", tags)
    seen, out = set(), []
    for t in tags:
        t = str(t).strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def migrate(data):
    """Fill missing keys so older or hand-written sidecars load. Returns the same dict."""
    if not isinstance(data, dict):
        raise SidecarError("asset.json does not contain an object")
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("id", uuid.uuid4().hex)
    data.setdefault("name", "")
    data.setdefault("type", "")
    data.setdefault("category", "")
    data["tags"] = clean_tags(data.get("tags") or [])
    data.setdefault("description", "")
    data.setdefault("author", "")
    data.setdefault("created", "")
    data.setdefault("modified", data.get("created", ""))
    versions = data.get("versions")
    if not isinstance(versions, list):
        versions = []
    for v in versions:
        if isinstance(v, dict):
            v.setdefault("files", {})
            v.setdefault("note", "")
            v.setdefault("created", "")
            v.setdefault("author", "")
    data["versions"] = [v for v in versions if isinstance(v, dict) and "version" in v]
    if not data.get("current") and data["versions"]:
        data["current"] = max(v["version"] for v in data["versions"])
    data.setdefault("current", 0)
    for key, default in (("source", {"kind": "", "origin_path": "", "megascans_id": "", "url": ""}),
                         ("info", {}),
                         ("thumbnail", {"file": "", "method": "", "generated": ""})):
        if not isinstance(data.get(key), dict):
            data[key] = dict(default)
        else:
            for k, v in default.items():
                data[key].setdefault(k, v)
    data["schema_version"] = SCHEMA_VERSION
    return data


def validate(data):
    """List of problems that make the asset unusable or suspicious (empty = fine)."""
    problems = []
    if not data.get("name"):
        problems.append("no name")
    if data.get("type") not in ASSET_TYPES:
        problems.append("unknown type %r" % data.get("type"))
    if not data.get("versions"):
        problems.append("no versions")
    elif current_version(data) is None:
        problems.append("current version %s not found" % data.get("current"))
    return problems


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SidecarError("missing: %s" % path)
    except (OSError, ValueError) as e:
        raise SidecarError("%s: %s" % (path, e))
    return migrate(data)


def save(path, data, touch=True):
    """Write atomically (temp file + replace) so a sync client never sees half a file."""
    if touch:
        data["modified"] = now_iso()
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


# --- versions ----------------------------------------------------------------
def version_entry(data, number):
    for v in data.get("versions", []):
        if v.get("version") == number:
            return v
    return None


def current_version(data):
    return version_entry(data, data.get("current"))


def next_version(data):
    return max([v["version"] for v in data.get("versions", [])] or [0]) + 1


def add_version(data, files, note="", author="", number=None):
    """Append a version with `files` ({key: path relative to the asset folder}); make it current."""
    number = number or next_version(data)
    data["versions"].append({
        "version": number,
        "created": now_iso(),
        "author": author,
        "files": dict(files),
        "note": note,
    })
    data["current"] = number
    return number


def set_current(data, number):
    if version_entry(data, number) is None:
        raise ValueError("no version %s" % number)
    data["current"] = number


def resolve(asset_dir, relpath):
    """Absolute path of a file referenced in the sidecar (absolute paths pass through)."""
    if not relpath:
        return ""
    if os.path.isabs(relpath) or re.match(r"^[A-Za-z]:[\\/]", relpath):
        return os.path.normpath(relpath)
    return os.path.normpath(os.path.join(asset_dir, relpath))


def relpath(asset_dir, path):
    """Path stored in the sidecar: relative with '/' when inside the asset folder, else absolute."""
    path = os.path.abspath(path)
    try:
        rel = os.path.relpath(path, asset_dir)
    except ValueError:  # different drive on Windows
        return path.replace("\\", "/")
    if rel.startswith(".."):
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def pick_file(files, order):
    """Key of the preferred file in a version's `files` dict, following `order` (format keys)."""
    if not files:
        return None
    for key in order:
        if key in files:
            return key
    return sorted(files)[0]
