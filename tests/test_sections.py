"""Description extras (summary, People mentioned, podcast), license, comment
count, facts, themes and the channel avatar."""

import datetime as dt
import re
import json
from pathlib import Path

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


def test_license_falls_back_when_the_video_page_could_not_be_read():
    info = {"license": "CC", **dict.fromkeys(yt2html.PAGE_KEYS),
            yt2html.WATCH_PAGE_KEY: "failed"}
    assert yt2html.license_of(info)["runs"] == [{"text": "CC", "url": None}]
    info.update({yt2html.WATCH_PAGE_KEY: "read", yt2html.EXTRAS_KEY: []})  # read: no license row
    assert yt2html.license_of(info) is None
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
    hidden = yt2html.render_page(info, None, {}, DAY, show_snapshot=False)
    assert 'class="label">Snapshot' not in hidden
    assert "Snapshot 05.11.2026 · made with yt2html" in hidden   # the date stays at the bottom


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
               "#000", "#ff4e45", "#fff", "#0e8f85", "#3cc8b4"}
    import re
    assert set(re.findall(r"#[0-9a-fA-F]{3,6}\b", yt2html.CSS)) <= allowed


# Local video player ----------------------------------------------------------

def test_player_tile_panel_and_script(info):
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6))
    facts = html.split('<div class="facts">', 1)[1]
    assert facts.startswith('<button class="fact play" type="button"><div class="value">▶ Play'
                            '</div><div class="label">Player</div></button>')
    assert html.count('<aside class="player"') == 1
    assert 'data-extensions="mp4 webm m4v mkv"' in html
    assert [b.split('"')[0] for b in html.split('data-speed="')[1:]] == \
        ["0.5", "0.75", "1", "1.25", "1.5"]
    assert html.count("<script>") == 1 and yt2html.PLAYER_JS in html


def test_player_knows_the_original_video_name(info):
    names = yt2html.video_names(info)
    assert names == [yt2html.output_path(info, Path(".")).stem]
    assert names[0].endswith("[TESTVIDEO01]")
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6))
    assert yt2html.esc(json.dumps(names, ensure_ascii=False)) in html


def test_player_is_hidden_until_a_video_is_found():
    # The tile and the panel show only after the script adds these classes.
    assert ".fact.play{display:none;" in yt2html.CSS
    assert ".has-video .fact.play{display:block}" in yt2html.CSS
    assert ".player{display:none}" in yt2html.CSS
    assert ".player-open .page > .thumbnail{display:none}" in yt2html.CSS


def test_every_timestamp_can_play_the_local_copy(info):
    info["comments"][0]["text"] = "Best part at 1:23"
    info["description"] = "Starts at 0:42 sharp."
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6),
                               transcript_spec=[("timestamps", None), ("bilingual", "pl")])
    import re
    links = re.findall(r'<a class="t" data-t="(\d+)" href="([^"]+)"', html)
    times = {int(t) for t, _ in links}
    assert {42, 83, 21} <= times                           # description, comment, walkthrough
    assert all(href.endswith(f"&amp;t={t}s") for t, href in links)
    # every link to a moment of the video is a player timestamp
    assert html.count("watch?v=TESTVIDEO01&amp;t=") == len(links)


def test_transcript_cues_carry_their_start(info):
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6),
                               transcript_spec=[("timestamps", None), ("plain", None),
                                                ("bilingual", "pl")])
    assert '<div class="cue" data-c="0"><span class="time">' in html
    assert '<div class="bi-row" data-c="0"><span class="time">' in html
    assert '<p><span data-c="0">' in html
    assert yt2html.cue_start({"start": 12.3456}) == "12.35"
    assert yt2html.cue_start({"start": 4.0}) == "4"


def test_player_has_follow_chapters_and_the_lines_under_the_video(info):
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6))
    assert 'data-do="follow"' in html and 'data-do="chapters"' in html
    assert 'data-do="unfollow"' not in html
    assert ('<div class="now-playing" title="Show in the transcript"><div class="chapter"></div>'
            '<div class="line prev2"></div><div class="line prev"></div><div class="line current"></div>'
            '<div class="line translation"></div><div class="line next"></div>'
            '<div class="line next2"></div></div>') in html


def test_player_buttons_repeat_and_no_pop_out(info):
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6))
    row = html[html.index('<div class="buttons">'):html.index('<div class="buttons speed">')]
    assert re.findall(r'data-do="(\w+)"', row) == ["start", "back", "repeat", "toggle", "forward", "end"]
    assert ">↺</button>" in row and "Shift+←" in row
    assert 'data-do="pip"' not in html and "requestPictureInPicture" not in yt2html.PLAYER_JS
    # the browser's own picture-in-picture still switches the page's layout
    assert "enterpictureinpicture" in yt2html.PLAYER_JS


