"""Translation of the dashboard's own interface text.

Names coming from the game (empires, leaders, technologies, ...) are already
localised by the game's own files, in the language set by ``stellaris_language``.
This module translates the text the dashboard adds around them, so the two match.

Messages are marked in Python with :func:`gettext` (alias ``_``), :func:`ngettext`,
:func:`pgettext` and :func:`npgettext`, and in Jinja templates with the same
functions or ``{% trans %}`` blocks. Text defined at import time (module-level
constants) is marked with :func:`N_` and translated where it is displayed.

Translations live in ``locales/<locale>.po`` (standard gettext PO files, editable
with Poedit, Weblate, ...). They are read directly at runtime, so there is no
compile step. ``locales/messages.pot`` lists all messages; it is regenerated, and
the PO files are updated from it, with ``uv run python -m stellarisdashboard.i18n.extract``.
See docs/translating.md.
"""
import ast
import functools
import gettext as _gettext
import logging
import pathlib
import re
from typing import Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

LOCALES_DIR = pathlib.Path(__file__).parent / "locales"

DEFAULT_LOCALE = "en"

# Stellaris language key -> dashboard locale. These are all languages the game ships.
STELLARIS_LANGUAGE_TO_LOCALE = {
    "l_english": "en",
    "l_braz_por": "pt_BR",
    "l_french": "fr",
    "l_german": "de",
    "l_japanese": "ja",
    "l_korean": "ko",
    "l_polish": "pl",
    "l_russian": "ru",
    "l_simp_chinese": "zh_CN",
    "l_spanish": "es",
}

# Locale -> name of the language in that language, for the settings page.
LANGUAGE_NAMES = {
    "en": "English",
    "pt_BR": "Português do Brasil",
    "fr": "Français",
    "de": "Deutsch",
    "ja": "日本語",
    "ko": "한국어",
    "pl": "Polski",
    "ru": "Русский",
    "zh_CN": "简体中文",
    "es": "Español",
}

# Value of the dashboard_language setting that follows stellaris_language.
AUTO = "auto"

# separates context and message ID in catalog keys, like in compiled gettext catalogs
_CONTEXT_SEPARATOR = "\x04"


def N_(message: str) -> str:
    """Mark a message for translation without translating it (yet).

    For text defined before the language is known, such as module-level constants.
    Pass the value through gettext where it is displayed."""
    return message


def current_locale() -> str:
    """The locale of the dashboard interface, from the dashboard_language setting."""
    from stellarisdashboard import config

    cfg = config.CONFIG
    if cfg is None:
        return DEFAULT_LOCALE
    return resolve_locale(cfg.dashboard_language, cfg.stellaris_language)


def resolve_locale(dashboard_language: Optional[str], stellaris_language: Optional[str]) -> str:
    if dashboard_language and dashboard_language != AUTO:
        if dashboard_language in LANGUAGE_NAMES:
            return dashboard_language
        logger.warning(f"Unknown dashboard language {dashboard_language!r}, using {DEFAULT_LOCALE}")
        return DEFAULT_LOCALE
    return STELLARIS_LANGUAGE_TO_LOCALE.get(stellaris_language, DEFAULT_LOCALE)


def html_lang(locale: str) -> str:
    """The locale as a BCP 47 tag for the html lang attribute (pt_BR -> pt-BR)."""
    return locale.replace("_", "-")


class POTranslations(_gettext.NullTranslations):
    """gettext translations read from a PO file.

    Untranslated and fuzzy entries fall back to the message ID, like in GNU gettext."""

    def __init__(self, path: Optional[pathlib.Path] = None):
        super().__init__()
        self._catalog: Dict[str, Union[str, Dict[int, str]]] = {}
        self._plural = lambda n: int(n != 1)
        if path is not None:
            self._load(path)

    def _load(self, path: pathlib.Path):
        for entry in _parse_po(path.read_text(encoding="utf-8")):
            msgid = entry.get("msgid")
            if msgid is None or "fuzzy" in entry["flags"]:
                continue
            if msgid == "":
                self._read_header(entry.get("msgstr", ""))
                continue
            key = msgid
            if "msgctxt" in entry:
                key = entry["msgctxt"] + _CONTEXT_SEPARATOR + msgid
            if "msgid_plural" in entry:
                forms = entry["msgstr_plural"]
                if forms and all(forms.values()):
                    self._catalog[key] = forms
            elif entry.get("msgstr"):
                self._catalog[key] = entry["msgstr"]

    def _read_header(self, header: str):
        for line in header.splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == "plural-forms":
                match = re.search(r"plural\s*=\s*([^;]+)", value)
                if match:
                    self._plural = _gettext.c2py(match.group(1).strip())

    def _lookup(self, key: str):
        return self._catalog.get(key)

    def gettext(self, message):
        translation = self._lookup(message)
        return translation if isinstance(translation, str) else message

    def ngettext(self, singular, plural, n):
        forms = self._lookup(singular)
        if isinstance(forms, dict):
            return forms.get(self._plural(n), singular if n == 1 else plural)
        return singular if n == 1 else plural

    def pgettext(self, context, message):
        translation = self._lookup(context + _CONTEXT_SEPARATOR + message)
        return translation if isinstance(translation, str) else message

    def npgettext(self, context, singular, plural, n):
        forms = self._lookup(context + _CONTEXT_SEPARATOR + singular)
        if isinstance(forms, dict):
            return forms.get(self._plural(n), singular if n == 1 else plural)
        return singular if n == 1 else plural


