"""Texture-set detection: which image files form a material, and which channel each is.

No hou or Qt imports, so it is tested with plain Python.

A file belongs to a channel when a word of its name (split on _ - . and spaces) is a
channel keyword: "Rock_Wall_2K_Roughness.jpg" -> material "Rock_Wall", channel
"roughness". Resolution and LOD words (2k, 4K, lod0) are dropped from the material name.
"""
import os
import re

from . import schema

# channel -> keywords (lower case). Two-word keywords are written with "_".
CHANNEL_KEYWORDS = {
    "basecolor": ("basecolor", "base_color", "albedo", "diffuse", "diff", "color", "col"),
    "roughness": ("roughness", "rough"),
    "gloss": ("glossiness", "gloss"),
    "metallic": ("metallic", "metalness", "metal"),
    "normal": ("normal", "normals", "normalgl", "normaldx", "nor", "nrm", "norm"),
    "displacement": ("displacement", "displace", "disp", "height"),
    "ao": ("ambientocclusion", "ambient_occlusion", "occlusion", "ao"),
    "opacity": ("opacity", "alpha", "transparency"),
    "emission": ("emission", "emissive"),
    "specular": ("specular", "spec"),
    "bump": ("bump",),
    # Recognized so they don't start a new material; not wired by the builders.
    "other": ("cavity", "translucency", "fuzz", "arm", "orm", "mask", "sss"),
}
CHANNELS = tuple(k for k in CHANNEL_KEYWORDS if k != "other")
# Channels the material builders and thumbnails wire up.
WIRED = ("basecolor", "roughness", "metallic", "normal", "opacity", "emission", "displacement")
COLOR_CHANNELS = {"basecolor", "emission"}

_KEYWORD_CHANNEL = {}
for _ch, _words in CHANNEL_KEYWORDS.items():
    for _w in _words:
        _KEYWORD_CHANNEL[_w] = _ch

_SPLIT = re.compile(r"[_\-\s.]+")
_NOISE = re.compile(r"^(\d+k|\d{3,5}(px)?|lod\d+|var\d+|v\d+|gl|dx|udim|\d{4})$")
PREVIEW_WORDS = ("preview", "thumb", "thumbnail")


def is_preview(path):
    words = _SPLIT.split(schema.strip_ext(path).lower())
    return any(w in PREVIEW_WORDS for w in words)


def classify(path):
    """(material name, channel) for a texture file, or (None, None) when no channel word is found.

    The last channel word wins, so "Metal_Plate_Roughness" is roughness, not metallic.
    """
    stem = schema.strip_ext(path)
    words = [w for w in _SPLIT.split(stem) if w]
    low = [w.lower() for w in words]
    hit = None
    for i in range(len(low) - 1, -1, -1):
        if i > 0 and (low[i - 1] + "_" + low[i]) in _KEYWORD_CHANNEL:
            hit = (i - 1, 2, _KEYWORD_CHANNEL[low[i - 1] + "_" + low[i]])
            break
        if low[i] in _KEYWORD_CHANNEL:
            hit = (i, 1, _KEYWORD_CHANNEL[low[i]])
            break
    if hit is None:
        return None, None
    i, _n, channel = hit
    base = [w for w, lw in zip(words[:i], low[:i]) if not _NOISE.match(lw)]
    return "_".join(base), channel


def _rank(path, channel):
    """Sort key: lower is preferred when a channel has several files."""
    low = os.path.basename(path).lower()
    ext = schema.file_ext(low)
    words = _SPLIT.split(schema.strip_ext(low))
    score = 0
    if any(re.match(r"lod[1-9]", w) for w in words):
        score += 10                        # LOD0 / no LOD first
    if channel == "normal" and "dx" in words:
        score += 5                         # OpenGL normals first (Karma, Octane, Redshift)
    if channel == "displacement":
        score += 0 if ext in (".exr", ".tif", ".tiff") else 2   # high bit depth first
    elif ext == ".exr":
        score += 1
    res = max([int(w[:-1]) for w in words if re.fullmatch(r"\d+k", w)] or [0])
    return (score, -res, low)            # then highest resolution (8K before 2K)


class TextureSet:
    def __init__(self, name, folder):
        self.name = name
        self.folder = folder
        self.channels = {}     # channel -> chosen file
        self.extra = []        # other files of the set (alternates, unwired channels)
        self.preview = None

    @property
    def files(self):
        return list(self.channels.values()) + self.extra

    def __repr__(self):
        return "<TextureSet %s %s>" % (self.name, sorted(self.channels))


