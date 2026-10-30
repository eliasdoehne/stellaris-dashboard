"""Collect the dashboard's translatable messages and update the translation files.

Run from the repository root (needs the dev dependencies, for Babel):

    uv run python -m stellarisdashboard.i18n.extract

This writes all messages to locales/messages.pot and merges them into each
locales/<locale>.po: new messages are added untranslated, messages that changed
slightly are added as fuzzy (ignored until a translator confirms them), and
removed messages are dropped.
"""
import importlib
import io
import pathlib
import re
import sys

from babel.messages.catalog import Catalog
from babel.messages.extract import extract, extract_from_dir
from babel.messages.pofile import read_po, write_po

from stellarisdashboard import i18n

PACKAGE_DIR = pathlib.Path(__file__).parent.parent
ROOT_DIR = PACKAGE_DIR.parent
POT_PATH = i18n.LOCALES_DIR / "messages.pot"

KEYWORDS = {
    "_": None,
    "gettext": None,
    "N_": None,
    "ngettext": (1, 2),
    "pgettext": ((1, "c"), 2),
    "npgettext": ((1, "c"), 2, 3),
}
COMMENT_TAGS = ["Translators:"]
METHOD_MAP = [
    ("**/i18n/**.py", "ignore"),
    ("**.py", "python"),
    ("**/templates/**.html", "jinja2.ext:babel_extract"),
]
JINJA_OPTIONS = {"extensions": "jinja2.ext.i18n", "trimmed": "true"}

# Modules that keep Jinja templates in Python strings. Each provides
# jinja_sources(), returning (name, source) pairs; name says where the template is defined.
JINJA_STRING_MODULES = [
    "stellarisdashboard.dashboard_app.event_registry",
]


def build_template_catalog() -> Catalog:
    catalog = Catalog(
        project="Stellaris Dashboard",
        msgid_bugs_address="https://github.com/eliasdoehne/stellaris-dashboard/issues",
        copyright_holder="Elias Doehne",
        fuzzy=False,
    )
    options_map = {"**/templates/**.html": JINJA_OPTIONS}
    for filename, lineno, message, comments, context in extract_from_dir(
        PACKAGE_DIR,
        METHOD_MAP,
        options_map,
        keywords=KEYWORDS,
        comment_tags=COMMENT_TAGS,
        strip_comment_tags=True,
    ):
        location = (f"stellarisdashboard/{pathlib.Path(filename).as_posix()}", lineno)
        catalog.add(message, None, [location], auto_comments=comments, context=context)

    for module_name in JINJA_STRING_MODULES:
        module = importlib.import_module(module_name)
        module_path = pathlib.Path(module.__file__).relative_to(ROOT_DIR).as_posix()
        for name, source in module.jinja_sources():
            for lineno, message, comments, context in _extract_jinja_string(source):
                catalog.add(
                    message,
                    None,
                    [(f"{module_path}:{name}", lineno)],
                    auto_comments=comments,
                    context=context,
                )
    return catalog


def _extract_jinja_string(source: str):
    for lineno, message, comments, context in extract(
        "jinja2.ext:babel_extract",
        io.BytesIO(source.encode("utf-8")),
        keywords=KEYWORDS,
        comment_tags=COMMENT_TAGS,
        options=JINJA_OPTIONS,
        strip_comment_tags=True,
    ):
        yield lineno, message, comments, context


_CREATION_DATE_RE = re.compile(rb'^"POT-Creation-Date: .*\n', re.MULTILINE)


def _write_if_changed(path: pathlib.Path, catalog: Catalog, **kwargs):
    """Write the catalog, unless the file differs only in its creation date (which
    changes on every run and would make every file show up as modified)."""
    buffer = io.BytesIO()
    write_po(buffer, catalog, width=100, sort_by_file=True, **kwargs)
    content = buffer.getvalue()
    if path.exists():
        old = path.read_bytes()
        if _CREATION_DATE_RE.sub(b"", old) == _CREATION_DATE_RE.sub(b"", content):
            return
    path.write_bytes(content)


def message_keys(catalog: Catalog):
    return {(m.id, m.context) for m in catalog if m.id}


def update_locale_catalogs(template: Catalog):
    for locale in i18n.LANGUAGE_NAMES:
        if locale == i18n.DEFAULT_LOCALE:
            continue
        po_path = i18n.LOCALES_DIR / f"{locale}.po"
        if po_path.exists():
            with open(po_path, "rb") as f:
                catalog = read_po(f, locale=locale)
        else:
            catalog = Catalog(locale=locale, project=template.project, fuzzy=False)
        catalog.update(template, update_header_comment=False)
        _write_if_changed(po_path, catalog, ignore_obsolete=True)
        translated = sum(1 for m in catalog if m.id and m.string and (not m.pluralizable or all(m.string)) and not m.fuzzy)
        print(f"{po_path.name}: {translated}/{len(message_keys(catalog))} translated")


def main():
    template = build_template_catalog()
    _write_if_changed(POT_PATH, template)
    print(f"Wrote {len(message_keys(template))} messages to {POT_PATH}")
    if "--pot-only" not in sys.argv:
        update_locale_catalogs(template)


if __name__ == "__main__":
    main()
