# Megascans: Bridge categories, scan fixes, plant downloads

## Context
The AX Asset Library builds Megascans categories from the JSON's `categories` list, which is
inconsistent (`['atlas', 'tree', 'atlas']`, `['3d', 'Interior', 'Furniture']`). Bridge's own
browse tree is in `assetCategories`, a nested dict whose top key is the Bridge section:

    {"3D asset": {"nature": {"rock": {"cliff": {}, "sandstone": {}}}}}
    {"surface": {"concrete": {"rough": {}}}}
    {"decal": {"leakage": {"stamp": {}}}}
    {"3D plant": {"grass": {"lawn": {}}}}

A survey of the user's library (`S:/Galerija_12_Digital_Tools/Assets/Megascans_Library/megascans_dl`)
found these top keys: `3D asset` (4,185), `surface` (7,604), `atlas` (3,294), `decal`
(2,235), `imperfection` (547), `3D plant` (288), `displacement` (52), and 12 assets with no
`assetCategories`. The folder names and `categories[0]` get the kind wrong for 2,234 decals
(in `atlases/`), 599 imperfections/displacements (in `surfaces/`) and 10 plants (`categories[0]`
is `3d`).

The user wants a Megascans root in the sidebar to look like Bridge:

    megascans_dl [Megascans]
      3D Assets
        nature
          rock
            cliff
      3D Plants
        grass
      Surfaces
      Decals
      Atlases
      Imperfections
      Displacements

Separately, the 288 3D plants have no geometry in the library (only their atlas material
shows). The user's downloader `C:/Users/Alex/Desktop/scripts/megascans_batch_dl.py` saves
plant meshes flat as `<id>_LOD0.fbx … <id>_LODn.fbx`, numbered by file size. The `Var<n>/`
folders and the real variation/LOD names are lost, and about half of the files are Alembic
(header `Ogawa`) with a `.fbx` extension. The cached asset details
(`C:/Users/Alex/Desktop/scripts/.quixel_asset_cache.json`, key `models`) hold each mesh's
`variation`, `lod`, `mimeType`, `contentLength` and original `uri`
(e.g. `/quixel-megascans-assets/qmBr2/Var1/Var1_LOD0.fbx`). The user chose to fix the
downloader and re-download the plant meshes.

Part 1 carries over review fixes for the parallel scan from the previous plan. Part 2 bumps
`CACHE_VERSION`, which forces a full rescan of ~19,000 folders, and that scan hits these
problems.

## Before starting
Stay on branch `megascans-scan-speed`. Don't commit. Before editing the downloader (Part 3),
copy it to `C:/Users/Alex/Desktop/scripts/megascans_batch_dl.py.bak`. That file is outside
the repo. If your session can't edit it, do Parts 1–2 and report Part 3 as not done.

## Part 1: scan fixes (`python3.13libs/ax_asset_library/`)

### 1.1 Stop superseded scans
- `megascans.scan_root`: add an optional `cancelled=None` parameter (a callable that returns
  bool). In the main loop, check it after each completed job. When it returns True, stop
  submitting jobs, cancel the queued futures (`future.cancel()` on each pending future, or
  `pool.shutdown(cancel_futures=True)`), and return what has been read so far.
- `gsg.scan_root`: pass `cancelled` through, like `progress` and `on_batch`.
- `library.scan_roots`: add `cancelled=None`. Pass it to vendor scans. Check it between
  roots, and stop the loop when it returns True. Keep the per-root `save_index` for roots
  that were scanned. Skip the final `save_index` for a root whose scan was cancelled
  (its batch saves already stored the progress).
- `ui/panel.py`: `_ScanJob.__init__` takes an extra `is_current` callable. `rescan()` passes
  `lambda g=self._scan_generation: g != self._scan_generation` as the cancel test.
  `_ScanJob.run` passes it to `library.scan_roots(..., cancelled=...)`. Reading an int
  attribute from the worker thread is fine; don't call `hou` there.

### 1.2 Unique temporary file per save
- `library.save_index`: name the temporary file `"%s.tmp%d_%d" % (path, os.getpid(),
  threading.get_ident())` so two scans never write the same file. Add `import threading`.

### 1.3 Throttle index saves and grid updates
- `library.scan_roots`, the `on_batch` closure: save the index only if at least
  `INDEX_SAVE_INTERVAL_S = 30` seconds (new module constant) have passed since the last save
  for that root. Call `partial(...)` only if at least `PARTIAL_INTERVAL_S = 2` seconds have
  passed since the last call. Use `time.monotonic()`. The first batch of each root always
  saves and calls `partial`.

