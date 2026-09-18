# features/

Your code goes here. One file per feature, or a folder with `__init__.py` for
anything bigger.

Everything in this directory is imported automatically at startup. You never
need to edit a file outside it.

**Read [../docs/FEATURE_GUIDE.md](../docs/FEATURE_GUIDE.md) first** — it is
short and it is the whole contract.

For a reference, read `files.py` (every tier, the undo-note pattern) and
`organize.py` (a READ plan / DANGER apply split). `_util.py` holds shared
helpers -- anything that shells out should use `_util.run`, which adds the
timeout, output cap, and mid-command kill switch for you. Modules starting
with `_` are not loaded as features.

Settings a feature reads live under `features:` in `config/policy.yaml`
(read them with `_util.feature_setting`). That file is the owner's.

Claimed name prefixes — add yours here so two people don't pick the same one:

| Prefix | Track | Owner | Status |
|---|---|---|---|
| `files.*` | A | files.py | done -- trash-not-delete, restore |
| `notes.*` | A | notes.py | done |
| `organize.*` | A | organize.py | done -- dry-run plan, approved apply |
| `git.*` | A | git_tools.py | done -- push is DANGER, never forces |
| `shell.*` | A | shell.py | done -- allowlist in policy.yaml |
| `code.*` | A | code_tools.py | done -- patches refused on main/master |
| `sys.*` | A | sys_tools.py | done -- needs psutil |
| `notify.*` | B | | done |
| `clipboard.*` | B | | done |
| `screen.*` | B | | done |
| `speech.*` | B | | done |
| `watch.*` | B | | done |
| `web.fetch` | B | | done |
| `web.search` | B | | done — Google CSE (needs `GOOGLE_CSE_ID`) or DuckDuckGo |
| `skill.*` | core | — | done — self-extension, do not edit casually |
| `browser.*` | B | | TODO — browser-use |
| `mail.*` | B | | TODO — needs Gmail OAuth |
