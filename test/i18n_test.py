import re

import jinja2
import pytest

from stellarisdashboard import i18n

PO_FILE = r'''# a translator comment
msgid ""
msgstr ""
"Language: de\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

#: some/file.py:12
msgid "Settings"
msgstr "Einstellungen"

msgid "Escaped \"quotes\"\n"
"and a second line"
msgstr "Maskierte \"Anführungszeichen\"\n"
"und eine zweite Zeile"

#, fuzzy
msgid "Graphs"
msgstr "Grafen"

msgid "Untranslated"
msgstr ""

msgctxt "ledger tab"
msgid "Events"
msgstr "Ereignisse"

msgid "%(num)s envoy"
msgid_plural "%(num)s envoys"
msgstr[0] "%(num)s Gesandter"
msgstr[1] "%(num)s Gesandte"

#, python-format
msgid "The %(country)s declared war."
msgstr "Das %(country)s erklärte den Krieg."
'''

RUSSIAN_PLURALS = r'''msgid ""
msgstr ""
"Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);\n"

msgid "%(num)s envoy"
msgid_plural "%(num)s envoys"
msgstr[0] "%(num)s посланник"
msgstr[1] "%(num)s посланника"
msgstr[2] "%(num)s посланников"
'''


@pytest.fixture
def german(tmp_path, monkeypatch):
    path = tmp_path / "de.po"
    path.write_text(PO_FILE, encoding="utf-8")
    translations = i18n.POTranslations(path)
    monkeypatch.setattr(i18n, "_translations", lambda: translations)
    return translations


def test_po_translations(german):
    assert german.gettext("Settings") == "Einstellungen"
    assert german.gettext('Escaped "quotes"\nand a second line') == 'Maskierte "Anführungszeichen"\nund eine zweite Zeile'
    assert german.gettext("Untranslated") == "Untranslated"
    assert german.gettext("Unknown message") == "Unknown message"


def test_fuzzy_entries_are_ignored(german):
    assert german.gettext("Graphs") == "Graphs"


def test_context(german):
    assert german.pgettext("ledger tab", "Events") == "Ereignisse"
    assert german.gettext("Events") == "Events"


def test_plurals(german):
    assert i18n.ngettext("%(num)s envoy", "%(num)s envoys", 1) == "1 Gesandter"
    assert i18n.ngettext("%(num)s envoy", "%(num)s envoys", 3) == "3 Gesandte"
    assert i18n.ngettext("%(num)s other", "%(num)s others", 3) == "3 others"


def test_plural_forms_header(tmp_path):
    path = tmp_path / "ru.po"
    path.write_text(RUSSIAN_PLURALS, encoding="utf-8")
    russian = i18n.POTranslations(path)
    assert russian.ngettext("%(num)s envoy", "%(num)s envoys", 1) == "%(num)s посланник"
    assert russian.ngettext("%(num)s envoy", "%(num)s envoys", 3) == "%(num)s посланника"
    assert russian.ngettext("%(num)s envoy", "%(num)s envoys", 11) == "%(num)s посланников"


def test_gettext_formats_variables(german):
    assert i18n.gettext("The %(country)s declared war.", country="Empire") == "Das Empire erklärte den Krieg."
    # without variables, a message is not formatted, so it can contain "%"
    assert i18n.gettext("100%") == "100%"


def test_jinja_newstyle_escapes_arguments_but_not_safe_ones(german):
    env = jinja2.Environment(autoescape=True)
    i18n.install_jinja(env)
    template = env.from_string(
        '{{ _("The %(country)s declared war.", country=link | safe) }} {{ _("Settings") }} '
        '{{ _("The %(country)s declared war.", country=name) }}'
        "{% trans %}Settings{% endtrans %}"
    )
    rendered = template.render(link='<a href="x">A</a>', name="<b>")
    assert rendered == (
        'Das <a href="x">A</a> erklärte den Krieg. Einstellungen '
        "Das &lt;b&gt; erklärte den Krieg.Einstellungen"
    )


@pytest.mark.parametrize(
    "dashboard_language,stellaris_language,expected",
    [
        ("auto", "l_german", "de"),
        ("auto", "l_braz_por", "pt_BR"),
        ("auto", "l_unknown", "en"),
        (None, "l_french", "fr"),
        ("ru", "l_german", "ru"),
        ("xx", "l_german", "en"),
    ],
)
def test_resolve_locale(dashboard_language, stellaris_language, expected):
    assert i18n.resolve_locale(dashboard_language, stellaris_language) == expected


def test_every_language_has_a_name():
    assert set(i18n.STELLARIS_LANGUAGE_TO_LOCALE.values()) == set(i18n.LANGUAGE_NAMES)


def _placeholders(message):
    return sorted(re.findall(r"%\((\w+)\)s", message))


@pytest.mark.parametrize("locale", sorted(set(i18n.LANGUAGE_NAMES) - {i18n.DEFAULT_LOCALE}))
def test_shipped_translations(locale):
    """Every shipped PO file parses, and its translations use the same placeholders
    as the English message (a missing or misspelled one would fail at runtime)."""
    path = i18n.LOCALES_DIR / f"{locale}.po"
    if not path.exists():
        pytest.skip(f"no translations for {locale}")
    for entry in i18n._parse_po(path.read_text(encoding="utf-8")):
        msgid = entry["msgid"]
        if not msgid or "fuzzy" in entry["flags"]:
            continue
        if "msgid_plural" in entry:
            for n, form in entry["msgstr_plural"].items():
                if not form:
                    continue
                # a language's singular form may leave out %(num)s ("one envoy")
                expected = set(_placeholders(msgid)) | set(_placeholders(entry["msgid_plural"]))
                assert set(_placeholders(form)) - {"num"} == expected - {"num"}, (locale, msgid, form)
        elif entry["msgstr"]:
            assert _placeholders(entry["msgstr"]) == _placeholders(msgid), (locale, msgid, entry["msgstr"])


def test_message_template_is_up_to_date():
    """locales/messages.pot lists exactly the messages marked in the code. If this
    fails, run: uv run python -m stellarisdashboard.i18n.extract"""
    extract = pytest.importorskip("stellarisdashboard.i18n.extract")
    with open(extract.POT_PATH, "rb") as f:
        from babel.messages.pofile import read_po

        pot = read_po(f)
    assert extract.message_keys(extract.build_template_catalog()) == extract.message_keys(pot)