### 1.4 Bounded job queue
- `megascans.scan_root`, threaded branch: `submit_new()` submits only while
  `len(pending) < workers * 4`. Behavior is otherwise the same: newly walked folders
  (`"dir"` results) append to `jobs` and get submitted as slots free up.

## Part 2: Bridge categories (`python3.13libs/ax_asset_library/`)

### 2.1 `megascans.py`: kind and category from `assetCategories`
- New constants:
  - `BRIDGE_TYPES = {"3d asset": "3d", "3d plant": "3dplant", "surface": "surface",
    "atlas": "atlas", "decal": "decal", "imperfection": "imperfection",
    "displacement": "displacement", "brush": "brush"}` (keys are lower-cased top keys).
  - `BRIDGE_SECTIONS = (("3d", "3D Assets"), ("3dplant", "3D Plants"), ("surface",
    "Surfaces"), ("decal", "Decals"), ("atlas", "Atlases"), ("imperfection",
    "Imperfections"), ("displacement", "Displacements"), ("brush", "Brushes"))`, in Bridge's
    order.
  - `SECTION_LABELS = dict(BRIDGE_SECTIONS)`.
- `MegascansAsset.__init__`: read `data.get("assetCategories")`. If it's a non-empty dict:
  - take its first key, lower-case it, and look it up in `BRIDGE_TYPES`. If found, it sets
    `self.kind` and overrides the `categories[0]` logic and `kind_hint`.
  - store `self.bridge_path`: walk the value under that key, taking the first key at each
    level until a level is empty or not a dict. Example: `["nature", "rock", "cliff"]`. Use
    `[]` when there's nothing below the top key.
  
  When `assetCategories` is missing or the key is unknown, keep the current kind logic and
  set `self.bridge_path = self.categories` (the current fallback).
- `MegascansAsset.category` (used by `publish.py` when a Megascans asset is added to a
  library, and the folder name there): return
  `"/".join(schema.sanitize_name(c.lower()) for c in self.bridge_path if c.strip())`.
  It no longer includes the section.
- New property `MegascansAsset.browse_category`: the section label
  (`SECTION_LABELS.get(self.kind, "Other")`) followed by the `category` path, joined with `/`.
  Example: `"3D Assets/nature/rock/cliff"`. Section labels are not sanitized (they have
  spaces).
- `build_records`: set the record's category to
  `getattr(asset, "browse_category", None) or asset.category or
  getattr(asset, "fallback_category", asset.kind)`. GSG assets have no `browse_category`,
  so they don't change.
- `plan()`:
  - Make the `megascans_size_m` block apply to `asset.kind in ("atlas", "decal")`. Decals
    used to be `atlas` and had it.
  - In the `PLANT_KINDS` branch, if `variations` is empty (a flat download), fall back to the
    `MESH_KINDS` behavior: geometry from `mesh_files(top)` plus `_material(asset, atlas or
    top, info)`.
- Bump `CACHE_VERSION` to 4.

### 2.2 `ui/panel.py`: section tree for Megascans roots
- `_rebuild_tree` (the `for r in roots:` loop, around line 501): when `r["kind"] ==
  "megascans"`, don't add the per-type level. List
  `library.categories(self.shown_records, root, None)` directly under the root item, with
  scope `("scope", root, None, cat)` and counts from `counts.get((root, None, cat), 0)`.
  Order the top-level sections by their position in `megascans.BRIDGE_SECTIONS` (unknown
  labels last, alphabetically). Keep alphabetical order below them. Other roots don't
  change.
- `library.tree_counts`: also count `(r.root, None, prefix)` for every category prefix, so the
  lookups above work. `(root, None, "")` stays the root total.
- Check that `library.in_scope(rec, root, None, cat)` already matches every type (it does:
  `asset_type is None` skips the type test). Selecting a section then shows geometry and
  material records together.
- Check `_on_tree_context_menu`/the code near line 581 ("vendor categories have no menu")
  still works with `asset_type None` scopes.

### 2.3 Tests (`tests/test_ax_asset_library.py`, `MegascansTests`)
The fixture assets are built around line 590. Add `assetCategories` to some of them in
`setUp` without changing existing ids, or add new fixture assets:
- `test_bridge_kind_and_category`: an asset in an `atlases/` folder with
  `categories: ["atlas", ...]` and `assetCategories: {"decal": {"leakage": {"stamp": {}}}}`
  gets `kind == "decal"`, `category == "leakage/stamp"` and
  `browse_category == "Decals/leakage/stamp"`.
- `test_bridge_category_fallback`: without `assetCategories`, `category` is the same as
  before this change.
