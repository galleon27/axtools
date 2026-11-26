"""Per-user settings: library roots, import options, UI state.

Stored in $HOUDINI_USER_PREF_DIR/ax_asset_library.json. Per-user data (favorites, color
labels, recents, UI state) lives here and not in the library, so two machines syncing the
same library never write the same file.
"""
import copy
import json
import os
import re

from . import schema

try:
    import hou
except ImportError:  # plain-Python tests
    hou = None

ENV_VAR = "AX_ASSET_LIB"
PREFS_FILE = "ax_asset_library.json"

GEO_FORMAT_ORDER = ["bgeo.sc", "bgeo", "usd", "usdc", "usda", "usdz", "abc", "fbx", "obj", "ply"]

VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
RESERVED_VARS = {"HIP", "HIPNAME", "HIPFILE", "JOB", "HFS", "HH", "HOME", "TEMP", "TMP", "PATH",
                 "OS"}
RESERVED_PREFIXES = ("HOUDINI_",)

DEFAULTS = {
    "roots": [],                 # [{"var": "MEGASCANS", "path": "D:/lib", "read_only": False,
                                 #   "kind": "library"}]
    "path_mode": "token",        # "token": keep $VAR roots in parms, "absolute": expanded paths
    "geo_format_order": GEO_FORMAT_ORDER,
    "thumb_size": 128,
    "sort": "name",
    "type_filter": [],
    "splitter": [],
    "copy_on_ingest": True,
    "read_geo_info": True,
    "ingest_render": False,
    "megascans_copy": False,       # Megascans textures are large: reference in place by default
    "model_lod": 0,                # wanted LOD rank; -1 = files without an LOD tag
    "model_resolution": "",        # e.g. "4K"; "" = highest
    "list_mode": False,
    "material_renderer": "karma",  # last renderer picked for a material import
    # per-user collections (asset ids), kept here so a shared library never gets write conflicts
    "favorites": [],
    "color_labels": {},            # asset id -> color label key
    "asset_meta": {},              # asset id -> {"tags": [..], "description": str}
    "smart_searches": [],          # [{"name": str, "query": str}]
    "recent": [],                  # [[asset id, iso time], ...], newest first
    "recent_limit": 50,
    "last_ingest_dir": "",
    "last_category": "",
    # publish
    "publish_formats": ["bgeo.sc"],
    "publish_center": False,
    "publish_ground": False,
    "publish_unpack": False,
    "publish_materials": True,     # save assigned materials and copy their textures
    "publish_thumbnail": "karma",  # "karma", "image" or "none"
    # Karma thumbnails
    "thumb_renderer": "BRAY_HdKarma",
    "thumb_samples": 16,
    "thumb_resolution": 512,
    "thumb_timeout": 300,
}


def expand(path):
    """Expand $VARS (Houdini variables when hou is available) and ~."""
    if not path:
        return ""
    if hou is not None:
        try:
            path = hou.text.expandString(path)
        except (AttributeError, hou.Error):
            path = os.path.expandvars(path)
    else:
        path = os.path.expandvars(path)
    return os.path.normpath(os.path.expanduser(path))


def pref_dir():
    d = ""
    if hou is not None:
        try:
            d = hou.text.expandString("$HOUDINI_USER_PREF_DIR")
        except (AttributeError, hou.Error):
            d = ""
    d = d or os.environ.get("HOUDINI_USER_PREF_DIR", "")
    if not d or d.startswith("$"):
        d = os.path.join(os.path.expanduser("~"), ".ax_asset_library")
    return d


def cache_dir():
    return os.path.join(pref_dir(), "ax_asset_library")


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


class Prefs:
    def __init__(self, path=None):
        self.path = path or os.path.join(pref_dir(), PREFS_FILE)
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                    self.data[k] = v

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass  # prefs are a convenience; never break the panel over them

    def get(self, key):
        return self.data.get(key, copy.deepcopy(DEFAULTS.get(key)))

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    # --- roots -------------------------------------------------------------------
    def roots(self):
        """[{"path" (as typed), "var", "read_only", "megascans", "kind", "expanded"}].

        A Megascans or GSG root (a vendor folder browsed in place) is always read-only.
        """
        roots = [dict(r) for r in self.get("roots") if isinstance(r, dict) and r.get("path")]
        for r in roots:
            kind = r.get("kind") or ("megascans" if r.get("megascans") else "library")
            if kind not in schema.ROOT_KINDS:
                kind = "library"
            r["kind"] = kind
            r["megascans"] = kind == "megascans"          # kept for older callers
            r["var"] = str(r.get("var") or "").strip().upper()
            r["read_only"] = bool(r.get("read_only")) or kind in schema.VENDOR_KINDS
            r["expanded"] = expand(r["path"])
        return roots

    def root_for(self, expanded_root):
        for r in self.roots():
            if _same(r["expanded"], expanded_root):
                return r
        return None

    def writable_roots(self):
        return [r for r in self.roots() if not r["read_only"]]


def check_var(name):
    """Error text for an invalid root variable name, or "" when it is valid or empty."""
    if not name:
        return ""
    if not VAR_RE.match(name):
        return "Use letters, numbers and underscores, and start with a letter or underscore."
    if name in RESERVED_VARS or name.startswith(RESERVED_PREFIXES):
        return "This name is reserved by Houdini."
    return ""


