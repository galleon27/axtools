# axtools

## Plan handoff
Planning happens in a Claude session; implementation runs in a separate Claude Code
session backed by DeepSeek (`dev/implement-plan.ps1`).
- When planning: name exact files, functions and parms in every step. A different
  model carries the plan out and has none of the planning context.
- When implementing a plan file: do its steps in order and don't redesign them. If a
  step doesn't match the code (missing file, renamed function, different signature),
  stop and report the mismatch instead of improvising. All other rules in this file
  still apply. Finish with a list of changed files and how each step was checked.

## Code map and search rules
- `python3.13libs/ax_asset_library/`: asset library. `library.py`, `schema.py`, `prefs.py`
  (data); `importers.py`, `materials.py`, `textures.py`, `megascans.py`, `karma.py`,
  `gsg.py`, `scene.py`, `variants.py` (import); `publish.py`, `manage.py`,
  `thumbnails.py`, `dragdrop.py`; `ui/` (`panel.py`, `grid.py`, `dialogs.py`).
- `python3.13libs/ax_type/`: type tool (`editor.py`, `layout.py`, `shaping.py`, `fonts.py`,
  `markup.py`, `arrays.py`, `build.py`).
- `scripts/python/`: standalone single-file tools (`ax_notes.py`, `ax_svg_import.py`,
  `asset_manager.py`, `swatches_panel.py`, `flow_ease.py`, ...).
- Tests: `tests/`, run with hython `-m unittest tests.test_ax_asset_library`
  (`..._hou` variant needs hython).
- Search with Grep scoped to `python3.13libs/` or `scripts/python/`, never the repo root.
- Don't open `database/`, `otls/`, `gallery/`, `usd/`, `presets/` (binary or huge; denied in
  `.claude/settings.json`). HDAs are inspected only through hython.
- Symbol index: `C:/Users/Alex/AppData/Roaming/Python/Python314/Scripts/codemap.exe find "<name>" -f` returns `file:start-end [type] name`; then Read just that range. `codemap show <file>` lists a file's symbols. Refresh with `codemap update --all` after edits (index is in `.codemap/`, gitignored; config in `.codemaprc`).
- Otherwise Grep for the symbol first, then Read with `offset`/`limit`. Files of 1,000+ lines
  (`ui/panel.py`, `ax_notes.py`, `ax_svg_import.py`, `asset_manager.py`,
  `swatches_panel.py`) are never read whole.
- Don't re-read a file you just edited.

## Writing HDA help and documentation
Any time you write or edit user-facing text for an HDA or tool, invoke the
`avoid-ai-writing` skill (voice profile: technical) on it before finishing. This covers:
- HDA Help sections (the `Help` section stored in the `.hda`/`.hdalc` in `otls/`)
- parm tooltips (`help=` arguments on parm templates)
- READMEs and other docs for the tools

Draft the text, run the skill in edit mode on just that text, then write it into the file.
Keep Houdini wiki markup (`= Title =`, `@parameters`, `#icon:` etc.) and parm names
unchanged; the skill edits prose only.

## Creating an HDA

### Files
- `otls/AX_<name>.hdalc`: node type name `AX_<name>`, label `AX <Name>`.
- Python: a package in `python3.13libs/ax_<name>/` when the tool needs several
  modules, otherwise a single file `scripts/python/ax_<name>.py`.
- Backups go to `otls/backup/AX_<name>_bakN.hdalc`.

### What goes in the HDA
The HDA stores the UI: parm templates, the Help section, OnCreated defaults (Python,
with the `OnCreated/IsPython` extra file option) and, if needed, a short PythonModule.
All other logic lives in the external module.

### Node color and icon
Set the color in the OnCreated script with `node.setColor(hou.Color((r, g, b)))` and the
icon with `definition.setIcon("<name>")`. Icons live in `config/icons/` and are referenced
by bare name, without a path or extension. Use the same name in the Help `#icon:` line.

