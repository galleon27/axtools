# AX Asset Library: watch folders and C4D Asset Browser features

## Context
The library lists only folders that hold an `asset.json`, so plain folders of models, USD
components (Component Builder) and textures stay invisible. Cinema 4D's Asset Browser has the
same split: managed **Databases** and read-only **Watch Folders**. C4D Watch Folders can't take
keywords and get re-read on every start. We add a watch-folder root kind that is cached (the
vendor-root index) and that you can tag, plus the C4D features the user picked: smart
searches, promoting a watched asset into the library, and publishing a SOP with its materials
and textures (C4D "dependencies").

Delivery: this content goes into `PLAN.md` at the repo root for the DeepSeek session
(`dev/implement-plan.ps1 -Plan PLAN.md`). Before that: create branch `asset-browser-folders`
and make a checkpoint commit of pending work (only `otls/AX_Init_Orient.hdalc` is modified;
ask the user first). The implementer does not commit. Each phase ends with the tests passing.

Tests: `hython -m unittest tests.test_ax_asset_library` (plain) and
`tests.test_ax_asset_library_hou` (needs hython). Run `codemap update --all` after each phase.

---

## Phase 1: "Folder" root kind (watch folder)

### 1.1 New `python3.13libs/ax_asset_library/folders.py` (no hou/Qt imports, like `gsg.py`)
It reuses `megascans.scan_root` and `megascans.build_records` the way `gsg.py` does.
- Constants: `USD_EXTS = {".usd", ".usda", ".usdc", ".usdz"}`;
  `SUBLAYER_STEMS = {"geo", "mtl", "payload", "proxy", "sim_proxy", "base", "contrib",
  "material", "materials"}`; `TEXTURE_DIRS = {"maps", "textures", "texture", "tex"}`;
  `NON_ASSET_DIRS = {"thumbs", "previews", ".trash"}` plus `TEXTURE_DIRS`;
  `THUMB_SUFFIXES` / `THUMB_NAMES` from `publish.py` lines 15-16 (copy the values; don't
  import `publish`, it imports hou).
- `class FolderAsset`: the attributes `build_records` reads (`id`, `folder`, `preview`,
  `tags=[]`, `category=""`, `kind="folder"`, `fallback_category=""`, `browse_category`),
  plus `items` (list of plan tuples) and `descend` (bool).
- `read_asset(folder, names=None, hint=None)`: thread-safe, reads file names only. Rules,
  for the folder's direct files:
  1. **USD component**: a USD file whose stem equals the folder name (case-insensitive) is
     the entry. Then USD files with a stem in `SUBLAYER_STEMS` are not assets. The component
     makes one geometry item named after the folder, with
     `info = {"folder_id": <id>, "component_dir": folder}`.
  2. **Geometry**: the remaining `schema.is_geometry` files, grouped by
     `schema.strip_ext(...).lower()`. Each group is one geometry item with
     `{schema.format_key(f): f}`. Skip a `.mtl` next to an `.obj` (`publish.COMPANION_EXTS`
     pattern).
  3. **Textures**: images go to `textures.group(images)`, which returns
     `(sets, leftovers, previews)`. Each set becomes a material item with
     `info = {"channels": sorted(ts.channels), "folder_id": ...}` and `resolution` from
     `megascans._resolution(ts.channels)`. Each leftover is an `hdri` item when
     `textures.looks_like_hdri(p)` is true, otherwise an `image` item. Preview images and
     images used as geometry thumbnails are not assets.
  4. **Model textures**: when the folder has geometry and a subfolder in `TEXTURE_DIRS`, run
     the subfolder's images through `textures.group` and add the first set as a material
     item with the same `folder_id` as the geometry. `library.companion_keys` and
     `variants.linked_material` then hide it from the grid and link it, because the id prefix
     (`folder:<id>`) matches. Give the geometry and that material the same asset `id`.
  5. **Geometry thumbnail**: an image with stem `<stem>` + `THUMB_SUFFIXES`, or one of
     `THUMB_NAMES` when the folder has a single geometry item. Store it in `asset.preview`
     when the folder yields one asset. Otherwise store it per item in `info["preview"]`.
     Check whether `build_records` only uses `asset.preview`. If so, add an optional
     per-item override in `build_records` (`info.get("preview") or asset.preview`).
  6. Return None when no items were made (the scan walks deeper). Set
     `descend = True` when the folder has subfolders outside `NON_ASSET_DIRS` and was not
     a USD component.
  - Ids: `asset.id = schema.sanitize_name(basename) + "_" + sha1(normcase(abspath(folder)))[:8]`.
    When a folder yields several unrelated assets (rule 2/3 groups), each item needs its own
    id. Pass ids through `info["variation"] = <stem>`, which `megascans.record_id` already
    appends.