def apply_root_vars(settings):
    """Set each root's variable in the environment; returns {var: expanded path}."""
    applied = {}
    for r in settings.roots():
        var = r.get("var", "")
        if not var or check_var(var):
            continue
        path = r["expanded"]
        applied[var] = path
        os.environ[var] = path
        if hou is not None:
            try:
                hou.putenv(var, path.replace("\\", "/"))
            except hou.Error:
                pass
    return applied


# --- favorites / recently used -------------------------------------------------------------
def favorites(settings):
    return set(settings.get("favorites"))


def color_labels(settings):
    """{asset id: color label key}, dropping keys that are no longer known."""
    stored = settings.get("color_labels")
    if not isinstance(stored, dict):
        return {}
    return {asset_id: key for asset_id, key in stored.items()
            if key in schema.COLOR_LABEL_KEYS}


def set_color_label(settings, asset_ids, key):
    """Set one color label (None or "" clears it) on each asset; saves once."""
    if key and key not in schema.COLOR_LABEL_KEYS:
        raise ValueError("Unknown color label %r." % key)
    labels = dict(settings.get("color_labels") or {})
    for asset_id in asset_ids:
        if not asset_id:
            continue
        if key:
            labels[asset_id] = key
        else:
            labels.pop(asset_id, None)
    settings.set("color_labels", labels)


def asset_meta(settings):
    """{asset id: {"tags": [...], "description": str}} added by the user, bad entries dropped."""
    stored = settings.get("asset_meta")
    if not isinstance(stored, dict):
        return {}
    out = {}
    for asset_id, meta in stored.items():
        if not asset_id or not isinstance(meta, dict):
            continue
        tags = schema.clean_tags(meta.get("tags") or [])
        description = meta.get("description")
        description = description.strip() if isinstance(description, str) else ""
        out[asset_id] = {"tags": tags, "description": description}
    return out


def set_asset_meta(settings, asset_id, tags, description, base_tags=None,
                   base_description=None):
    """Store the user's tags and description for an asset browsed in place and save.

    The entry replaces the scanned tags and description (an empty entry clears them). When
    both equal the scanned `base_tags` / `base_description`, the entry is removed instead."""
    meta = settings.get("asset_meta")
    meta = dict(meta) if isinstance(meta, dict) else {}
    tags = schema.clean_tags(tags or [])
    description = (description or "").strip()
    if base_tags is not None and tags == schema.clean_tags(base_tags) \
            and description == (base_description or "").strip():
        meta.pop(asset_id, None)
    else:
        meta[asset_id] = {"tags": tags, "description": description}
    settings.set("asset_meta", meta)


def set_favorite(settings, asset_ids, on):
    favs = [f for f in settings.get("favorites") if f not in asset_ids]
    if on:
        favs.extend(asset_ids)
    settings.set("favorites", favs)


def smart_searches(settings):
    """Saved searches, in the order they were saved: [{"name": str, "query": str}]."""
    out = []
    for item in settings.get("smart_searches") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        query = str(item.get("query") or "").strip()
        if name and query:
            out.append({"name": name, "query": query})
    return out


def save_smart_search(settings, name, query):
    """Add a saved search; a search with the same name is replaced."""
    name, query = (name or "").strip(), (query or "").strip()
    if not name or not query:
        return
    items = [s for s in smart_searches(settings) if s["name"].lower() != name.lower()]
    items.append({"name": name, "query": query})
    settings.set("smart_searches", items)


def remove_smart_search(settings, name):
    items = [s for s in smart_searches(settings) if s["name"].lower() != (name or "").lower()]
    settings.set("smart_searches", items)


def recent(settings):
    """{asset id: iso time} of recently imported assets."""
    out = {}
    for item in settings.get("recent"):
        if isinstance(item, list) and len(item) == 2 and item[0] not in out:
            out[item[0]] = item[1]
    return out


def add_recent(settings, asset_ids, when):
    keep = [i for i in settings.get("recent") if isinstance(i, list) and i and i[0] not in asset_ids]
    items = [[a, when] for a in asset_ids] + keep
    settings.set("recent", items[:max(1, int(settings.get("recent_limit")))])

def parm_path(abs_path, root, path_mode="token"):
    """Path to write into a node parm.

    With path_mode "token", the result keeps the root's variable ($MEGASCANS/...) or a root
    path typed with a variable, so the .hip works on machines where the library sits
    elsewhere.
    """
    abs_path = os.path.normpath(abs_path)
    if root and path_mode == "token":
        var = root.get("var", "")
        if var:
            try:
                rel = os.path.relpath(abs_path, root["expanded"])
            except ValueError:
                rel = ".."
            if not rel.startswith(".."):
                return "$" + var + "/" + rel.replace("\\", "/")
        if "$" in root.get("path", ""):
            try:
                rel = os.path.relpath(abs_path, root["expanded"])
            except ValueError:
                rel = ".."
            if not rel.startswith(".."):
                return root["path"].rstrip("/\\") + "/" + rel.replace("\\", "/")
    return abs_path.replace("\\", "/")
