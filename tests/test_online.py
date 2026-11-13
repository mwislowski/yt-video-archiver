"""Checks against real YouTube pages (reference videos).

Skipped unless pytest runs with --online. They download real data, so they
take a minute or two and can fail when YouTube is slow or blocks requests;
a failure here usually means YouTube changed something the program reads,
or yt-dlp needs an update. Run them now and then, not on every change.
"""

import pytest

import yt2html

pytestmark = pytest.mark.online

URL = "https://www.youtube.com/watch?v="


def archive(tmp_path, video_id, *options):
    code = yt2html.main([URL + video_id, "-o", str(tmp_path), "--max-comments", "5",
                         "--no-avatars", "--save-json", *options])
    assert code == 0, "yt2html failed; see its output above"
    page = next(tmp_path.glob(f"*[[]{video_id}[]].html"))
    data = next(tmp_path.glob(f"*[[]{video_id}[]].info.json"))
    import json
    info = json.loads(data.read_text(encoding="utf-8"))
    # Everything below depends on it; a clear message instead of a TypeError later.
    assert info.get(yt2html.WATCH_PAGE_KEY) == "read", (
        "the video page could not be read (see 'Could not read them' above); "
        "usually temporary: run the test again")
    return page.read_text(encoding="utf-8"), info


def test_description_chapters_summary_podcast_and_dubbed_transcript(tmp_path):
    html, info = archive(tmp_path, "Z8lf5oWKfj0", "--transcript")
    chapters = info[yt2html.MARKERS_KEY][0]
    assert chapters["id"] == "chapters" and chapters["source"] == "description"
    types = [e["type"] for e in info[yt2html.EXTRAS_KEY]]
    assert "video_summary" in types and "playlists" in types
    # auto-dubbed video: the transcript must be the original English one
    assert info[yt2html.TRANSCRIPT_KEY]["language"].startswith("en")
    assert "from description" in html
    # data behind "About this copy": written by yt-dlp and by the program
    assert isinstance(info["epoch"], int) and info["_version"]["version"]
    assert info[yt2html.DOWNLOAD_KEY]["yt2html"] == yt2html.__version__
    assert info[yt2html.WATCH_PAGE_KEY] == "read"
    assert info[yt2html.TRANSCRIPT_KEY]["original"] is True
    assert info[yt2html.PLAYLIST_KEY] is None
    assert "<dt>Video page</dt><dd>read</dd>" in html
    import re
    assert re.search(r"<dt>Images</dt><dd>thumbnail \d+×\d+;", html)  # size read from the image


def test_auto_chapters(tmp_path):
    _, info = archive(tmp_path, "qyqCTMirNWg")
    assert info[yt2html.MARKERS_KEY][0]["source"] == "auto"


def test_walkthroughs_and_polish_transcript(tmp_path):
    _, info = archive(tmp_path, "hz6-3-7GGyI", "--transcript")
    titles = [m["title"] for m in info[yt2html.MARKERS_KEY]]
    assert "Walkthroughs" in titles and "Chapters" not in titles
    assert info[yt2html.TRANSCRIPT_KEY]["language"].startswith("pl")


def test_people_mentioned(tmp_path):
    _, info = archive(tmp_path, "9uSXOr-AdAU")
    people = [e for e in info[yt2html.EXTRAS_KEY] if e["type"] == "people"]
    assert people and people[0]["people"]
    assert yt2html.TRANSCRIPT_KEY not in info          # no --transcript: none downloaded


def test_license(tmp_path):
    _, info = archive(tmp_path, "KZtvWKpXPro")
    assert "Creative Commons" in info[yt2html.LICENSE_KEY]["runs"][0]["text"]


def test_info_cards(tmp_path):
    html, info = archive(tmp_path, "EBQzfbqFKN0")
    cards = info[yt2html.CARDS_KEY]
    assert len(cards) >= 2
    assert cards[0]["type"] == "channel" and 60 < cards[0]["start"] < 75
    assert {c["type"] for c in cards[1:]} == {"video"}
    assert 'id="info-cards"' in html
    # the channel avatar comes from the row under the video, not from comments
    assert info[yt2html.CHANNEL_AVATAR_KEY].startswith("https://yt3.")


def test_info_card_links_are_resolved(tmp_path):
    _, info = archive(tmp_path, "dkh1A_mAhY8")
    links = [c for c in info[yt2html.CARDS_KEY] if c["type"] == "link"]
    assert links and all("youtube.com/redirect" not in c["url"] for c in links)


def test_comments_turned_off(tmp_path):
    # okT1dSP6gQQ had comments turned off on 13.11.2026; if the author turns them
    # on, choose another such video
    html, info = archive(tmp_path, "okT1dSP6gQQ")
    assert info[yt2html.COMMENTS_MESSAGE_KEY] == "Comments are turned off."
    assert '<div class="note">Comments are turned off.</div>' in html
