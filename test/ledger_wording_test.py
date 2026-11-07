from stellarisdashboard.dashboard_app import event_registry
from stellarisdashboard.dashboard_app.event_registry import render_sentence, with_article, with_articles


class _Entity:
    def __init__(self, name, ingame_name=None):
        self.rendered_name = name
        self.ingame_name = ingame_name


def _links(*entities):
    return {e: f"<a>{e.rendered_name}</a>" for e in entities}


def test_names_with_their_own_article_get_no_extra_the():
    chosen, empire = _Entity("The Chosen"), _Entity("Tzynn Empire")
    links = _links(chosen, empire)
    assert with_article(chosen, links) == "<a>The Chosen</a>"
    assert with_article(empire, links) == "the <a>Tzynn Empire</a>"
    assert with_article(empire, links, capitalize=True) == "The <a>Tzynn Empire</a>"
    assert with_article("The Core") == "The Core"
    assert with_article("Zithoria") == "the Zithoria"
    assert with_article("<b>") == "the &lt;b&gt;"


def test_lists_name_each_country_with_its_article():
    a, b, c = _Entity("A"), _Entity("The B"), _Entity("C")
    links = _links(a, b, c)
    assert with_articles([a], links, capitalize=True) == "The <a>A</a>"
    assert with_articles([a, b, c], links) == "the <a>A</a>, <a>The B</a> and the <a>C</a>"


def test_sentences_avoid_doubled_articles():
    chosen, empire, system = _Entity("The Chosen"), _Entity("Tzynn Empire"), _Entity("Rana")
    war = _Entity("The War in Heaven", ingame_name="The War in Heaven")
    links = _links(chosen, empire, system, war)
    event = dict(event_type="conquered_system", country=chosen, target_country=empire, system=system, war=war)
    assert render_sentence(event, links) == (
        "<a>The Chosen</a> conquered the <a>Rana</a> system from the <a>Tzynn Empire</a>"
        " during <a>The War in Heaven</a>."
    )
    event = dict(event_type="war", country=empire, target_country=chosen, war=war, call_type="alliance")
    assert render_sentence(event, links) == (
        "The <a>Tzynn Empire</a> entered <a>The War in Heaven</a>, honoring their alliance with <a>The Chosen</a>."
    )


def test_every_sentence_compiles():
    from stellarisdashboard.dashboard_app import flask_app

    for descriptor in list(event_registry.REGISTRY.values()) + list(event_registry.STORYLINE_REGISTRY.values()):
        if descriptor.sentence is not None:
            flask_app.jinja_env.from_string(descriptor.sentence)
