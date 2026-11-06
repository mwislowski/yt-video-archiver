"""Shared test helpers: synthetic YouTube data and fakes for the network.

All data here is made up, but shaped like the real structures seen on the
reference videos listed in README.md (section B). No test touches the
network unless it is marked "online" and pytest runs with --online.
"""

from __future__ import annotations

import io
import json
import time
import urllib.parse

import pytest

import yt2html


# --------------------------------------------------------------------------
# pytest options: online tests are skipped unless --online is given
# --------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--online", action="store_true",
                     help="also run tests that download real pages from YouTube")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--online"):
        return
    skip = pytest.mark.skip(reason="needs --online (downloads from YouTube)")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------
# No network and no waiting in offline tests
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def offline(monkeypatch, request):
    """Images are never downloaded and time.sleep does not wait."""
    if "online" in request.keywords:
        return
    monkeypatch.setattr(yt2html, "fetch_image", lambda url: None)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


# --------------------------------------------------------------------------
# Builders for YouTube structures (ytInitialData / player response)
# --------------------------------------------------------------------------

def runs(text: str) -> dict:
    return {"runs": [{"text": text}]}


def marker_panel(panel_id: str, title: str, items: list[tuple[int, str]],
                 info_row: bool = False) -> dict:
    """An engagement panel with a timestamp list (macroMarkersListRenderer)."""
    contents = []
    if info_row:
        contents.append({"macroMarkersInfoItemRenderer": {
            "infoText": runs("These chapters are auto-generated")}})
    for start, text in items:
        contents.append({"macroMarkersListItemRenderer": {
            "title": {"simpleText": text},
            "timeDescription": {"simpleText": yt2html.format_duration(start)},
            "onTap": {"watchEndpoint": {"startTimeSeconds": start}},
        }})
    return {"engagementPanelSectionListRenderer": {
        "panelIdentifier": panel_id,
        "header": {"engagementPanelTitleHeaderRenderer": {"title": runs(title)}},
        "content": {"macroMarkersListRenderer": {"contents": contents}},
    }}


def player_bar(key: str, items: list[tuple[int, str]]) -> dict:
    """playerOverlays with chapters marked on the progress bar."""
    chapters = [{"chapterRenderer": {"title": {"simpleText": text},
                                     "timeRangeStartMillis": start * 1000}}
                for start, text in items]
    return {"playerOverlayRenderer": {"decoratedPlayerBarRenderer": {
        "decoratedPlayerBarRenderer": {"playerBar": {"multiMarkersPlayerBarRenderer": {
            "markersMap": [{"key": key, "value": {"chapters": chapters}}]}}}}}}


def structured_description(items: list[dict]) -> dict:
    return {"engagementPanelSectionListRenderer": {
        "panelIdentifier": "engagement-panel-structured-description",
        "content": {"structuredDescriptionContentRenderer": {"items": items}}}}


def comments_panel(count_text: str) -> dict:
    return {"engagementPanelSectionListRenderer": {
        "panelIdentifier": "engagement-panel-comments-section",
        "header": {"engagementPanelTitleHeaderRenderer": {"contextualInfo": runs(count_text)}}}}


def summary_item(*paragraphs: str) -> dict:
    return {"expandableMetadataRenderer": {
        "header": {"collapsedLabel": {"simpleText": "Summary"},
                   "expandedTitle": {"simpleText": "AI-generated video summary"}},
        "expandedContent": {"videoSummaryContentViewModel": {"paragraphs": [
            {"videoSummaryParagraphViewModel": {"text": {"content": p}}} for p in paragraphs]}}}}


INFO_TEXT = "These people are mentioned in the video. Accuracy may vary. Learn more or send feedback"


def people_item(people: list[tuple[str, str]]) -> dict:
    dialog = {"dialogViewModel": {"content": {"basicContentViewModel": {"paragraphs": [{"text": {
        "content": INFO_TEXT,
        "commandRuns": [
            {"startIndex": 60, "length": 10, "onTap": {"innertubeCommand": {
                "urlEndpoint": {"url": "https://support.google.com/youtube/answer/12825599"}}}},
            {"startIndex": 74, "length": 13, "onTap": {"innertubeCommand": {
                "userFeedbackEndpoint": {"hack": True}}}},
        ]}}]}}}}
    return {"videoAttributesSectionViewModel": {
        "headerTitle": "People mentioned",
        "headerSubtitle": f"{len(people)} people",
        "headerInfoButton": {"buttonViewModel": {"onTap": {"innertubeCommand": {
            "showDialogCommand": {"panelLoadingStrategy": {"inlineContent": dialog}}}}}},
        "videoAttributeViewModels": [
            {"videoAttributeViewModel": {"image": {"sources": [{"url": f"https://img.test/p{i}"}]},
                                         "title": name, "subtitle": about}}
            for i, (name, about) in enumerate(people)],
    }}