def _parse_po(text: str):
    """Yield the entries of a PO file as dicts.

    Keys: flags (set), msgctxt, msgid, msgid_plural, msgstr, msgstr_plural ({n: str})."""
    entry = {"flags": set()}
    field: Optional[Tuple[str, Optional[int]]] = None

    def finish():
        nonlocal entry, field
        if "msgid" in entry:
            yield entry
        entry = {"flags": set()}
        field = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            yield from finish()
            continue
        if line.startswith("#"):
            if "msgid" in entry:
                # comments start the next entry
                yield from finish()
            if line.startswith("#,"):
                entry["flags"].update(f.strip() for f in line[2:].split(","))
            continue
        if line.startswith('"'):
            if field is None:
                raise ValueError(f"Unexpected string in PO file: {raw_line!r}")
            _append(entry, field, _unquote(line))
            continue
        keyword, _, rest = line.partition(" ")
        index = None
        match = re.fullmatch(r"msgstr\[(\d+)\]", keyword)
        if match:
            keyword, index = "msgstr_plural", int(match.group(1))
        if keyword not in ("msgctxt", "msgid", "msgid_plural", "msgstr", "msgstr_plural"):
            raise ValueError(f"Unexpected line in PO file: {raw_line!r}")
        if keyword in ("msgctxt", "msgid") and "msgid" in entry:
            yield from finish()
        field = (keyword, index)
        if index is None:
            entry[keyword] = ""
        else:
            entry.setdefault(keyword, {})[index] = ""
        _append(entry, field, _unquote(rest.strip()))
    yield from finish()


def _append(entry, field, value):
    keyword, index = field
    if index is None:
        entry[keyword] += value
    else:
        entry[keyword][index] += value


def _unquote(s: str) -> str:
    if not (len(s) >= 2 and s.startswith('"') and s.endswith('"')):
        raise ValueError(f"Expected a quoted string in PO file: {s!r}")
    # PO strings use C escapes, which match Python's for everything gettext writes
    return ast.literal_eval(s)


@functools.lru_cache(maxsize=None)
def get_translations(locale: str) -> _gettext.NullTranslations:
    if locale == DEFAULT_LOCALE:
        return _gettext.NullTranslations()
    path = LOCALES_DIR / f"{locale}.po"
    if not path.is_file():
        logger.warning(f"No translations found for {locale}, using English")
        return _gettext.NullTranslations()
    try:
        return POTranslations(path)
    except Exception:
        logger.exception(f"Failed to read translations from {path}, using English")
        return _gettext.NullTranslations()


def _translations() -> _gettext.NullTranslations:
    return get_translations(current_locale())


def gettext(message: str, **variables) -> str:
    """Translate a message. Keyword arguments are substituted for %(name)s placeholders."""
    translated = _translations().gettext(message)
    return _format(translated, variables)


def ngettext(singular: str, plural: str, n: int, **variables) -> str:
    """Translate a message with a singular and plural form, chosen for n.

    n is available to the message as %(num)s, other keyword arguments by name."""
    translated = _translations().ngettext(singular, plural, n)
    return _format(translated, {"num": n, **variables})


def pgettext(context: str, message: str, **variables) -> str:
    """Translate a message in a context, to tell apart identical English texts."""
    translated = _translations().pgettext(context, message)
    return _format(translated, variables)


def npgettext(context: str, singular: str, plural: str, n: int, **variables) -> str:
    translated = _translations().npgettext(context, singular, plural, n)
    return _format(translated, {"num": n, **variables})


_ = gettext


def _format(message: str, variables: dict) -> str:
    # like Jinja's newstyle gettext: only messages with variables are formatted, so a
    # message without them can contain a plain "%"
    if not variables:
        return message
    return message % variables


def install_jinja(env):
    """Enable translation in a Jinja environment: ``_()``, ``gettext()``,
    ``ngettext()``, ``pgettext()``, ``npgettext()`` and ``{% trans %}`` blocks.

    Uses Jinja's "new style" gettext: placeholders are %(name)s, filled from keyword
    arguments, and with autoescaping the arguments are escaped (pass HTML through
    the ``safe`` filter) while the translated text itself is trusted."""
    env.add_extension("jinja2.ext.i18n")
    env.install_gettext_callables(
        lambda message: _translations().gettext(message),
        lambda singular, plural, n: _translations().ngettext(singular, plural, n),
        newstyle=True,
        pgettext=lambda context, message: _translations().pgettext(context, message),
        npgettext=lambda context, singular, plural, n: _translations().npgettext(context, singular, plural, n),
    )
    env.globals["current_locale"] = current_locale
    env.globals["html_lang"] = lambda: html_lang(current_locale())
