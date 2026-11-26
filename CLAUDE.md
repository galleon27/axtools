# axtools

## Plan handoff
Planning happens in a Claude session; implementation runs in a separate DeepSeek-backed
Claude Code session (`dev/implement-plan.ps1`).
- Planning: name exact files, functions and parms in every step. The implementing model
  has none of the planning context.
- Implementing: do the steps in order, don't redesign them. If a step doesn't match the
  code (missing file, renamed function, different signature), stop and report the
  mismatch. All other rules here still apply. Finish with the changed files and how each
  step was checked.

## Code map and search rules
- `python3.13libs/ax_asset_library/`: data (`library.py`, `schema.py`, `prefs.py`);
  import (`importers.py`, `materials.py`, `textures.py`, `megascans.py`, `karma.py`,
  `gsg.py`, `scene.py`, `variants.py`); `publish.py`, `manage.py`, `thumbnails.py`,
  `dragdrop.py`; `ui/` (`panel.py`, `grid.py`, `dialogs.py`).
- `python3.13libs/ax_type/`: type tool (`editor.py`, `layout.py`, `shaping.py`,
  `fonts.py`, `markup.py`, `arrays.py`, `build.py`).
- `scripts/python/`: standalone single-file tools (`ax_notes.py`, `ax_svg_import.py`,
  `asset_manager.py`, `swatches_panel.py`, `flow_ease.py`, ...).
- Tests: `tests/`, run with hython `-m unittest tests.test_ax_asset_library`
  (the `..._hou` variant also needs hython).
- Grep only inside `python3.13libs/` or `scripts/python/`, never the repo root.
- Never open `database/`, `otls/`, `gallery/`, `usd/`, `presets/` (binary or huge; denied
  in `.claude/settings.json`). Inspect HDAs only through hython.
- Symbol lookup: `C:/Users/Alex/AppData/Roaming/Python/Python314/Scripts/codemap.exe find "<name>" -f`
  gives `file:start-end [type] name`; Read just that range. `codemap show <file>` lists
  a file's symbols. After edits run `codemap update --all` (index in `.codemap/`,
  gitignored; config `.codemaprc`).
- Otherwise Grep the symbol, then Read with `offset`/`limit`. Never read whole files of
  1,000+ lines (`ui/panel.py`, `ax_notes.py`, `ax_svg_import.py`, `asset_manager.py`,
  `swatches_panel.py`).
- Don't re-read a file you just edited.

## Writing HDA help and documentation
Run the `avoid-ai-writing` skill (voice profile: technical) on any user-facing text you
write or edit for an HDA or tool: HDA `Help` sections (in the `.hda`/`.hdalc` in
`otls/`), parm tooltips (`help=`), READMEs and other tool docs.

Draft, run the skill in edit mode on just that text, then write it into the file. Keep
Houdini wiki markup (`= Title =`, `@parameters`, `#icon:` etc.) and parm names unchanged;
the skill edits prose only.

## Creating an HDA

### Files
- `otls/AX_<name>.hdalc`: node type `AX_<name>`, label `AX <Name>`.
- Python: package `python3.13libs/ax_<name>/` for several modules, else single file
  `scripts/python/ax_<name>.py`.
- Backups: `otls/backup/AX_<name>_bakN.hdalc`.

### What goes in the HDA
Only the UI: parm templates, Help section, OnCreated defaults (Python, with the
`OnCreated/IsPython` extra file option) and, if needed, a short PythonModule. All other
logic lives in the external module.

### Node color, icon and TAB submenu
Set color in OnCreated with `node.setColor(hou.Color((r, g, b)))` and icon with
`definition.setIcon("<name>")`. Icons live in `config/icons/`, referenced by bare name
(no path or extension); use the same name in the Help `#icon:` line. The TAB menu path
is the `<toolSubmenu>` in the `Tools.shelf` section (Type Properties > Interactive >
Shelf Tools > Context). To change it on an existing HDA, patch `Tools.shelf` in place
(see "Changing HDAs").

| Kind of node | Color (r, g, b) | Icon | Submenu |
|---|---|---|---|
| Importer (reads data into Houdini) | `(1, 0.5, 0.1)` | `ax_import` | `AX/Import` |
| Exporter (writes data out of Houdini) | `(0.8, 0.1, 0.1)` | `ax_export` | `AX/Export` |
| Other, generates geometry | `(0.145, 0.667, 0.557)` | `custom_hda_icon` | `AX` or `AX/<Group>` (e.g. `AX/Camera`) |
| Everything else | no color rule | `custom_hda_icon` | `AX` or `AX/<Group>` |

Importer/exporter is decided by what the node does, not its name.

### Wiring Python
- Button callbacks: `import ax_<name>; ax_<name>.<fn>(kwargs["node"])`. With a
  PythonModule, `hou.phm().<fn>(kwargs)` also works, but it only forwards to the
  external module.
- Menu scripts: `import ax_<name>; return ax_<name>.<menu_fn>(kwargs["node"])`.
- Never use a Python SOP, for cooking or anything else.
- VEX-based tools stay as wrangles inside the HDA. Don't write or call their logic from
  external Python, and don't add Python callbacks or helper buttons to them unless asked.
- An HDA that creates geometry always has a Reload button that regenerates the geometry,
  e.g. by writing it into a Stash SOP inside the HDA.
- A package with several modules gets a `dev_reload()` that runs `importlib.reload` on
  each submodule.

### Code patterns
- Read parms through a helper that returns `None` for a missing parm, so older instances
  keep working after new parms are added.
- Raise `hou.NodeError` for fatal errors. Collect soft problems and report each unique
  message once with `node.addWarning`.
- Keep expensive caches at module level and give the user a way to clear them.

### Parm layout
- Main action button and main input at the top, then output options.
- Group settings with Simple folders (`hou.folderType.Simple`), not tabs.
- Multiparm block parms use `#` (`name#`).
- Every parm gets a `help=` tooltip.

### Help section
Wiki header (`= AX <Name> =`, `#type: node`, `#context:`, `#internal: AX_<name>`,
`#icon:`, `#tags:`), then a one-line `"""summary"""`, `== Overview ==`, `@parameters`.

### First build
One-off hython script, kept in the scratchpad (not the repo), run with
`"C:/Program Files/Side Effects Software/Houdini 22.0.459/bin/hython.exe"`:
1. Create `/obj/geo` → `subnet`, build the internal network ending in `output0`.
2. `sub.createDigitalAsset(name=..., hda_file_name=..., description=...)`.
3. On the definition: `setParmTemplateGroup(...)`, `setIcon(...)`,
   `addSection("OnCreated", ...)` (with node color) plus
   `setExtraFileOption("OnCreated/IsPython", True)`, `addSection("Help", ...)`, and if
   needed `addSection("PythonModule", ...)` plus
   `setExtraFileOption("PythonModule/IsPython", True)`.
4. Delete the script. From then on, follow "Changing HDAs".

## Changing HDAs
The HDAs in `otls/` are the source of truth, edited by hand in Houdini (labels, icons,
layout). Never regenerate one from a script. Patch in place with hython
(`hou.hda.definitionsInFile`, `parmTemplateGroup()` / `setParmTemplateGroup`,
`sections()["Help"].setContents`): back up the file first, and diff the DialogScript and
Help sections before and after.
