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

## Changing HDAs
The HDAs in `otls/` are the source of truth and are edited by hand in Houdini
(labels, icons, layout). Never regenerate one from a script. Patch it in place
with hython (`hou.hda.definitionsInFile`, `parmTemplateGroup()` /
`setParmTemplateGroup`, `sections()["Help"].setContents`), back up the file
first, and diff the DialogScript and Help sections before and after.
