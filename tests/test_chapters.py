"""Chapters, their origin, Walkthroughs and other timestamp lists."""

import pytest

import yt2html
from conftest import CHAPTER_DESCRIPTION, CHAPTERS, marker_panel, player_bar, watch_page

DESCRIPTION_PANEL = "engagement-panel-macro-markers-description-chapters"
AUTO_PANEL = "engagement-panel-macro-markers-auto-chapters"
WALKTHROUGHS_PANEL = "engagement-panel-macro-markers-problem-walkthroughs"
WALKTHROUGHS = [(21, "What is a test?"), (132, "How does it work?")]


def lists(panels, bar=None, description="", duration=600):
    data = {"engagementPanels": panels}
    if bar:
        data["playerOverlays"] = bar
    return yt2html.parse_marker_lists(data, description, duration)


class TestDescriptionCheck:
    def test_valid_list(self):
        assert yt2html.description_chapter_starts(CHAPTER_DESCRIPTION, 600) == [0, 110, 264]

    @pytest.mark.parametrize("text", [
        "0:02 Intro\n2:35 Two\n4:24 Three",       # does not start at 0:00
        "0:00 Intro\n0:05 Two\n1:00 Three",       # chapter shorter than 10 s
        "0:00 Intro\n1:00 Two",                   # fewer than three
    ])
    def test_invalid_lists(self, text):
        assert yt2html.description_chapter_starts(text, 600) is None

    def test_other_formats(self):
        assert yt2html.description_chapter_starts("00:00:00 a\n00:01:00 b\n00:02:00 c", 600) == [0, 60, 120]
        assert yt2html.description_chapter_starts("[0:00] a\n(1:00) b\n2:00 - c", None) == [0, 60, 120]


class TestChapterOrigin:
    def test_description_chapters(self):
        result = lists([marker_panel(DESCRIPTION_PANEL, "Chapters", CHAPTERS)],
                       player_bar("DESCRIPTION_CHAPTERS", CHAPTERS), CHAPTER_DESCRIPTION)
        assert result[0]["title"] == "Chapters"
        assert result[0]["source"] == "description"
        assert [i["start_time"] for i in result[0]["items"]] == [0, 110, 264]

    def test_auto_chapters_with_info_row_skipped(self):
        result = lists([marker_panel(AUTO_PANEL, "Chapters", CHAPTERS, info_row=True)],
                       player_bar("AUTO_CHAPTERS", CHAPTERS), "No timestamps.")
        assert result[0]["source"] == "auto"
        assert len(result[0]["items"]) == 3

    def test_progress_bar_only(self):
        result = lists([], player_bar("AUTO_CHAPTERS", CHAPTERS), "")
        assert result[0]["source"] == "auto"

    @pytest.mark.parametrize("bar_key, panel_id, description", [
        ("DESCRIPTION_CHAPTERS", None, CHAPTER_DESCRIPTION.replace("0:00 Intro", "0:02 Intro")),
        ("AUTO_CHAPTERS", None, CHAPTER_DESCRIPTION),
        ("DESCRIPTION_CHAPTERS", AUTO_PANEL, CHAPTER_DESCRIPTION),
        ("SOMETHING_NEW", None, CHAPTER_DESCRIPTION),
    ])
    def test_disagreement_gives_neutral_heading(self, bar_key, panel_id, description, capsys):
        panels = [marker_panel(panel_id, "Chapters", CHAPTERS)] if panel_id else []
        result = lists(panels, player_bar(bar_key, CHAPTERS), description)
        assert result[0]["source"] == "uncertain"
        assert result[0]["source_problem"]
        assert "Chapter source unclear" in capsys.readouterr().out


def test_walkthroughs_are_not_chapters():
    result = lists([marker_panel(WALKTHROUGHS_PANEL, "Walkthroughs", WALKTHROUGHS)])
    assert len(result) == 1
    assert result[0]["title"] == "Walkthroughs"
    assert result[0]["source"] is None


def test_chapters_first_then_other_lists():
    result = lists([marker_panel(WALKTHROUGHS_PANEL, "Walkthroughs", WALKTHROUGHS),
                    marker_panel(DESCRIPTION_PANEL, "Chapters", CHAPTERS)],
                   player_bar("DESCRIPTION_CHAPTERS", CHAPTERS), CHAPTER_DESCRIPTION)
    assert [r["title"] for r in result] == ["Chapters", "Walkthroughs"]


def test_initial_data_is_found_in_a_page():
    data = {"engagementPanels": [], "x": "};</script>"}
    assert yt2html.initial_data_of(watch_page(data)) == data


def test_fallback_to_yt_dlp_chapters_without_page_data():
    info = {"chapters": [{"start_time": 0.0, "title": "Intro"}, {"start_time": 75.0, "title": "Next"}]}
    result = yt2html.marker_lists_of(info)
    assert result[0]["title"] == "Chapters" and result[0]["source"] is None
    assert result[0]["items"][1]["time_text"] == "1:15"


def test_rendered_origin_note(info):
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    assert '<h2>Chapters <span class="source">from description</span></h2>' in html
    assert "<h2>Walkthroughs</h2>" in html