- `plan(asset)` returns `asset.items`.
- `records_for(asset, root, thumb_dir, thumbs, mtime)`: set `asset.browse_category` to the
  folder's path relative to `root` (`/` separators). Use the parent folder instead when the
  folder is a USD component or holds exactly one geometry asset. Then call
  `megascans.build_records(asset, plan(asset), root, thumb_dir, thumbs, mtime, "folder", "Folder")`.
- `looks_like_library(path)`: returns False when `megascans.looks_like_download(path)` or
  `gsg.looks_like_library(path)` is true, or when any `asset.json` sits two levels down
  (a library root). Otherwise returns True when a walk limited to depth 3 and 200 folders
  finds a folder that `read_asset` recognizes.
- `scan_root(root, cached=None, thumb_dir="", progress=None, on_batch=None, cancelled=None)`
  calls `megascans.scan_root(..., reader=read_asset, recorder=records_for, kind_folders={},
  skip_dirs=NON_ASSET_DIRS, ...)`.

### 1.2 `megascans.py` hooks (Megascans/GSG behaviour unchanged by default)
- `scan_root(..., skip_dirs=())`: `walk()` ignores subfolders whose lowercase name is in
  `skip_dirs`.
- `_read_folder`: put `"descend": bool(getattr(asset, "descend", False))` in the entry.
- `scan_root.handle`, status `"asset"`: if `entry.get("descend")` and `depth < MAX_DEPTH`,
  `walk(path, depth + 1, hint)`. Do the same in `walk()` for a reused cached entry.
- `CACHE_VERSION` 4 -> 5.

### 1.3 Wiring
- `schema.py` lines 24-26: `VENDOR_KINDS = ("megascans", "gsg", "folder")`,
  `ROOT_KIND_LABELS["folder"] = "Folder (watch)"`.
- `library.py` `scan_roots` (line ~317): replace the gsg/megascans ternary with a dict
  `{"megascans": megascans.scan_root, "gsg": gsg.scan_root, "folder": folders.scan_root}`.
  Mention `"folder"` in the `_root_kind` and `scan_roots` docstrings.
- `variants.linked_material` line 130: add `"folder_id"` to the info keys.
- `ui/dialogs.py` `_browse` (lines 298-300): after the megascans/gsg checks,
  `folders.looks_like_library(d)` gives kind `"folder"` and variable name `"FOLDER"`.
- `ui/panel.py` `_guess_vendor` (409-416): add the same check. Update the docstring.
- `__init__.py` line 15: add `"folders"` to `_SUBMODULES`.

### 1.4 Tests (`tests/test_ax_asset_library.py`, new `FoldersTests`, temp trees)
- `cat/Chair/{Chair.usd, geo.usd, mtl.usd, thumbnail.png, maps/Chair_basecolor.png,
  maps/Chair_roughness.png}`: `browsable` gives 1 geometry, category `cat`, key `usd`,
  thumb = thumbnail.png. `companion_keys` hides the material, and
  `variants.linked_material` finds it.
- `props/{Rock.abc, Rock.bgeo.sc, Rock_preview.jpg, Pot.fbx}`: 2 geometry records, Rock
  with 2 format keys and the preview as thumb.