- `test_flat_plant_has_geometry`: a `3dplant` asset with top-level `<id>_LOD0.fbx` and no
  `Var*/` folder gives a `geometry` item and a `material` item in `plan()`.
- `test_scan_root_cancelled`: `scan_root(self.dl, workers=4, cancelled=lambda: True)`
  returns without error and with at most `workers * 4` entries read.
- Update existing assertions on Megascans record categories if they change. Keep them
  asserting the new `browse_category` form, not deleted.

## Part 3: downloader (`C:/Users/Alex/Desktop/scripts/megascans_batch_dl.py`)

### 3.1 Keep real mesh names and formats
In `AssetDownloader.process_asset`, the `# --- PROCESS MESHES ---` block (lines ~754–801):
- Build `models = (details or {}).get("models") or []` and a set of used model indexes.
- For each response `mesh`, find its model:
  1. If the response `uri`/`url` path (before `?`) matches `r"Var(\d+)_LOD(\d+)\.(\w+)$"`, use
     those values.
  2. Otherwise, take the first unused model with the same `contentLength`. Prefer one whose
     `mimeType` matches the response mesh's `mimeType`.
  3. Otherwise, keep the current `LOD{index}` naming.
- Skip a mesh whose model `mimeType` is a mesh format other than `MESH_FORMAT` (e.g.
  `application/x-abc` when `MESH_FORMAT` is FBX). Log the count skipped per asset.
- Extension: from the model `mimeType` (`x-fbx` → `fbx`, `x-abc` → `abc`, `x-obj` → `obj`),
  or the current rule when there's no model.
- Destination:
  - With a variation: `dest_dir / f"Var{v}" / f"{asset_id}_Var{v}_LOD{lod}.{ext}"`
    (create the folder). This is the layout `ax_asset_library.megascans.plan()` reads for
    plants.
  - With a LOD only: `dest_dir / f"{asset_id}_LOD{lod}.{ext}"`.
- After download, sniff the header with a new static method `_mesh_format(path)`. It returns
  `"fbx"` for `b"Kaydara FBX Binary"` or `b"; FBX"`, `"abc"` for `b"Ogawa"`, else None.
  If the sniffed format differs from the extension, rename the file to the sniffed extension
  and log a warning. Generalize the existing `.obj` → `.fbx` safety net into this.
  Keep `_is_fbx` if other code uses it.

### 3.2 Re-download only the plant meshes
- New CLI flags in `parse_args`:
  - `--only-plants`: limit `assets` to those whose details have `categories[0] == "3dplant"`
    or an `assetCategories` top key equal to `"3D plant"` (case-insensitive).
  - `--replace-flat-meshes`: in `process_asset`, before the mesh loop, if `dest_dir` has no
    `Var*` subfolder, delete the top-level `<id>_LOD*.fbx`, `<id>_LOD*.obj`, `<id>_LOD*.abc`
    and `<id>_mesh.*` files. Log each deleted file. Only plants are affected
    (`--only-plants` or the same plant test).
  - `--test-id ID` (default `"qcmaW"`): replaces the hard-coded `target_id` in `run_test`.
- In `run_test`, print each response mesh's `uri` (first 90 chars), `mimeType`,
  `contentLength` and the destination path the new naming would use, so the user can check
  the naming on one plant (`--test --test-id qmBr2`) before a full run.
- Textures already on disk pass the existing size check and aren't downloaded again.

## Verification
1. `"C:/Program Files/Side Effects Software/Houdini 22.0.459/bin/hython.exe" -m unittest tests.test_ax_asset_library`
   passes.
2. Read-only check on the real library (run from `python3.13libs/`, `python -I`): run
   `megascans.read_asset` on these folders under `megascans_dl` and print `kind`,
   `category` and `browse_category`:
   - `3d/vmnmaax_Massive Sandstone Cliff` → `3d`, `3D Assets/nature/rock/cliff`
   - `atlases/tdfdfebc_Leakage` → `decal`, `Decals/leakage/stamp`
   - `3d/qmBr2_Grass Clumps` → `3dplant`, `3D Plants/grass/lawn`
   - `surfaces/pkif70_Old Concrete` → `surface`, `Surfaces/concrete/rough`
3. `python -m py_compile C:/Users/Alex/Desktop/scripts/megascans_batch_dl.py`. Don't run
   the downloader itself: it calls the Quixel API with the user's token.
4. Report the changed files and how each step was checked (per CLAUDE.md "Plan handoff").
   List what the user still has to do by hand: run `--test --test-id qmBr2`, then
   `--only-plants --replace-flat-meshes`, then rescan in Houdini.