def test_repeat_goes_to_the_previous_sentence_or_5_seconds_back():
    js = yt2html.PLAYER_JS
    assert "var target = i > 0 ? source.times[i - 1] : -1;" in js
    assert "jump(target >= 0 && t - target <= 20 ? target : t - 5);" in js


def test_keys_of_the_player():
    js = yt2html.PLAYER_JS
    for key, action in (("' '", "toggle"), ("'ArrowLeft'", "back"), ("'ArrowRight'", "forward"),
                        ("'Shift+ArrowLeft'", "repeat"), ("'Ctrl+ArrowLeft'", "start"),
                        ("'Ctrl+ArrowRight'", "end")):
        assert f"{key}: '{action}'" in js


def test_follow_cycles_through_the_transcripts_and_then_off():
    js = yt2html.PLAYER_JS
    assert "else if (follow + 1 < lists.length) setFollow(follow + 1);" in js
    assert "else { setFollow(-1); lastFollow = 0; }" in js


def test_css_has_only_printable_characters():
    # A backslash escape in the Python string (e.g. "\2013") once put a control
    # character and a "3" on the page instead of the intended mark.
    for css in (yt2html.CSS, yt2html.LIGHT_VARIABLES):
        assert all(ch.isprintable() or ch in "\n\t" for ch in css)
        assert "\\" not in css


def test_speaker_mark_is_a_grey_bubble():
    assert ".transcript .speaker{color:var(--muted)}" in yt2html.CSS
    rule = yt2html.CSS.split(".transcript .cue .speaker::before", 1)[1].split("}", 1)[0]
    assert 'content:""' in rule and "background:currentColor" in rule and "mask:url(" in rule


def test_subtitles_cycle_and_language_buttons(info):
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 6))
    assert ('data-do="subtitles" aria-pressed="false" title="Show the current sentence under the '
            'video; again: with one line before and after, with two, off">Subtitles'
            '<span class="dots"></span></button><span class="langs"></span></div>') in html
    js = yt2html.PLAYER_JS
    assert "subtitles: function () { setSubtitles((subtitles + 1) % 4); }" in js
    assert "['', '<i></i>', '<i class=\"full\"></i>', '<i class=\"full\"></i><i class=\"full\"></i>'][mode]" in js
    # the dots are drawn in CSS: hollow and filled ones have the same size
    assert ".player .dots i{width:6px;height:6px;border-radius:50%;border:1.5px solid currentColor;" in yt2html.CSS
    assert ".player .dots i.full{background:currentColor}" in yt2html.CSS
    assert "bilinguals.map(function (list, i)" in js            # one language button per bilingual
    assert "box.hidden = true;" in js                           # nothing under the video at first
    assert ".player .now-playing .line{color:var(--muted);font-size:18px;" in yt2html.CSS
    # the translation stands out from the original lines, faintly, only through the variables
    assert "color:color-mix(in srgb,var(--accent-text) 30%,var(--muted))" in yt2html.CSS


def test_chapters_toggle_keeps_its_line_and_buttons_only_when_useful():
    js = yt2html.PLAYER_JS
    assert "boxPart.chapter.classList.toggle('off', !showChapters);" in js
    assert ".player .now-playing .chapter.off{visibility:hidden}" in yt2html.CSS  # reserved line
    chapter = yt2html.CSS.split(".player .now-playing .chapter{", 1)[1].split("}", 1)[0]
    assert "color:var(--accent-text)" in chapter and "opacity:.75" in chapter
    assert "white-space:nowrap" in chapter and "text-overflow:ellipsis" in chapter
    rule = yt2html.CSS.split(".player .now-playing{", 1)[1].split("}", 1)[0]
    assert "border" not in rule
    assert "if (!lists.length) { followButton.remove(); subtitlesButton.remove(); langs.remove(); }" in js
    assert "if (!chapters.length) chaptersButton.remove();" in js
    assert "subtitlesButton.textContent = 'Chapters'" not in js   # the old label trick is gone
    # each button its own lines: Chapters alone shows no sentence
    assert "boxPart.current.textContent = subtitles ? cue(i) : '';" in js


# Channel avatar: from the owner row under the video, else from an uploader's comment