| Kind of node | Color (r, g, b) | Icon |
|---|---|---|
| Importer (reads data into Houdini) | `(1, 0.5, 0.1)` | `ax_import` |
| Exporter (writes data out of Houdini) | `(0.8, 0.1, 0.1)` | `ax_export` |
| Anything else that generates geometry | `(0.145, 0.667, 0.557)` | `custom_hda_icon` |
| Everything else | no color rule | `custom_hda_icon` |

Importers and exporters are decided by what the node does, not by its name. The default
icon is `custom_hda_icon` unless the node is an importer or exporter.

### TAB menu submenu
The TAB menu path is stored in the `Tools.shelf` section (`<toolSubmenu>`), set in the
Type Properties under Interactive > Shelf Tools > Context.

| Kind of node | Submenu |
|---|---|
| Importer | `AX/Import` |
| Exporter | `AX/Export` |
| Everything else | `AX` (or `AX/<Group>` for a group like `AX/Camera`) |

Use the same importer/exporter decision as for color and icon. To change it on an
existing HDA, patch the `Tools.shelf` section in place (see "Changing HDAs").

### Wiring Python
- Button callbacks call the module directly:
  `import ax_<name>; ax_<name>.<fn>(kwargs["node"])`. With a PythonModule,
  `hou.phm().<fn>(kwargs)` also works; the PythonModule function only forwards to the
  external module.
- Menu scripts: `import ax_<name>; return ax_<name>.<menu_fn>(kwargs["node"])`.
- Never use a Python SOP, for cooking or for anything else.
- VEX-based tools stay as wrangles inside the HDA. Don't write or call their logic from
  an external Python script, and don't add Python callbacks or helper buttons to them
  unless asked.
- An HDA that creates geometry always has a Reload button. Its callback regenerates
  the geometry, for example by writing it into a Stash SOP inside the HDA.
- A package with several modules gets a `dev_reload()` that runs `importlib.reload`
  on each submodule, so code edits apply without restarting Houdini.

### Code patterns
- Read parms through a helper that returns `None` for a missing parm, so older
  instances of the HDA keep working after new parms are added.
- Raise `hou.NodeError` for fatal errors. Collect soft problems and report each
  unique message once with `node.addWarning`.
- Keep expensive caches at module level and give the user a way to clear them.

### Parm layout
- Put the main action button and the main input at the top, then the output options.
- Separate groups of settings with Simple folders (`hou.folderType.Simple`), not tabs.
- Name parms inside multiparm blocks with `#` (`name#`).
- Give every parm a `help=` tooltip.

### Help section
Start with the wiki header (`= AX <Name> =`, `#type: node`, `#context:`,
`#internal: AX_<name>`, `#icon:`, `#tags:`), then a one-line `"""summary"""`,
`== Overview ==` and `@parameters`.

### First build
Build the first version with a one-off hython script:
1. Write the script in the scratchpad, not the repo, and run it with
   `"C:/Program Files/Side Effects Software/Houdini 22.0.459/bin/hython.exe"`.
2. Create `/obj/geo` → `subnet`, then build the internal network ending in `output0`.
3. Call `sub.createDigitalAsset(name=..., hda_file_name=..., description=...)`.
4. On the definition: `setParmTemplateGroup(...)`, `setIcon(...)` and
   `addSection("OnCreated", ...)` (including the node color, see "Node color and icon")
   with `setExtraFileOption("OnCreated/IsPython", True)`, `addSection("Help", ...)`,
   and if needed `addSection("PythonModule", ...)` with
   `setExtraFileOption("PythonModule/IsPython", True)`.
5. Delete the script. From then on, follow "Changing HDAs".

## Changing HDAs
The HDAs in `otls/` are the source of truth and are edited by hand in Houdini
(labels, icons, layout). Never regenerate one from a script. Patch it in place
with hython (`hou.hda.definitionsInFile`, `parmTemplateGroup()` /
`setParmTemplateGroup`, `sections()["Help"].setContents`), back up the file
first, and diff the DialogScript and Help sections before and after.
