"""LOD and texture-resolution alternatives, and the link from geometry to its material.

No hou or Qt imports: the panel and importers use this, plain-Python tests cover it.
"""
import os
import re

from . import megascans, schema

LOD_RE = re.compile(r"lod\d+", re.IGNORECASE)
RES_RE = re.compile(r"(?<=[_\-.])(\d{1,2})k(?=[_\-.])", re.IGNORECASE)


def lod_label(rank):
    return "Full" if rank == -1 else "LOD%d" % rank


def _lod_stem(path):
    return LOD_RE.sub("", os.path.basename(path)).lower()


def lod_files(files):
    """{rank: {format key: path}} for a geometry record's files, sorted by rank.

    Looks in each file's folder for the same name with another LOD token. The record's own
    files are always included.
    """
    out = {}

    def add(rank, key, path):
        out.setdefault(rank, {})[key] = path

    for key, path in files.items():
        add(megascans.lod_rank(path), key, path)
    for key, path in files.items():
        folder = os.path.dirname(os.path.abspath(path))
        stem = _lod_stem(path)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            candidate = os.path.join(folder, name)
            if schema.format_key(candidate) == key and _lod_stem(candidate) == stem:
                add(megascans.lod_rank(candidate), key, candidate)
    return {rank: out[rank] for rank in sorted(out)}


def _resolution_variants(path):
    """{resolution number: file} for one channel's file, found by name in its folder."""
    base = os.path.basename(path)
    match = RES_RE.search(base)
    if not match:
        return {}
    prefix, suffix = base[:match.start(1)], base[match.end(1):]
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    found = {}
    for name in names:
        low = name.lower()
        if not (low.startswith(prefix.lower()) and low.endswith(suffix.lower())):
            continue
        middle = name[len(prefix): len(name) - len(suffix)]
        if middle.isdigit():
            found[int(middle)] = os.path.join(folder, name)
    return found


def resolution_sets(channels):
    """{"8K": {...}, "4K": {...}, ...} highest first; {"": channels} without tokens.

    A channel missing at a resolution keeps the record's own file.
    """
    variants = {ch: _resolution_variants(path) for ch, path in channels.items()}
    numbers = sorted({number for found in variants.values() for number in found}, reverse=True)
    if not numbers:
        return {"": dict(channels)}
    sets = {}
    for number in numbers:
        sets["%dK" % number] = {ch: variants[ch].get(number, path)
                                for ch, path in channels.items()}
    return sets


def pick_lod(available_ranks, wanted):
    """The wanted rank, else the most detailed rank at or below it, else the smallest."""
    ranks = sorted(available_ranks)
    if not ranks:
        return None
    if wanted in ranks:
        return wanted
    if wanted is not None and wanted >= 0:
        below = [rank for rank in ranks if rank <= wanted]
        if below:
            return max(below)
    return min(ranks)


def _res_number(label):
    digits = label[:-1] if label else ""
    return int(digits) if digits.isdigit() else None


def pick_resolution(available_labels, wanted):
    """The wanted label, else the highest at or below it, else the lowest; "" = highest."""
    labels = sorted(available_labels, key=lambda l: _res_number(l) or 0, reverse=True)
    if not labels:
        return None
    if wanted and wanted in labels:
        return wanted
    number = _res_number(wanted) if wanted else None
    if number is not None:
        lower = [label for label in labels if (_res_number(label) or 0) <= number]
        return lower[0] if lower else labels[-1]
    return labels[0]


def linked_material(rec, records):
    """The material record for a geometry record, or None."""
    if rec.source_kind in schema.VENDOR_KINDS:
        prefix = rec.id.split(":")[:2] if rec.id else []
        for other in records:
            if other.type == "material" and other.id and other.id.split(":")[:2] == prefix:
                return other
        return None
    info = rec.info or {}
    ids = [(key, info.get(key)) for key in ("megascans_id", "gsg_id") if info.get(key)]
    if not ids:
        return None
    candidates = [other for other in records if other.type == "material" and other is not rec
                  and any((other.info or {}).get(key) == value for key, value in ids)]
    for other in candidates:
        if os.path.normcase(os.path.normpath(other.root)) == \
                os.path.normcase(os.path.normpath(rec.root)):
            return other
    return candidates[0] if candidates else None


def is_atlas(rec):
    return rec.type == "material" and (
        (rec.info or {}).get("vendor_kind") == "atlas" or "opacity" in rec.files)