- `tex/{Brick_basecolor.jpg, Brick_roughness.jpg, Brick_normal.jpg}`: 1 material with 3
  channels.
- `a/b/Lamp/Lamp.usdc`: category `a/b`.
- `mixed/{Tree.abc, sub/Bush.abc}`: both found (`descend`).
- Second `scan_root` with the returned entries gives the same record keys. Existing
  `MegascansTests` / `GsgTests` still pass.

---

## Phase 2: Tags and notes on watch-folder and vendor assets
Per-user, kept in prefs like favorites (`prefs.py` lines 194-242), so nothing is written to the folder.
- `prefs.py` `DEFAULTS`: `"asset_meta": {}` (`{asset id: {"tags": [..], "description": str}}`).
  New `asset_meta(settings)` returns a dict and drops bad entries.
  New `set_asset_meta(settings, asset_id, tags, description)` runs `schema.clean_tags`; it
  removes the entry when both are empty, then saves.
- `library.py`: new `apply_meta(records, meta)`. For records with
  `source_kind in schema.VENDOR_KINDS` and an id in `meta`: on first call keep the scanned
  tags in `rec.info["base_tags"]`. Then set `rec.tags = clean_tags(base_tags + meta tags)`
  and set `rec.description` when the meta has one. Make it idempotent, so removing a tag
  works without a rescan.
- `ui/panel.py` `records` setter (line 144): call
  `library.apply_meta(records, _prefs.asset_meta(self.settings))` before `browsable`.
- `ui/panel.py` `_on_selection_changed` (line 761): `editable` is also true for a single
  vendor record with an id. Check that `details.set_records(..., editable)` only gates the
  tags/description editor (`details.py` lines 261-277). If it gates more, stop and report.
- `ui/panel.py` `_save_meta` (1095): for a vendor record, call
  `_prefs.set_asset_meta(...)`, then `self.records = self.records`, `_rebuild_tree()`,
  `_apply_filter()` (no rescan), and show the message "<name>: tags saved (stored in your
  preferences)."
- `details.py` line 277 read-only hint stays for read-only *library* roots.
- Tests: `PrefsTests` (set/clear meta), `QueryTests` (`tag:` finds a meta tag; removing it
  via `apply_meta` again drops it).

---

## Phase 3: Smart searches
- `prefs.py` `DEFAULTS`: `"smart_searches": []` (`[{"name": str, "query": str}]`). New
  `smart_searches(settings)`, `save_smart_search(settings, name, query)` (replaces same
  name), `remove_smart_search(settings, name)`.
- `library.py` `Query`: add fields `root` (substring of `os.path.basename(rec.root)`,
  lowercased) and `kind` (`rec.source_kind` when in `VENDOR_KINDS`, else `"library"`). Add
  them to `QUERY_FIELDS` and to the docstring's field list.
- `ui/panel.py` `_rebuild_tree` (after "In this scene", line ~488): when there are saved
  searches, add a group "Smart searches" with one item per search, scope
  `("smart", name)`, count = records in `shown_records` that `library.Query(query, favs, labels)`
  matches. Tooltip: the query text.
- `_apply_filter` (662): scope `"smart"` filters by that stored query, ANDed with the search
  field.
- `_build_ui`: a tool button next to `self.search`, tooltip "Save this search in the
  sidebar.", enabled when the field has text. It asks for a name with `hou.ui.readInput`
  and calls `save_smart_search`, then `_rebuild_tree`.
- `_tree_menu` (574): it returns early unless the scope is `"scope"`. Add a `"smart"` branch
  with "Edit query...", "Rename...", "Delete". Keep the comment about not holding
  `QTreeWidgetItem`s.
- Tests: `QueryTests` for `root:` and `kind:folder`; `PrefsTests` for save/replace/remove.

---