def podcast_item() -> dict:
    return {"videoDescriptionCourseSectionRenderer": {
        "sectionTitle": runs("Explore the podcast"),
        "mediaLockups": [{"structuredDescriptionPlaylistLockupRenderer": {
            "title": {"simpleText": "Test Podcast"},
            "shortBylineText": {"simpleText": "Test Channel"},
            "videoCountShortText": {"runs": [{"text": "123"}, {"text": " episodes"}]},
            "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLtest123"}},
            "thumbnail": {"thumbnails": [{"url": "https://img.test/c240", "width": 240},
                                         {"url": "https://img.test/c480", "width": 480}]},
        }}],
        "topicLink": {"topicLinkRenderer": {"title": runs("Podcasts")}},
    }}


def license_contents(text: str, url: str) -> dict:
    return {"twoColumnWatchNextResults": {"results": {"results": {"contents": [
        {"videoPrimaryInfoRenderer": {}},
        {"videoSecondaryInfoRenderer": {"metadataRowContainer": {"metadataRowContainerRenderer": {
            "rows": [{"metadataRowRenderer": {
                "title": runs("License"),
                "contents": [{"runs": [{"text": text, "navigationEndpoint": {
                    "urlEndpoint": {"url": url}}}]}]}}]}}}},
    ]}}}}


def watch_page(initial_data: dict, player_response: dict | None = None) -> str:
    """A minimal watch page as YouTube embeds the data in it."""
    page = "<html><script>var ytInitialData = " + json.dumps(initial_data) + ";</script>"
    if player_response is not None:
        page += ("<script>var ytInitialPlayerResponse = " + json.dumps(player_response)
                 + ";var meta = 1;</script>")
    return page + "</html>"


# Chapters used by several tests
CHAPTERS = [(0, "Intro"), (110, "Second part"), (264, "Third part")]
CHAPTER_DESCRIPTION = ("Some text.\n\nTIMESTAMPS\n0:00 Intro\n1:50 Second part\n"
                       "4:24 Third part\n\nFollow me at 12:00 noon")


# --------------------------------------------------------------------------
# Captions (json3) and a fake yt-dlp for downloads
# --------------------------------------------------------------------------

ORIGINAL_CAPTIONS = {"events": [
    {"tStartMs": 0, "segs": [{"utf8": "Hello"}, {"utf8": " and welcome"}]},
    {"tStartMs": 3000, "aAppend": 1, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 6000, "segs": [{"utf8": "first line\nsecond line"}]},
    {"tStartMs": 120000, "segs": [{"utf8": "[Music]"}]},
]}