def owner_data(*urls: str) -> dict:
    sizes = [48, 88, 176]
    return {"contents": {"twoColumnWatchNextResults": {"results": {"results": {"contents": [
        {"videoPrimaryInfoRenderer": {}},
        {"videoSecondaryInfoRenderer": {"owner": {"videoOwnerRenderer": {
            "title": {"runs": [{"text": "Channel"}]},
            "thumbnail": {"thumbnails": [{"url": u, "width": w, "height": w}
                                         for u, w in zip(urls, sizes)]}}}}},
    ]}}}}}


def test_channel_avatar_is_read_from_the_owner_row():
    data = owner_data("https://yt3.ggpht.test/a=s48", "https://yt3.ggpht.test/a=s88",
                      "https://yt3.ggpht.test/a=s176")
    assert yt2html.parse_channel_avatar(data) == "https://yt3.ggpht.test/a=s176"  # largest
    assert yt2html.parse_channel_avatar(owner_data("//yt3.ggpht.test/b=s48")) == \
        "https://yt3.ggpht.test/b=s48"
    assert yt2html.parse_channel_avatar(owner_data()) is None
    assert yt2html.parse_channel_avatar({}) is None


def channel_row(html: str) -> str:
    return html[html.index('<div class="channel">'):html.index('<div class="facts">')]


def test_channel_avatar_without_a_comment_of_the_uploader(info):
    info["comments"] = [c for c in info["comments"] if not c.get("author_is_uploader")]
    info[yt2html.CHANNEL_AVATAR_KEY] = "https://yt3.ggpht.test/owner=s176"
    html = yt2html.render_page(info, None, {"https://yt3.ggpht.test/owner=s176":
                                            "data:image/png;base64,AAAA"}, DAY)
    assert 'class="avatar av0"' in channel_row(html)
    assert '.av0{background-image:url("data:image/png;base64,AAAA")}' in html


def test_channel_avatar_prefers_the_watch_page_and_falls_back_to_a_comment(info):
    uploader_comment = "https://yt3.ggpht.test/@Channel=s88-c-k-c0x00ffffff-no-rj"
    assert yt2html.channel_avatar_url(info) == uploader_comment       # older JSON: no key
    info[yt2html.CHANNEL_AVATAR_KEY] = None                            # page not read
    assert yt2html.channel_avatar_url(info) == uploader_comment
    info[yt2html.CHANNEL_AVATAR_KEY] = "https://yt3.ggpht.test/owner=s176"
    assert yt2html.channel_avatar_url(info) == "https://yt3.ggpht.test/owner=s176"
    info["comments"] = []
    info[yt2html.CHANNEL_AVATAR_KEY] = None
    assert yt2html.channel_avatar_url(info) is None
    html = yt2html.render_page(info, None, {}, DAY)
    assert '<span class="avatar" style="--hue:' in channel_row(html)  # a letter


def test_watch_page_gives_the_channel_avatar(info, fake_ydl):
    from conftest import watch_page
    yt2html.add_watch_page_data(fake_ydl(webpage=watch_page(
        owner_data("https://yt3.ggpht.test/o=s48"), {})), info)
    assert info[yt2html.CHANNEL_AVATAR_KEY] == "https://yt3.ggpht.test/o=s48"


def test_channel_avatar_is_downloaded_small_also_with_no_avatars(capsys, info, tmp_path,
                                                                 monkeypatch):
    info["comments"] = [dict(c, author_is_uploader=False) for c in info["comments"]]
    info[yt2html.CHANNEL_AVATAR_KEY] = "https://yt3.ggpht.test/owner=s176"
    saved = tmp_path / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    requested = []
    monkeypatch.setattr(yt2html, "fetch_image",
                        lambda url: requested.append(url) or "data:image/png;base64,AAAA")
    yt2html.main(["--from-json", str(saved), "-o", str(tmp_path / "a")])
    assert f"https://yt3.ggpht.test/owner=s{yt2html.AVATAR_SIZE}" in requested
    assert any("@newest" in url for url in requested)
    requested.clear()
    # --no-avatars leaves out the comment authors' avatars, not the channel's
    yt2html.main(["--from-json", str(saved), "-o", str(tmp_path / "b"), "--no-avatars"])
    avatars = [url for url in requested if "yt3.ggpht" in url]   # not the thumbnail
    assert avatars == [f"https://yt3.ggpht.test/owner=s{yt2html.AVATAR_SIZE}"]
    html = next((tmp_path / "b").glob("*.html")).read_text("utf-8")
    assert 'class="avatar av0"' in html[html.index('<div class="channel">'):]