## Phase 4: "Add to library" from watch folders and vendor roots
- `publish.py` `IngestItem.__init__` (line 150): new keyword `tree=None` (a folder to copy
  whole). In `_fill_asset` (302): when `copy` and `item.tree`, use
  `shutil.copytree(item.tree, vdir, dirs_exist_ok=True)` and record each `item.files` value
  as its path relative to `item.tree`, inside `vdir`. Without `copy`, keep absolute paths as
  today. This keeps a USD component's relative sublayer and texture paths valid.
- `publish.py` new `plan_from_records(recs, records_all)` returns an `IngestPlan`. For each
  record with `source_kind in schema.VENDOR_KINDS`, make an `IngestItem(rec.type, rec.name,
  rec.files, thumb=<rec.thumb if the file exists>, info=<rec.info without "preview",
  "base_tags">, category=rec.category, tags=rec.tags, source={"kind": rec.source_kind,
  "origin_path": rec.asset_dir}, tree=rec.info.get("component_dir"))`. For geometry, also
  add its `variants.linked_material(rec, records_all)` as a material item (once). The
  copied `megascans_id` / `gsg_id` / `folder_id` keep the link in the library.
- `ui/panel.py` `_context_menu` (916): when any selected record is in a vendor root, add
  "Add to library..." after "Copy path". It calls
  `self._ingest([], plan=publish.plan_from_records(vendor_recs, self.records))`.
  `IngestDialog` already handles the copy/category/tags choices.
- Tests (`test_ax_asset_library_hou.py`, `ImportFeatureTests`): scan a Phase 1 temp tree,
  then `plan_from_records` + `ingest` into a temp library root. The Chair component folder
  is copied whole (`v001/geo.usd`, `v001/maps/...` exist) and the library scan finds it with
  its linked material.

---

## Phase 5: Publish a SOP with its materials (dependencies)
Renderer-agnostic: the assigned material nodes are saved as nodes, and their textures are
copied into the version folder.

### 5.1 New `python3.13libs/ax_asset_library/material_bundle.py` (uses hou)
- `PLACEHOLDER = "__AX_VERSION_DIR__"`, `BUNDLE_FILE = "materials.cpio"`,
  `TEXTURE_DIR = "textures"`.
- `assigned(sop, geo)` returns `({attr value: hou.Node}, warnings)`. It reads the unique
  prim values of `shop_materialpath` (and the detail value if present), resolves each with
  `sop.node(value)`, and adds a warning for values that don't resolve.
- `texture_parms(nodes)` returns `[(hou.Parm, expanded path)]`: string parms whose
  `parmTemplate().stringType() == hou.stringParmType.FileReference`, over each node and
  `allSubChildren()`, non-empty, and existing on disk. `<UDIM>`, `%(UDIM)d` and `<udim>`
  are expanded to a glob matching all tiles.
- `write(vdir, sop, geo)` returns `(info, warnings)`:
  1. Get the materials from `assigned`. Give each a unique saved name (node name, `_1`
     suffix on clashes) and build `rename = {attr value: saved name}`.
  2. Copy the nodes with `hou.copyNodesTo` into a temp matnet
     (`hou.node("/mat").createNode("matnet", "__ax_publish_tmp")` or similar; destroy it in
     `finally`), then rename the copies.
  3. For each texture parm on the copies, copy the file(s) into `vdir/textures/` (reuse
     `publish._copy_into` for unique names) and set the parm to
     `PLACEHOLDER + "/textures/<name>"`, keeping UDIM tokens.
  4. `tmp.saveItemsToFile(tmp.children(), os.path.join(vdir, BUNDLE_FILE))`.
  5. Return `info = {"file": BUNDLE_FILE, "names": [...], "textures": [...rel paths]}`
     and `rename`.
- `rewrite_attr(geo, rename)`: set `shop_materialpath` prim values to the saved names
  (`geo.setPrimStringAttribValues` or per-prim set).