def group(files):
    """Group texture files into TextureSets. Returns (sets, leftovers, previews).

    Sets are formed per folder and material name. A folder that ends up with a single set
    names it after the folder when the file names give no name (or a scan ID such as
    Megascans' "vgvlbhqn"). A set needs a base color, a normal or a roughness map to count
    as a material; anything else goes to leftovers.
    """
    by_key, previews, leftovers = {}, [], []
    for f in files:
        if not schema.is_image(f):
            continue
        if is_preview(f):
            previews.append(f)
            continue
        name, channel = classify(f)
        if channel is None:
            leftovers.append(f)
            continue
        folder = os.path.dirname(os.path.abspath(f))
        key = (os.path.normcase(folder), name.lower())
        by_key.setdefault(key, (name, folder, []))[2].append((channel, f))

    per_folder = {}
    for (folder_key, _n) in by_key:
        per_folder[folder_key] = per_folder.get(folder_key, 0) + 1

    sets = []
    for (folder_key, _n), (name, folder, items) in by_key.items():
        folder_name = os.path.basename(folder)
        if per_folder[folder_key] == 1 and (not name or _looks_like_id(name)):
            name = folder_name
        ts = TextureSet(name or folder_name, folder)
        chosen = {}
        for channel, f in sorted(items, key=lambda cf: _rank(cf[1], cf[0])):
            if channel != "other" and channel not in chosen:
                chosen[channel] = f
            else:
                ts.extra.append(f)
        ts.channels = chosen
        if not ({"basecolor", "normal", "roughness"} & set(chosen)):
            leftovers.extend(f for _c, f in items)
            continue
        sets.append(ts)

    # Previews: same folder, and same name if there are several sets there.
    for ts in sets:
        local = [p for p in previews if os.path.normcase(os.path.dirname(os.path.abspath(p)))
                 == os.path.normcase(ts.folder)]
        named = [p for p in local if ts.name.lower() in schema.strip_ext(p).lower()]
        single = per_folder[os.path.normcase(ts.folder)] == 1
        candidates = named or (local if single else [])
        ts.preview = candidates[0] if candidates else None
    used = {os.path.normcase(ts.preview) for ts in sets if ts.preview}
    previews = [p for p in previews if os.path.normcase(p) not in used]
    sets.sort(key=lambda s: (s.folder.lower(), s.name.lower()))
    return sets, leftovers, previews


def _looks_like_id(name):
    """Scan IDs: one lower-case word of 6-10 letters/digits without vowels pattern, e.g. 'vgvlbhqn'."""
    return bool(re.fullmatch(r"[a-z0-9]{6,10}", name)) and \
        sum(c in "aeiou" for c in name) <= 2


# --- HDRI / image split ---------------------------------------------------------------
HDR_EXTS = {".hdr", ".exr", ".rat"}


def looks_like_hdri(path, size=None):
    """HDR file with a lat-long (about 2:1) aspect. `size` = (w, h) when known."""
    ext = schema.file_ext(path)
    if ext not in HDR_EXTS:
        return False
    if ext == ".hdr" or size is None:
        return True
    w, h = size
    return h > 0 and 1.8 <= float(w) / h <= 2.2


# --- Megascans metadata ------------------------------------------------------------------
def _find_key(obj, key):
    if isinstance(obj, dict):
        if obj.get("key") == key and "value" in obj:
            return obj["value"]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def megascans_height(folder):
    """Displacement height in scene units from a Megascans .json in `folder`, or None.

    Megascans stores it as {"key": "height", "value": "0.02 m"}. For a recognized Megascans
    asset only surfaces count: on 3D assets "height" is the object size.
    """
    import json
    from . import megascans
    asset = megascans.read_asset(folder)
    if asset is not None:
        return asset.displacement_scale
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".json")]
    except OSError:
        return None
    for n in names:
        try:
            with open(os.path.join(folder, n), "r", encoding="utf-8") as f:
                value = _find_key(json.load(f), "height")
        except (OSError, ValueError):
            continue
        if value is not None:
            m = re.search(r"[0-9]*\.?[0-9]+", str(value))
            if m:
                return float(m.group(0))
    return None