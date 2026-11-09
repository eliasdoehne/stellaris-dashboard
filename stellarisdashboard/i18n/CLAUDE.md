# Translation work

- Delegate rote translation work to a subagent with `model: "sonnet"`. That means machine-translating new msgids into the locale `.po` files, filling `msgstr` entries, and bulk catalog edits. Don't do it on the main model; it's not worth the usage quota.
- Give that subagent the exact files and the new msgids, and point it at `docs/translating.md` (placeholders, plurals, `fuzzy`, game terminology).
- Marking strings with `_()`/`ngettext` in feature code and running `extract.py` stay with the main agent. Check the subagent's result yourself (`test/i18n_test.py`, `test/ledger_i18n_test.py`) before committing.
- When spawning feature agents that add UI text, pass this instruction on in their prompt.
