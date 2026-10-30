# Translating the dashboard

Names that come from the game (empires, leaders, systems, technologies, civics, ...)
are rendered from the game's own localisation files, in the language of the
`Stellaris language` setting. The dashboard's own text (menus, settings, graph
titles, ledger sentences) is translated with gettext catalogs, so the two match.

The `Dashboard language` setting picks the language of the dashboard's own text.
By default (`Same as Stellaris language`) it follows the `Stellaris language`
setting. It applies immediately, without a restart.

## Files

- `stellarisdashboard/i18n/__init__.py`: the translation functions and the
  mapping from Stellaris languages (`l_german`) to locales (`de`).
- `stellarisdashboard/i18n/locales/messages.pot`: every translatable message,
  generated from the code. Do not edit it by hand.
- `stellarisdashboard/i18n/locales/<locale>.po`: the translations of one
  language. These are standard gettext PO files: edit them with any text editor
  or with a PO editor such as [Poedit](https://poedit.net/). The dashboard reads
  them directly, there is no compile step.
- `stellarisdashboard/i18n/extract.py`: collects the messages from the code and
  updates the PO files.

## Translating

1. Open `stellarisdashboard/i18n/locales/<locale>.po`.
2. Fill in the `msgstr` of each entry. An empty `msgstr` shows the English text.
3. Entries marked `#, fuzzy` are guesses made when the English text changed. They
   are ignored until you check the translation and remove the `fuzzy` flag.
4. Keep every placeholder like `%(country)s` exactly as it is, but move it
   wherever your language's word order needs it. The placeholders are filled with
   names, links or numbers, and the test suite checks that no placeholder was lost
   or misspelled.
5. Messages with a plural form (`msgid_plural`) have one `msgstr[n]` per plural
   form of your language (see the `Plural-Forms` header). The number is `%(num)s`.
6. `#.` comments above an entry are notes for translators, `#:` lines say where
   the message is used. `msgctxt` tells apart identical English texts that may need
   different translations.
7. Use the terms the game uses in your language (resources, leader classes,
   diplomatic agreements, ...), so the dashboard reads like part of the game.

To check a translation, set the `Dashboard language` in the settings and reload
the page.

## Adding translatable text (developers)

Mark text for translation where it is written, and translate it when it is shown:

```python
from stellarisdashboard.i18n import gettext as _, ngettext, pgettext, N_

_("Settings applied.")
_("The %(country)s declared war on the %(target)s.", country=a, target=b)
ngettext("%(num)s envoy", "%(num)s envoys", n)
pgettext("graph tab", "Economy")
```

In Jinja templates, the same functions are available, as well as `{% trans %}`
blocks. Templates use Jinja's "new style" gettext: arguments are escaped, so pass
HTML (such as ledger links) through `| safe`:

```jinja
{{ _("The %(country)s declared war.", country=links[event["country"]] | safe) }}
{% trans %}Return to Top{% endtrans %}
```

Rules:

- **Translate at display time, never at import time.** The language can change
  while the dashboard runs. Text defined in module-level constants is marked with
  `N_("...")` (which only marks it) and passed through `_()` where it is displayed.
- **Translate whole sentences, with named placeholders.** Never build a sentence
  from separately translated fragments, or from English grammar (articles, "a/an",
  plural "s"): other languages need different word order, cases and plural forms.
  It is fine to have several full variants of a sentence.
- **Use `ngettext` for counts**, never `"s" if n != 1`.
- **Add a `# Translators:` comment** (`{# Translators: ... #}` in templates) when a
  message is ambiguous or its placeholders are not self-explanatory.
- **Use `pgettext`** for short texts that could mean different things.
- Do not translate: names from the game, identifiers stored in the database or in
  `config.yml` (only their display), log messages and exceptions meant for
  developers, and command line output.
- Messages in templates are always %-formatted, so a literal `%` in them must be
  written `%%`. In Python, only messages with placeholders are formatted.

After changing messages, update the catalogs (needs the dev dependencies):

```
uv run python -m stellarisdashboard.i18n.extract
```

This regenerates `messages.pot` and merges it into every PO file. The test suite
fails if `messages.pot` is out of date.

Jinja templates kept in Python strings (like the ledger sentences in
`event_registry.py`) are found through the module's `jinja_sources()` function;
modules with such templates are listed in `JINJA_STRING_MODULES` in `extract.py`.

## Adding a language

The dashboard supports the languages of the game. Their locales are listed in
`STELLARIS_LANGUAGE_TO_LOCALE` and `LANGUAGE_NAMES` in
`stellarisdashboard/i18n/__init__.py`; `extract.py` creates a PO file for each.
