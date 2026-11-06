"""Description extras (summary, People mentioned, podcast), license, comment
count, facts and themes."""

import datetime as dt

import yt2html
from conftest import (comments_panel, license_contents, people_item, podcast_item,
                      structured_description, summary_item)

DAY = dt.date(2026, 11, 5)


def extras(*items):
    return yt2html.parse_description_extras({"engagementPanels": [structured_description(
        [{"expandableVideoDescriptionBodyRenderer": {}}, *items,
         {"videoDescriptionTranscriptSectionRenderer": {}}])]})


def test_summary():
    result = extras(summary_item("First paragraph.", "Second one."))
    assert result == [{"type": "video_summary", "title": "AI-generated video summary",
                       "paragraphs": ["First paragraph.", "Second one."]}]


def test_people_mentioned():
    people = extras(people_item([("Person One", ""), ("Person Two", "Example description")]))[0]
    assert people["type"] == "people" and people["subtitle"] == "2 people"
    assert people["people"][1] == {"name": "Person Two", "description": "Example description",
                                   "image": "https://img.test/p1"}
    # only the web link is kept, "send feedback" stays text
    assert people["info"]["links"] == [{"start": 60, "length": 10,
                                        "url": "https://support.google.com/youtube/answer/12825599"}]
    html = yt2html.render_attributed(people["info"])
    assert '>Learn more</a> or send feedback' in html


def test_podcast():
    podcast = extras(podcast_item())[0]
    assert podcast["title"] == "Explore the podcast"
    assert podcast["items"][0] == {"title": "Test Podcast",
                                   "url": "https://www.youtube.com/playlist?list=PLtest123",
                                   "byline": "Test Channel", "count": "123 episodes",
                                   "image": "https://img.test/c240"}


def test_extras_keep_youtube_order():
    result = extras(summary_item("S"), people_item([("A", "")]), podcast_item())
    assert [e["type"] for e in result] == ["video_summary", "people", "playlists"]


def test_page_order_of_sections(info):
    info[yt2html.EXTRAS_KEY] = extras(summary_item("S"), people_item([("A", "")]), podcast_item())
    info[yt2html.LICENSE_KEY] = {"title": "License", "runs": [{"text": "CC BY", "url": "https://x.test"}]}
    html = yt2html.render_page(info, None, {}, DAY)
    order = ["AI-generated video summary", "<h2>Chapters", "<h2>Walkthroughs", "People mentioned",
             "Explore the podcast", "<h2>Transcript", "<h2>License", "<h2>Comments"]
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_people_photo_placeholder_and_full_description(info):
    info[yt2html.EXTRAS_KEY] = extras(people_item([("Person Two", "A rather long description")]))
    html = yt2html.render_page(info, None, {}, DAY, extra_images={})
    assert 'class="photo img-placeholder"' in html
    assert '<div class="about">A rather long description</div>' in html


def test_license():
    data = {"contents": license_contents("Creative Commons Attribution license (reuse allowed)",
                                         "https://www.youtube.com/t/creative_commons")}
    assert yt2html.parse_license(data) == {"title": "License", "runs": [
        {"text": "Creative Commons Attribution license (reuse allowed)",
         "url": "https://www.youtube.com/t/creative_commons"}]}
    assert yt2html.parse_license({}) is None


def test_license_falls_back_to_yt_dlp_text():
    assert yt2html.license_of({"license": "CC"})["runs"] == [{"text": "CC", "url": None}]
    page_read = {yt2html.LICENSE_KEY: None, yt2html.EXTRAS_KEY: [], "license": "CC"}
    assert yt2html.license_of(page_read) is None


def test_redirect_links_are_unwrapped():
    url = "https://www.youtube.com/redirect?event=x&q=https%3A%2F%2Fcreativecommons.org%2F"
    assert yt2html.unwrap_redirect(url) == "https://creativecommons.org/"


def test_comment_count_from_page():
    assert yt2html.parse_comment_count({"engagementPanels": [comments_panel("116")]}) == "116"
    assert yt2html.youtube_comment_count({yt2html.YT_COUNT_KEY: "1,234"}) == ("1 234", 1234)
    assert yt2html.youtube_comment_count({yt2html.YT_COUNT_KEY: "1.2K"}) == ("1.2K", 1200)


def test_facts_and_snapshot(info):
    html = yt2html.render_page(info, None, {}, DAY)
    assert '<div class="fact extra"><div class="value">05.11.2026</div><div class="label">Snapshot' in html
    assert '<div class="value">9</div><div class="label">Comments' in html
    assert "Snapshot" not in yt2html.render_page(info, None, {}, DAY, show_snapshot=False)


def test_themes(info):
    auto = yt2html.render_page(info, None, {}, DAY)
    assert '<meta name="color-scheme" content="dark light">' in auto
    assert "@media (prefers-color-scheme: light)" in auto
    light = yt2html.render_page(info, None, {}, DAY, theme="light")
    assert "@media (prefers-color-scheme: light)" not in light and "--bg:#ffffff" in light
    dark = yt2html.render_page(info, None, {}, DAY, theme="dark")
    assert "--bg:#ffffff" not in dark


def test_no_fixed_colours_outside_the_variables():
    # New styles must use CSS variables, or they break one of the themes.
    allowed = {"#0f0f0f", "#f1f1f1", "#aaaaaa", "#272727", "#3f3f3f", "#3ea6ff", "#3a3a3a",
               "#000", "#ff4e45", "#fff"}
    import re
    assert set(re.findall(r"#[0-9a-fA-F]{3,6}\b", yt2html.CSS)) <= allowed
