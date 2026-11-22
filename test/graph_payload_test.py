"""The data the timeline page sends for its graphs: hover templates instead of per-point texts,
and an enlarged view that the browser builds from the grid figure."""

import contextlib
import json
import shutil
import subprocess

import pytest

from stellarisdashboard import config, datamodel, i18n
from stellarisdashboard.dashboard_app import graph_ledger, visualization_data

PlotStyle = visualization_data.PlotStyle


class FakePlotData:
    def __init__(self, series):
        self.series = series

    def get_data_for_plot(self, plot_spec):
        return iter(self.series)


def _spec(style, plot_id="test-plot", **kwargs):
    return visualization_data.PlotSpecification(
        plot_id=plot_id,
        title="Test plot",
        data_container_factory=None,
        style=style,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def fixed_colors(monkeypatch):
    monkeypatch.setattr(
        graph_ledger, "get_country_color", lambda game, key, alpha=1.0: f"rgba(1,2,3,{alpha})"
    )
    monkeypatch.setattr(config.CONFIG, "normalize_stacked_plots", False)


def _render(template, customdata, y):
    """What the browser shows for one point of a trace with a hovertemplate (the parts used here)."""
    assert template.endswith("<extra></extra>")
    text = template[: -len("<extra></extra>")]
    if isinstance(customdata, list):
        text = text.replace("%{customdata[0]}", customdata[0])
        text = text.replace("%{customdata[1]:.2f}", f"{customdata[1]:.2f}")
    else:
        text = text.replace("%{customdata}", customdata)
    return text.replace("%{y:.2f}", f"{y:.2f}")


X = [0.0, 10.0, 20.25]


def test_line_traces_have_a_hovertemplate_and_no_point_texts():
    data = FakePlotData([(1, "empire_name", X, [1.5, float("nan"), 2.255])])
    (trace,) = graph_ledger.get_raw_plot_data_dicts("g", data, _spec(PlotStyle.line))

    assert "text" not in trace
    assert trace["hovertemplate"] == "%{customdata}: %{y:.2f} - Empire name<extra></extra>"
    # the date of the label belongs to the unshifted x value; points without a label get none
    assert trace["customdata"] == [
        datamodel.days_to_date(0),
        None,
        datamodel.days_to_date(360 * 20.25),
    ]
    assert trace["x"] == [2200.0, 2210.0, 2220.25]


def test_template_renders_the_labels_of_the_point_texts():
    y = [1.5, 2.255, -3.0, 1234.5678]
    labels = graph_ledger.get_plot_value_labels(X + [30.0], y, "empire_name")
    fields = graph_ledger.get_hover_fields(X + [30.0], y, "empire_name")

    assert "text" not in fields
    rendered = [
        _render(fields["hovertemplate"], d, v) for d, v in zip(fields["customdata"], y)
    ]
    assert rendered == labels


def test_template_uses_the_translated_message(tmp_path, monkeypatch):
    po = tmp_path / "xx.po"
    po.write_text(
        'msgid ""\nmsgstr ""\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
        'msgid "%(date)s: %(value)s - %(label)s"\nmsgstr "%(label)s => %(value)s (%(date)s)"\n',
        encoding="utf-8",
    )
    translations = i18n.POTranslations(po)
    monkeypatch.setattr(i18n, "_translations", lambda: translations)

    fields = graph_ledger.get_hover_fields(X, [1.0, 2.0, 3.0], "Energy")

    assert fields["hovertemplate"] == "Energy => %{y:.2f} (%{customdata})<extra></extra>"
    assert graph_ledger.get_plot_value_labels([0.0], [1.0], "Energy") == [
        "Energy => 1.00 (2200.01.01)"
    ]


@pytest.mark.parametrize(
    "label, escaped",
    [
        ("a < b > c", "A &lt; b &gt; c"),
        ("<b>bold</b>", "&lt;b&gt;bold&lt;/b&gt;"),
        ("<extra>x</extra>", "&lt;extra&gt;x&lt;/extra&gt;"),
        ("%{y} 100%", "&#37;{y} 100&#37;"),
        ("Salt & pepper", "Salt &amp; pepper"),
        ("&lt;", "&amp;lt;"),
    ],
)
def test_labels_are_escaped_for_the_template(label, escaped):
    fields = graph_ledger.get_hover_fields(X, [1.0, 2.0, 3.0], label)

    assert fields["hovertemplate"] == f"%{{customdata}}: %{{y:.2f}} - {escaped}<extra></extra>"
    # the only template syntax left is the one added by the code
    assert fields["hovertemplate"].count("%{") == 2
    assert fields["hovertemplate"].count("<") == 2


@pytest.mark.parametrize(
    "y",
    [
        [1.0, 0, 3.0],  # no hover label for a value of 0, which a template can't leave out
        [1.0, 0.0, 3.0],
        [1.0, 2.125, 3.0],  # the browser rounds ties differently than format()
        [1.0, -0.001, 3.0],  # "-0.00" in the browser would be "0.00"
    ],
)
def test_points_a_template_cannot_reproduce_keep_their_texts(y):
    fields = graph_ledger.get_hover_fields(X, y, "empire_name")

    assert "hovertemplate" not in fields and "customdata" not in fields
    assert fields["hoverinfo"] == "text"
    assert fields["text"] == graph_ledger.get_plot_value_labels(X, y, "empire_name")
    assert len(fields["text"]) == 3


def test_missing_values_need_no_texts():
    fields = graph_ledger.get_hover_fields(X, [None, float("nan"), 2.0], "empire_name")

    assert "text" not in fields
    assert fields["customdata"][:2] == [None, None]


def test_stacked_traces():
    data = FakePlotData(
        [
            (1, "energy", X, [1.0, 2.0, 3.0]),
            (2, "minerals", X, [0.0, 2.0, 3.0]),
        ]
    )
    plain, with_zero = graph_ledger.get_raw_plot_data_dicts(
        "g", data, _spec(PlotStyle.stacked)
    )

    assert plain["stackgroup"] == "pos" and plain["groupnorm"] == ""
    assert plain["hovertemplate"] == "%{customdata}: %{y:.2f} - Energy<extra></extra>"
    assert "text" not in plain
    assert with_zero["text"][0] == "" and with_zero["text"][1].endswith("2.00 - Minerals")
    assert "hovertemplate" not in with_zero


def test_normalized_stacked_traces_show_the_raw_values(monkeypatch):
    monkeypatch.setattr(config.CONFIG, "normalize_stacked_plots", True)
    data = FakePlotData([(1, "energy", X, [1.0, 2.0, 3.004])])
    (trace,) = graph_ledger.get_raw_plot_data_dicts("g", data, _spec(PlotStyle.stacked))

    assert trace["groupnorm"] == "percent"
    # "y" would be the share of the stack
    assert trace["hovertemplate"] == (
        "%{customdata[0]}: %{customdata[1]:.2f} - Energy<extra></extra>"
    )
    assert trace["customdata"] == [
        [datamodel.days_to_date(360 * x), v] for x, v in zip(X, [1.0, 2.0, 3.0])
    ]


def test_budget_traces_and_net_result():
    data = FakePlotData(
        [
            (1, "income", X, [1.0, 2.0, 3.0]),
            (2, "upkeep", X, [-1.0, -2.0, -1.5]),
            (3, "mixed", X, [-1.0, 2.0, 1.5]),
        ]
    )
    traces = graph_ledger.get_raw_plot_data_dicts("g", data, _spec(PlotStyle.budget))
    by_name = {}
    for trace in traces:
        by_name.setdefault(trace["name"], []).append(trace)

    assert set(by_name) == {"Income", "Upkeep", "Mixed", "Net result"}
    assert "text" not in by_name["Income"][0]
    assert "hovertemplate" in by_name["Upkeep"][0]
    # mixed signs: two stack groups that each have zeros
    assert [t["stackgroup"] for t in by_name["Mixed"]] == ["pos", "neg"]
    assert all("hovertemplate" not in t and "text" in t for t in by_name["Mixed"])
    net = by_name["Net result"][0]
    assert net["hovertemplate"] == "%{customdata}: %{y:.2f} - Net result<extra></extra>"
    assert net["customdata"][0] == datamodel.days_to_date(0)
    assert net["y"] == pytest.approx([-1.0, 2.0, 3.0])


def _patch_update_content(monkeypatch, series, style=PlotStyle.line):
    game_id = "payload_test_game"
    specs = [_spec(style, plot_id="plot-a"), _spec(style, plot_id="plot-b")]
    monkeypatch.setattr(
        graph_ledger, "_get_game_ids_matching_url", lambda url: (game_id, [game_id])
    )
    monkeypatch.setattr(
        datamodel, "get_available_games_dict", lambda: {game_id: {"game_id": game_id}}
    )
    monkeypatch.setattr(
        visualization_data,
        "plot_data",
        lambda game, perspective: contextlib.nullcontext(FakePlotData(series)),
    )
    monkeypatch.setattr(
        visualization_data,
        "get_plot_specifications_for_tab_layout",
        lambda: {"Test": specs},
    )
    monkeypatch.setattr(graph_ledger, "_get_tab_from_url", lambda url: "Test")
    monkeypatch.setattr(graph_ledger, "_get_player_legend_label", lambda game: "Player")
    return game_id


def _components(node, found=None):
    found = [] if found is None else found
    if isinstance(node, (list, tuple)):
        for child in node:
            _components(child, found)
    elif hasattr(node, "to_plotly_json"):
        found.append(node)
        _components(getattr(node, "children", None), found)
    return found


def test_tab_content_sends_each_figure_once(monkeypatch):
    series = [(1, "player", X, [1.0, 2.0, 3.0]), (2, "other", X, [2.0, 3.0, 4.0])]
    _patch_update_content(monkeypatch, series)

    children = graph_ledger.update_content("?game_name=x", [], None)

    components = _components(children)
    assert not [c for c in components if type(c).__name__ == "Store"]
    graphs = [c for c in components if type(c).__name__ == "Graph"]
    assert [g.id for g in graphs] == [
        {"type": "grid-graph", "index": "plot-a"},
        {"type": "grid-graph", "index": "plot-b"},
    ]
    payload = json.dumps(children, default=lambda o: o.to_plotly_json())
    assert "full-figures-store" not in payload
    for graph in graphs:
        assert all("text" not in trace for trace in graph.figure.to_plotly_json()["data"])
    assert payload.count("hovertemplate") == 4
    # plotly's default template (7 KB) isn't sent with every figure
    assert len(payload) < 20000


def test_modal_does_not_depend_on_a_store():
    assert "full-figures-store" not in json.dumps(
        graph_ledger.timeline_app.layout(), default=lambda o: o.to_plotly_json()
    )
    callbacks = graph_ledger.timeline_app._callback_list
    modal = [c for c in callbacks if "modal-graph.figure" in c["output"]]
    assert len(modal) == 1
    assert "clientside_function" in modal[0]
    assert all(s["id"] != "full-figures-store" for s in modal[0]["state"])
    # no server side callback is left that could receive figures
    assert not [
        c
        for c in callbacks
        if "clientside_function" not in c and "grid-graph" in str(c["state"])
    ]


def test_enlarged_layout_changes_complete_the_compact_layout():
    spec = _spec(PlotStyle.line, y_axis_label="Credits", y_axis_reversed=True)
    full = graph_ledger.get_figure_layout(spec).to_plotly_json()
    full["title"] = dict(
        text="Test plot",
        font=dict(size=18, color=graph_ledger.TEXT_COLOR, family=graph_ledger.FONT_FAMILY),
        x=0.5,
        xanchor="center",
    )
    compact = graph_ledger.get_compact_figure_layout(spec).to_plotly_json()

    changes = graph_ledger.get_enlarged_layout_changes(spec)

    def merge(base, changed):
        for key, value in changed.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value
        return base

    assert merge(compact, changes) == full
    assert changes["height"] == config.CONFIG.plot_height
    assert changes["xaxis"]["title"]["text"] == "Year"
    assert "yaxis" in changes and "autorange" not in changes["yaxis"]
    assert changes["title"]["text"] == "Test plot"


def test_emphasis_keeps_what_the_enlarged_view_restores():
    figure_data = [
        dict(name="Other", line={"color": "rgba(4,5,6,1.0)"}, showlegend=True),
        dict(
            name="Player",
            line={"color": "rgba(1,2,3,1.0)", "width": 0.5},
            fillcolor="rgba(1,2,3,0.5)",
        ),
    ]
    emphasized = graph_ledger._emphasize_player_in_compact_data(figure_data, "Player")

    assert [t["name"] for t in emphasized] == ["Other", "Player"]
    other = next(t for t in emphasized if t["name"] == "Other")
    player = next(t for t in emphasized if t["name"] == "Player")
    assert other["meta"] == {
        "index": 0,
        "original": {"line": {"color": "rgba(4,5,6,1.0)"}, "showlegend": True},
    }
    assert player["meta"] == {
        "index": 1,
        "original": {
            "line": {"color": "rgba(1,2,3,1.0)", "width": 0.5},
            "fillcolor": "rgba(1,2,3,0.5)",
        },
    }
    # the input is not changed
    assert "meta" not in figure_data[0] and figure_data[0]["line"] == {"color": "rgba(4,5,6,1.0)"}


NODE_DRIVER = """
const source = %s;
const figures = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const fn = eval('(' + source + ')');
function run(triggered, value, cards) {
  global.window = {dash_clientside: {
    no_update: 'NO_UPDATE',
    callback_context: {triggered_id: triggered, triggered: [{prop_id: 'x', value: value}],
                       states_list: [cards]},
  }};
  return fn([], 0, 0, []);
}
const cards = figures.map((figure, i) => ({id: {type: 'grid-graph', index: 'plot-' + i}, property: 'figure', value: figure}));
const out = {
  open: run({type: 'expand-plot', index: 'plot-0'}, 1, cards),
  spurious: run({type: 'expand-plot', index: 'plot-0'}, 0, cards),
  close: run('modal-close', 1, cards),
  backdrop: run('modal-backdrop', 1, cards),
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the browser code")
def test_modal_function_rebuilds_the_enlarged_figure(monkeypatch):
    series = [
        (1, "other", X, [2.0, 3.0, 4.0]),
        (2, "player", X, [1.0, 2.0, 3.0]),
        (3, "third", X, [5.0, 6.0, 7.0]),
    ]
    spec = _spec(PlotStyle.line, y_axis_label="Credits", y_axis_reversed=True)
    figure_data = graph_ledger.get_raw_plot_data_dicts("g", FakePlotData(series), spec)
    compact_layout = graph_ledger.get_compact_figure_layout(spec)
    compact_layout["meta"] = {"enlarged": graph_ledger.get_enlarged_layout_changes(spec)}
    card = graph_ledger.go.Figure(
        data=graph_ledger._emphasize_player_in_compact_data(figure_data, "Player"),
        layout=compact_layout,
    ).to_plotly_json()
    # the card was zoomed
    card["layout"]["xaxis"]["range"] = [2210, 2220]
    card["layout"]["xaxis"]["autorange"] = False
    assert [t["name"] for t in card["data"]] == ["Other", "Third", "Player"]

    script = NODE_DRIVER % json.dumps(graph_ledger.MODAL_FIGURE_JS)
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps([card]),
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(result.stdout)

    class_name, figure, title = out["open"]
    assert class_name == "tl-modal" and title == "Test plot"
    full_layout = graph_ledger.get_figure_layout(spec).to_plotly_json()
    assert figure["layout"]["height"] == full_layout["height"]
    assert figure["layout"]["showlegend"] is True
    assert figure["layout"]["yaxis"]["autorange"] == "reversed"
    assert "range" not in figure["layout"]["xaxis"]
    assert figure["layout"]["xaxis"]["title"]["text"] == "Year"
    assert "meta" not in figure["layout"]
    # original order, colors, widths and legend entries
    original = {t["name"]: t for t in figure_data}
    assert [t["name"] for t in figure["data"]] == ["Other", "Player", "Third"]
    for trace in figure["data"]:
        assert "meta" not in trace
        expected = original[trace["name"]]
        for key in ("line", "x", "y", "customdata", "hovertemplate"):
            assert trace[key] == expected[key]
        assert ("showlegend" in trace) == ("showlegend" in expected)

    assert out["spurious"] == ["NO_UPDATE"] * 3
    assert out["close"] == ["tl-modal tl-modal--hidden", "NO_UPDATE", "NO_UPDATE"]
    assert out["backdrop"] == out["close"]
