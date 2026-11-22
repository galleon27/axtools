"""Per-user settings: library roots, import options, UI state.

Stored in $HOUDINI_USER_PREF_DIR/ax_asset_library.json. Per-user data (favorites, recents,
UI state) lives here and not in the library, so two machines syncing the same library
never write the same file.
"""
import copy
import json
import os

try:
    import hou
except ImportError:  # plain-Python tests
    hou = None

ENV_VAR = "AX_ASSET_LIB"
PREFS_FILE = "ax_asset_library.json"

GEO_FORMAT_ORDER = ["bgeo.sc", "bgeo", "usd", "usdc", "usda", "usdz", "abc", "fbx", "obj", "ply"]

DEFAULTS = {
    "roots": [],                 # [{"path": "$AX_ASSET_LIB" or "D:/lib", "read_only": False}]
    "path_mode": "token",        # "token": keep $VAR roots in parms, "absolute": expanded paths
    "geo_format_order": GEO_FORMAT_ORDER,
    "thumb_size": 128,
    "sort": "name",
    "type_filter": [],
    "splitter": [],
    "copy_on_ingest": True,
    "read_geo_info": True,
    "ingest_render": False,
    "material_renderer": "karma",  # last renderer picked for a material import
    # per-user collections (asset ids), kept here so a shared library never gets write conflicts
    "favorites": [],
    "recent": [],                  # [[asset id, iso time], ...], newest first
    "recent_limit": 50,
    "last_ingest_dir": "",
    "last_category": "",
    # publish
    "publish_formats": ["bgeo.sc"],
    "publish_center": False,
    "publish_ground": False,
    "publish_unpack": False,
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
        """[{"path" (as typed), "read_only", "expanded"}]. Falls back to $AX_ASSET_LIB."""
        roots = [dict(r) for r in self.get("roots") if isinstance(r, dict) and r.get("path")]
        if not roots and os.environ.get(ENV_VAR):
            roots = [{"path": "$" + ENV_VAR, "read_only": False}]
        for r in roots:
            r["read_only"] = bool(r.get("read_only"))
            r["expanded"] = expand(r["path"])
        return roots

    def root_for(self, expanded_root):
        for r in self.roots():
            if _same(r["expanded"], expanded_root):
                return r
        return None

    def writable_roots(self):
        return [r for r in self.roots() if not r["read_only"]]


# --- favorites / recently used -------------------------------------------------------------
def favorites(settings):
    return set(settings.get("favorites"))


def set_favorite(settings, asset_ids, on):
    favs = [f for f in settings.get("favorites") if f not in asset_ids]
    if on:
        favs.extend(asset_ids)
    settings.set("favorites", favs)


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

    With path_mode "token" and a root typed with a variable ($AX_ASSET_LIB), the result keeps
    the variable so the .hip works on machines where the library sits elsewhere.
    """
    abs_path = os.path.normpath(abs_path)
    if root and path_mode == "token" and "$" in root.get("path", ""):
        try:
            rel = os.path.relpath(abs_path, root["expanded"])
        except ValueError:
            rel = ".."
        if not rel.startswith(".."):
            return root["path"].rstrip("/\\") + "/" + rel.replace("\\", "/")
    return abs_path.replace("\\", "/")
