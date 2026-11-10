# axtools

## Writing HDA help and documentation
Any time you write or edit user-facing text for an HDA or tool, invoke the
`avoid-ai-writing` skill (voice profile: technical) on it before finishing. This covers:
- HDA Help sections (`HELP` strings passed to `defn.addSection("Help", ...)` in
  `scripts/python/build_*_hda.py`)
- parm tooltips (`help=` arguments on parm templates)
- READMEs and other docs for the tools

Draft the text, run the skill in edit mode on just that text, then write it into the file.
Keep Houdini wiki markup (`= Title =`, `@parameters`, `#icon:` etc.) and parm names
unchanged; the skill edits prose only.
