# axtools

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

### Wiring Python
- Button callbacks call the module directly:
  `import ax_<name>; ax_<name>.<fn>(kwargs["node"])`. With a PythonModule,
  `hou.phm().<fn>(kwargs)` also works; the PythonModule function only forwards to the
  external module.
- Menu scripts: `import ax_<name>; return ax_<name>.<menu_fn>(kwargs["node"])`.
- Never use a Python SOP, for cooking or for anything else.
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
4. On the definition: `setParmTemplateGroup(...)`, `addSection("OnCreated", ...)`
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