def translated_captions(language: str) -> dict:
    return {"events": [
        {"tStartMs": 0, "segs": [{"utf8": f"{language}: hello"}]},
        {"tStartMs": 6000, "segs": [{"utf8": f"{language}: lines"}]},
        {"tStartMs": 120000, "segs": [{"utf8": f"{language}: music"}]},
    ]}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTPError(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP Error {status}")
        self.status = status


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL.urlopen: captions, translations, pages."""

    def __init__(self, fail_translations: int = 0, empty_translation: bool = False,
                 webpage: str | None = None):
        self.fail_translations = fail_translations
        self.empty_translation = empty_translation
        self.webpage = webpage
        self.urls: list[str] = []

    def urlopen(self, url: str):
        self.urls.append(url)
        if "/watch?" in url and self.webpage is not None:
            return FakeResponse(self.webpage.encode())
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if "tlang" in query:
            if self.fail_translations:
                self.fail_translations -= 1
                raise FakeHTTPError(429)
            if self.empty_translation:
                return FakeResponse(b"")
            return FakeResponse(json.dumps(translated_captions(query["tlang"][0])).encode())
        return FakeResponse(json.dumps(ORIGINAL_CAPTIONS).encode())


def caption_formats(name: str, url: str = "https://www.youtube.com/api/timedtext?v=X&lang=en") -> list[dict]:
    return [{"ext": "json3", "url": url + "&fmt=json3", "name": name},
            {"ext": "vtt", "url": url + "&fmt=vtt", "name": name}]


@pytest.fixture
def fake_ydl():
    return FakeYDL


# --------------------------------------------------------------------------
# A complete info dict (as yt-dlp returns it, plus yt2html's own keys)
# --------------------------------------------------------------------------

def make_comment(cid, parent, author, text, days_ago, time_text, **extra):
    now = 1_800_000_000
    comment = {"id": cid, "parent": parent, "author": author, "text": text,
               "timestamp": now - days_ago * 86400, "_time_text": time_text,
               "author_thumbnail": f"https://yt3.ggpht.test/{author}=s88-c-k-c0x00ffffff-no-rj",
               "author_url": f"https://www.youtube.com/{author}", "like_count": 0}
    comment.update(extra)
    return comment


def make_comments() -> list[dict]:
    """Comments in yt-dlp's order with comment_sort=new: pinned first, then
    newest to oldest, each followed by its replies (oldest first)."""
    return [
        make_comment("P", "root", "@Channel", "Pinned by the creator", 700, "1 year ago",
                     is_pinned=True, author_is_uploader=True, like_count=1520),
        make_comment("N1", "root", "@newest", "Newest comment", 2, "2 days ago"),
        make_comment("N2", "root", "@middle", "Middle comment", 400, "1 year ago", like_count=12345),
        make_comment("N2r1", "N2", "@reply_a", "@middle first reply", 390, "1 year ago"),
        make_comment("N2r2", "N2", "@Channel", "@reply_a reply by creator", 30, "1 month ago",
                     author_is_uploader=True),
        make_comment("N2r2a", "N2r2", "@reply_b", "nested reply <b>&</b>", 1, "1 day ago (edited)"),
        make_comment("N3", "root", "@oldest", "Oldest comment", 3000, "8 years ago",
                     is_favorited=True, author_thumbnail=None),
    ]


@pytest.fixture
def info() -> dict:
    """A full info dict for rendering tests (no network needed)."""
    return {
        "id": "TESTVIDEO01", "title": 'Test: "video" | part 1 / <2>?',
        "webpage_url": "https://www.youtube.com/watch?v=TESTVIDEO01",
        "uploader": "Channel", "uploader_url": "https://www.youtube.com/@Channel",
        "channel_is_verified": True, "channel_follower_count": 1_230_000,
        "view_count": 23_456_789, "like_count": 345_678, "comment_count": 7,
        "duration": 597, "timestamp": 1_212_060_266, "upload_date": "20080529",
        "thumbnail": "https://i.ytimg.test/vi/TESTVIDEO01/maxresdefault.jpg",
        "description": "Description with https://example.org and 1:23.\n" + CHAPTER_DESCRIPTION,
        "comments": make_comments(),
        yt2html.ARCHIVED_KEY: "2026-11-05",
        yt2html.LIMIT_KEY: None,
        yt2html.YT_COUNT_KEY: "9",
        yt2html.MARKERS_KEY: [
            {"id": "chapters", "title": "Chapters", "source": "description",
             "items": [{"start_time": s, "time_text": yt2html.format_duration(s), "title": t}
                       for s, t in CHAPTERS]},
            {"id": "engagement-panel-macro-markers-problem-walkthroughs",
             "title": "Walkthroughs", "source": None,
             "items": [{"start_time": 21, "time_text": "0:21", "title": "What is a test?"}]},
        ],
        yt2html.EXTRAS_KEY: [],
        yt2html.LICENSE_KEY: None,
        yt2html.TRANSCRIPT_KEY: {
            "language": "en", "name": "English", "kind": "auto",
            "cues": yt2html.parse_json3_captions(ORIGINAL_CAPTIONS),
            "translations": {"pl": {"language": "pl", "name": "Polish",
                                    "cues": yt2html.parse_json3_captions(translated_captions("pl"))}},
        },
    }


@pytest.fixture
def info_file(tmp_path, info):
    """The info dict saved as an .info.json file, as --save-json writes it."""
    path = tmp_path / "test.info.json"
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return path