- `load(bundle_info, version_dir_parm, container, asset_name)` returns
  `(subnet, {saved name: node})`. It creates a `subnet` named `<asset>_materials` inside
  `importers.local_matnet(container)`, so names never clash, and runs
  `subnet.loadItemsFromFile(path)`. Then for every string parm under it, replaces
  `PLACEHOLDER` in `parm.unexpandedString()` with `version_dir_parm`. Get that value from
  `prefs.parm_path(version_dir, root, path_mode)` so `$VAR` roots stay tokenized.

### 5.2 Publish side
- `schema.add_version` gets an optional `materials=None`, stored as `version["materials"]`.
  `library._record_from_sidecar` (line 107) copies it into each `version_list` entry,
  with paths resolved.
- `publish.publish_node` (497): new kwarg `materials=False`. When true, after
  `prepare_geometry`: get the SOP the same way `node_geometry` does, call
  `material_bundle.write(vdir, sop, geo)` after `os.makedirs(vdir)` and before the format
  loop, then `rewrite_attr(geo, rename)`, so every format gets the saved names. Pass the
  info to `add_version(..., materials=info)` and extend `result.warnings`. A node with no
  `shop_materialpath` adds the warning "no assigned materials found".
- `ui/dialogs.py` `PublishDialog` (format row ~510-522): checkbox "Include materials",
  tooltip "Save the assigned materials and copy their textures into the asset.",
  default `settings.get("publish_materials")`. Add `"materials"` to `values()` (~649).
- `prefs.DEFAULTS`: `"publish_materials": True`.
- `ui/panel.py` `_publish` (1279): save the setting and pass `materials=v["materials"]`.

### 5.3 Import side (`importers.py` `_import_geometry`, line 378)
- Get the picked version's `materials` from `rec.version(version)` (or the current one).
- When it exists and no linked material `mrec` is used: in the SOP and OBJ branches, call
  `material_bundle.load(...)` with `container=network` (SOP) or `geo` (OBJ). Then add a
  Material SOP after the last node: new helper `_bundle_material_sop(parent, nodes, name)`
  with `num_materials = len(nodes)`, `group<i> = '@shop_materialpath="<saved name>"'` and
  `shop_materialpath<i>` = relative path to the node. Set display/render flags the same way
  the linked-material code does, then `_tag_node`.
- LOP context: leave the bundle unused and document it ("bundled materials import into SOP
  and OBJ networks"). If `import_records` has a way to report a warning, use it; otherwise
  only document it.

### 5.4 Tests (`test_ax_asset_library_hou.py`)
- Build `/obj/geo` (box + Material SOP) pointing to a `/mat` Principled Shader whose
  basecolor texture is a temp PNG. Publish to a temp root with `materials=True`. Check that
  `v001/materials.cpio` and `v001/textures/<png>` exist, and that `asset.json` has
  `versions[0].materials.names`.
- Import into a new geo: a `<asset>_materials` subnet with the shader exists, its texture
  parm points into the asset folder (no placeholder left), and the Material SOP group
  matches all prims.

---

## Verification (end to end, in Houdini)
1. Settings: add the USD component folder and a texture folder. The type auto-picks
   "Folder (watch)". The scan finds one asset per component with thumbnails, and model
   textures are linked, not listed.
2. Tag a watch-folder asset, search `tag:<tag>`, remove the tag, and check it's gone
   without a rescan.
3. Save `kind:folder type:geo` as a smart search, add a file to the folder, rescan, and
   check that the count updates.
4. "Add to library..." on a component, then import it from the library into LOPs
   (Reference LOP, materials intact).
5. Publish a SOP with a textured material, import it into a new geo, and render.

## Notes for the user afterwards
- In LOPs, USD components keep their materials (they live in `mtl.usd`). In SOP/OBJ, a File
  SOP brings only geometry.
- Bundled SOP materials import into SOP/OBJ only, and Karma thumbnails render them grey (as
  now).
- Component Builder's file set varies by version. If yours differs, extend `SUBLAYER_STEMS`.
