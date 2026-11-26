"""Resource use of the asset browser: process memory and the size of its caches.

No hou or Qt imports, so it is tested with plain Python. The panel's console shows the
report; the in-memory image cache reports its own numbers (grid.ThumbCache.stats).
"""
import ctypes
import os

from . import library

PREVIEWS = "previews"      # thumbnails.preview_cache_dir()


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%d B" % n if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024


def process_memory():
    """Resident memory of this process in bytes, or None when the OS gives no number."""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        try:
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            info = Counters()
            info.cb = ctypes.sizeof(info)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.byref(info), info.cb)
            return int(info.WorkingSetSize) if ok else None
        except (AttributeError, OSError):
            return None
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def folder_usage(path, pattern=None):
    """(file count, total bytes) of the files directly in `path` (all, or those `pattern`
    matches: a callable on the file name)."""
    count = size = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file() and (pattern is None or pattern(entry.name)):
                try:
                    size += entry.stat().st_size
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return count, size


def disk_usage(cache_dir):
    """{name: (files, bytes)} for the browser's caches on disk."""
    return {
        "scan index": folder_usage(cache_dir, lambda n: n.startswith("index_")
                                   and n.endswith(".json")),
        "rendered thumbnails": folder_usage(library.megascans_thumb_dir(cache_dir)),
        "image previews": folder_usage(os.path.join(cache_dir, PREVIEWS)),
    }


def clear_index(cache_dir):
    """Delete the scan index files; the next scan reads every asset again."""
    for name in os.listdir(cache_dir) if os.path.isdir(cache_dir) else ():
        if name.startswith("index_") and name.endswith(".json"):
            try:
                os.remove(os.path.join(cache_dir, name))
            except OSError:
                pass


def report_lines(cache_dir, image_stats=None, records=None, queue=None):
    """Console text: one string per line."""
    mem = process_memory()
    lines = ["Process memory     %s" % (fmt_bytes(mem) if mem else "unknown")]
    if records is not None:
        lines.append("Records            %d" % records)
    if image_stats:
        s = image_stats
        lines.append("Images in RAM      %d (%s), limit %s" % (
            s["images"], fmt_bytes(s["image_bytes"]), fmt_bytes(s["limit"])))
        lines.append("Pixmaps in RAM     %d (%s)" % (s["pixmaps"], fmt_bytes(s["pixmap_bytes"])))
        total = s["hits"] + s["misses"]
        lines.append("Cache hits         %d / %d%s  (%d loading)" % (
            s["hits"], total, " = %d%%" % (100 * s["hits"] // total) if total else "",
            s["pending"]))
    for name, (count, size) in disk_usage(cache_dir).items():
        lines.append("%-20s %d file(s), %s" % (name.capitalize(), count, fmt_bytes(size)))
    if queue:
        lines.append("Karma queue        %d" % queue)
    return lines
