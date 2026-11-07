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
    return page.read_text(encoding="utf-8"), json.loads(data.read_text(encoding="utf-8"))


def test_description_chapters_summary_podcast_and_dubbed_transcript(tmp_path):
    html, info = archive(tmp_path, "Z8lf5oWKfj0")
    chapters = info[yt2html.MARKERS_KEY][0]
    assert chapters["id"] == "chapters" and chapters["source"] == "description"
    types = [e["type"] for e in info[yt2html.EXTRAS_KEY]]
    assert "video_summary" in types and "playlists" in types
    # auto-dubbed video: the transcript must be the original English one
    assert info[yt2html.TRANSCRIPT_KEY]["language"].startswith("en")
    assert "from description" in html


def test_auto_chapters(tmp_path):
    _, info = archive(tmp_path, "qyqCTMirNWg")
    assert info[yt2html.MARKERS_KEY][0]["source"] == "auto"


def test_walkthroughs_and_polish_transcript(tmp_path):
    _, info = archive(tmp_path, "hz6-3-7GGyI")
    titles = [m["title"] for m in info[yt2html.MARKERS_KEY]]
    assert "Walkthroughs" in titles and "Chapters" not in titles
    assert info[yt2html.TRANSCRIPT_KEY]["language"].startswith("pl")


def test_people_mentioned(tmp_path):
    _, info = archive(tmp_path, "9uSXOr-AdAU", "--transcript", "none")
    people = [e for e in info[yt2html.EXTRAS_KEY] if e["type"] == "people"]
    assert people and people[0]["people"]


def test_license(tmp_path):
    _, info = archive(tmp_path, "KZtvWKpXPro", "--transcript", "none")
    assert "Creative Commons" in info[yt2html.LICENSE_KEY]["runs"][0]["text"]


def test_info_cards(tmp_path):
    html, info = archive(tmp_path, "EBQzfbqFKN0", "--transcript", "none")
    cards = info[yt2html.CARDS_KEY]
    assert len(cards) >= 2
    assert cards[0]["type"] == "channel" and 60 < cards[0]["start"] < 75
    assert {c["type"] for c in cards[1:]} == {"video"}
    assert 'id="info-cards"' in html


def test_info_card_links_are_resolved(tmp_path):
    _, info = archive(tmp_path, "dkh1A_mAhY8", "--transcript", "none")
    links = [c for c in info[yt2html.CARDS_KEY] if c["type"] == "link"]
    assert links and all("youtube.com/redirect" not in c["url"] for c in links)
