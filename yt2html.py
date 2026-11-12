"""yt2html - archive a YouTube video page as a single self-contained HTML file.

The page contains the video's metadata (thumbnail, title, channel, counts,
dates, full description, AI-generated video summary), YouTube's timestamp
lists (Chapters, Walkthroughs), People mentioned, the podcast the video
belongs to, the transcript, the license (if not the standard one) and all
comments with their reply threads, sorted oldest first. Images (thumbnail,
avatars, photos) are embedded as data URIs, so the result is one file that
works offline, like a page saved with SingleFile. The video itself is not
downloaded.

Layout of this file: configuration and data keys; fetching (yt-dlp, then the
watch page for what yt-dlp does not return); transcript; images; formatting
helpers; HTML rendering; command line.

Usage (installed as a command, or with "uv run yt2html.py" in the project):
    yt2html URL [URL ...] [options]
    yt2html --help
    yt2html --version
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import glob
import html
import html.parser
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp
from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS

# The program version; pyproject.toml reads it from here (single source).
__version__ = "0.8.0"

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Default folder for generated pages: the current folder, i.e. wherever the
# command is run (for example the folder with the downloaded videos).
DEFAULT_OUTPUT_DIR = "."

# File name template (yt-dlp syntax). The default matches yt-dlp's own default
# "%(title)s [%(id)s].%(ext)s", so the page sits next to the downloaded video
# under the same base name.
OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

DATE_FORMAT = "%d.%m.%Y"            # DD.MM.YYYY
THOUSANDS_SEPARATOR = " "      # no-break space: 1 234 567

AVATAR_SIZE = 80                    # requested pixel size (displayed at 40/24 px)
IMAGE_WORKERS = 8                   # parallel image downloads
IMAGE_TIMEOUT = 20                  # seconds per image request
IMAGE_RETRIES = 3

# YouTube answers "HTTP 429 Too Many Requests" to auto-translated captions
# requested less than about 60 s after the video data was fetched (only
# translations; original captions are not affected). So the translation is
# requested no sooner than this many seconds after fetching started, and on a
# 429 it is retried after a further pause.
TRANSLATION_WAIT = 65
TRANSLATION_RETRY_WAITS = (60, 120)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

YOUTUBE = "https://www.youtube.com"

# --------------------------------------------------------------------------
# Keys of yt2html's own data in the info dict (and in --save-json files),
# next to yt-dlp's fields
# --------------------------------------------------------------------------

# Date of fetching, stored so that --from-json keeps the original snapshot date.
ARCHIVED_KEY = "yt2html_archived"

# Key under which the timestamp lists are stored in the info dict (and in a
# --save-json file). Not a yt-dlp field.
MARKERS_KEY = "yt2html_marker_lists"

# Key for extra description elements (e.g. the AI-generated video summary).
EXTRAS_KEY = "yt2html_description_extras"

# Comment count as YouTube shows it ("116", "1.2K"). yt-dlp replaces its own
# `comment_count` with the number of downloaded comments when it fetches them.
YT_COUNT_KEY = "yt2html_youtube_comment_count"

# The --max-comments value used when fetching (None = no limit).
LIMIT_KEY = "yt2html_max_comments"

# The "License" row of the video details: {title, runs: [{text, url}]}.
LICENSE_KEY = "yt2html_license"

# The caption track YouTube's transcript panel opens with: {language, kind}.
CAPTION_KEY = "yt2html_default_caption_track"

# Languages YouTube can translate captions into: {code: name}.
TRANSLATIONS_KEY = "yt2html_translation_languages"

# The transcript: {language, name, kind, cues: [{start, text}], translations:
# {lang: {language, name, cues}}}; None if the video has none, absent with
# --transcript none. All versions on the page are built from the same cues.
TRANSCRIPT_KEY = "yt2html_transcript"

# The author's info cards: [{type, title, url, image, details, message, start}];
# absent with --no-cards, None when the watch page cannot be read.
CARDS_KEY = "yt2html_cards"

# The channel's avatar (image URL) from the watch page's owner row; yt-dlp
# gives it only for channel pages, not for videos.
CHANNEL_AVATAR_KEY = "yt2html_channel_avatar"

# How the data was downloaded, for "About this copy":
# {yt2html: version, cookies_from_browser: browser name or None}.
DOWNLOAD_KEY = "yt2html_download"

# The playlist the video was archived from: {id, title, position, count}
# (position and count on the download day); None if not from a playlist.
PLAYLIST_KEY = "yt2html_playlist"

# Why the transcript is missing when TRANSCRIPT_KEY is None.
TRANSCRIPT_MISSING_KEY = "yt2html_transcript_missing"

# The watch page: "read", "partial" (without the player data) or "failed".
WATCH_PAGE_KEY = "yt2html_watch_page"

# Keys filled from the watch page; all None when the page cannot be read.
PAGE_KEYS = (MARKERS_KEY, EXTRAS_KEY, YT_COUNT_KEY, LICENSE_KEY, CAPTION_KEY, TRANSLATIONS_KEY,
             CHANNEL_AVATAR_KEY)


# --------------------------------------------------------------------------
# Fetching data
# --------------------------------------------------------------------------

# The format yt-dlp is told to "choose" when nothing is downloaded: a single
# format, so it does not plan to merge image and sound and warn about a missing
# ffmpeg (needed only to download a video).
NO_MERGE_FORMAT = "bv*/b/ba"


def fetch_formats(url: str, args: argparse.Namespace) -> dict:
    """Only the video's data, for --video-formats: no comments, no watch page.
    Notes, like fetch_info(), whether Deno was there and whether signed in."""
    opts = {"skip_download": True, "noplaylist": True, "extract_flat": "in_playlist",
            "ignore_no_formats_error": True, "noprogress": True, "quiet": True,
            "format": NO_MERGE_FORMAT}
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies_from_browser)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    info[DOWNLOAD_KEY] = {"yt2html": __version__,
                          "cookies_from_browser": browser_name(args.cookies_from_browser),
                          "deno": shutil.which("deno") is not None}
    return info


def fetch_info(url: str, args: argparse.Namespace) -> dict:
    """Extract metadata and comments with yt-dlp (no video download)."""
    max_comments = "all" if args.max_comments is None else str(args.max_comments)
    opts = {
        "skip_download": True,
        "noplaylist": True,
        # A URL that still turns out to be a playlist or channel returns only
        # the list of its videos, which are not extracted (comments included).
        "extract_flat": "in_playlist",
        "getcomments": not args.no_comments,
        # The video is not downloaded, so a missing format must not be fatal.
        "ignore_no_formats_error": True,
        "format": NO_MERGE_FORMAT,   # no warning about a missing ffmpeg
        "noprogress": True,
        "extractor_args": {
            "youtube": {
                # Newest first gives the exact chronological order of top-level
                # comments; the page reverses it.
                "comment_sort": ["new"],
                # total, parents, replies, replies per thread
                "max_comments": [max_comments, "all", "all", "all"],
            }
        },
    }
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies_from_browser)
    started = time.monotonic()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
        info[ARCHIVED_KEY] = dt.date.today().isoformat()
        info[DOWNLOAD_KEY] = {"yt2html": __version__,
                              "cookies_from_browser": browser_name(args.cookies_from_browser),
                              # yt-dlp needs a JavaScript runtime (by default Deno) for
                              # YouTube's full list of formats (Video formats section)
                              "deno": shutil.which("deno") is not None}
        if not args.no_comments:
            info[LIMIT_KEY] = args.max_comments
        if info.get("_type") not in ("playlist", "multi_video"):
            add_watch_page_data(ydl, info, cards=not args.no_cards)
            if args.transcript:
                info[TRANSCRIPT_KEY] = fetch_transcript(
                    ydl, info, bilingual_languages(args.transcript),
                    not_before=started + TRANSLATION_WAIT)
        return info


# --------------------------------------------------------------------------
# Reading the watch page (what yt-dlp does not return)
# --------------------------------------------------------------------------

def add_watch_page_data(ydl: yt_dlp.YoutubeDL, info: dict, cards: bool = True) -> None:
    """Fill PAGE_KEYS from the watch page, fetched once more.

    yt-dlp keeps only one timestamp list, as `chapters`, without its name and
    origin, replaces YouTube's comment count with the downloaded number, and
    returns nothing of the description extras, the license link or the
    default caption track. The page is fetched with yt-dlp's cookies and
    consent; ytInitialData and ytInitialPlayerResponse are read from it.
    If it cannot be read, all PAGE_KEYS are None and the page falls back to
    yt-dlp's data where it can. Info cards are read only if `cards` is true
    (--no-cards leaves CARDS_KEY out and does nothing for them).
    """
    url = f"{YOUTUBE}/watch?v={info.get('id')}&hl=en"
    try:
        log("Downloading chapters, timestamp lists and description extras...")
        with ydl.urlopen(url) as response:
            webpage = response.read().decode("utf-8", errors="replace")
        data = initial_data_of(webpage)
    except Exception as error:
        log(f"  Could not read them ({error}); using yt-dlp chapters instead.")
        info.update(dict.fromkeys(PAGE_KEYS))
        info[WATCH_PAGE_KEY] = "failed"
        if cards:
            info[CARDS_KEY] = None
        return
    try:
        player_response = embedded_json(webpage, "ytInitialPlayerResponse")
        info[CAPTION_KEY] = default_caption_track(player_response)
        info[TRANSLATIONS_KEY] = translation_languages(player_response)
        info[WATCH_PAGE_KEY] = "read"
    except Exception:
        info[CAPTION_KEY] = info[TRANSLATIONS_KEY] = None
        info[WATCH_PAGE_KEY] = "partial"
    info[MARKERS_KEY] = parse_marker_lists(data, info.get("description") or "",
                                           info.get("duration"))
    info[EXTRAS_KEY] = parse_description_extras(data)
    info[YT_COUNT_KEY] = parse_comment_count(data)
    info[LICENSE_KEY] = parse_license(data)
    info[CHANNEL_AVATAR_KEY] = parse_channel_avatar(data)
    if cards:
        info[CARDS_KEY] = parse_cards(data)
        resolve_card_links(ydl, info[CARDS_KEY])


def unwrap_redirect(url: str) -> str:
    """YouTube's /redirect?q=<target> links -> the target address."""
    if "youtube.com/redirect" in url or url.startswith("/redirect"):
        target = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q")
        if target:
            return target[0]
    return urllib.parse.urljoin(YOUTUBE, url)


def secondary_info(data: dict) -> list[dict]:
    """The videoSecondaryInfoRenderer blocks (channel row, license) of the watch page."""
    contents = ((((data.get("contents") or {}).get("twoColumnWatchNextResults") or {})
                 .get("results") or {}).get("results") or {}).get("contents") or []
    return [c["videoSecondaryInfoRenderer"] for c in contents
            if isinstance(c.get("videoSecondaryInfoRenderer"), dict)]


def parse_channel_avatar(data: dict) -> str | None:
    """The channel's avatar from the owner row under the video (largest size;
    the download asks for a small one anyway)."""
    for secondary in secondary_info(data):
        owner = (secondary.get("owner") or {}).get("videoOwnerRenderer") or {}
        sources = [t for t in (owner.get("thumbnail") or {}).get("thumbnails") or []
                   if t.get("url")]
        if sources:
            url = max(sources, key=lambda t: t.get("width") or 0)["url"]
            return urllib.parse.urljoin("https:", url)  # sometimes "//yt3..."
    return None


def parse_license(data: dict) -> dict | None:
    """The "License" row (e.g. Creative Commons) from the video's metadata rows.

    It is in videoSecondaryInfoRenderer.metadataRowContainer, not in the
    expanded description's "Video details", and only on videos whose author
    chose a license other than the standard YouTube one.
    """
    for secondary in secondary_info(data):
        rows = (((secondary.get("metadataRowContainer") or {})
                 .get("metadataRowContainerRenderer") or {}).get("rows") or [])
        for row in rows:
            renderer = row.get("metadataRowRenderer") or {}
            title = text_of(renderer.get("title")).strip()
            if title.lower() != "license":
                continue
            runs = []
            for part in renderer.get("contents") or []:
                if runs:
                    runs.append({"text": ", ", "url": None})
                for run in part.get("runs") or [{"text": text_of(part)}]:
                    url = (((run.get("navigationEndpoint") or {}).get("urlEndpoint") or {})
                           .get("url"))
                    runs.append({"text": run.get("text") or "",
                                 "url": unwrap_redirect(url) if url else None})
            if any(r["text"].strip() for r in runs):
                return {"title": title, "runs": runs}
    return None


def license_of(info: dict) -> dict | None:
    """License to show; falls back to yt-dlp's plain-text `license`."""
    if LICENSE_KEY in info and watch_page_status(info) != "failed":
        return info[LICENSE_KEY]
    # Watch page not read (or an older JSON file): yt-dlp's text, no link.
    text = info.get("license")
    return {"title": "License", "runs": [{"text": text, "url": None}]} if text else None


def parse_comment_count(data: dict) -> str | None:
    """Comment count text from the comments panel header ("116", "1.2K")."""
    for panel in data.get("engagementPanels") or []:
        renderer = panel.get("engagementPanelSectionListRenderer") or {}
        if renderer.get("panelIdentifier") in ("engagement-panel-comments-section",
                                               "comment-item-section"):
            header = (renderer.get("header") or {}).get("engagementPanelTitleHeaderRenderer") or {}
            text = text_of(header.get("contextualInfo")).strip()
            if text:
                return text
    return None


def youtube_comment_count(info: dict) -> tuple[str | None, int | None]:
    """(text for the Comments tile, number) of the count YouTube shows.

    Exact counts are formatted like the other numbers; short forms such as
    "1.2K" are shown as they are, since the exact number is unknown.
    """
    text = info.get(YT_COUNT_KEY)
    if text:
        digits = text.replace(",", "").replace("\u00a0", "").replace(" ", "")
        if digits.isdigit():
            return format_int(int(digits)), int(digits)
        return text, yt_dlp.utils.parse_count(text)
    # Without the page data: yt-dlp's count is YouTube's when comments were
    # not downloaded; otherwise it is the downloaded number, which equals the
    # available comments only for a complete download without a limit.
    count = info.get("comment_count")
    complete = LIMIT_KEY in info and info[LIMIT_KEY] is None
    if count is not None and ("comments" not in info or complete):
        return format_int(count), count
    return None, None


def text_of(node) -> str:
    """Text of a YouTube text object ({'simpleText': ...} or {'runs': [...]})."""
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    if "content" in node:
        return str(node["content"])
    return "".join(str(run.get("text", "")) for run in node.get("runs") or [])


def embedded_json(webpage: str, name: str) -> dict:
    """A JSON object assigned to a variable in a watch page (e.g. ytInitialData)."""
    match = re.search(rf"""(?:window\s*\[\s*["']{name}["']\s*\]|\b{name})\s*=\s*""", webpage)
    if not match:
        raise ValueError(f"{name} not found")
    data, _ = json.JSONDecoder().raw_decode(webpage, match.end())
    return data


def initial_data_of(webpage: str) -> dict:
    """The ytInitialData object embedded in a watch page."""
    return embedded_json(webpage, "ytInitialData")


def translation_languages(player_response: dict) -> dict[str, str]:
    """{code: name} of the languages YouTube's auto-translate offers."""
    tracklist = (player_response.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}
    return {lang["languageCode"]: text_of(lang.get("languageName")) or lang["languageCode"]
            for lang in tracklist.get("translationLanguages") or [] if lang.get("languageCode")}


def default_caption_track(player_response: dict) -> dict | None:
    """Language and kind of the caption track the transcript panel opens with.

    The player lists one audio track per language (auto-dubbed videos have
    many); `defaultAudioTrackIndex` points to the original audio, and its
    `defaultCaptionTrackIndex`, when present, to the caption track YouTube
    picks for it. Without that index, the original audio language is used.
    Returns {language, kind: "asr" | "manual" | None} or None.
    """
    tracklist = (player_response.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}
    captions = tracklist.get("captionTracks") or []
    audio_tracks = tracklist.get("audioTracks") or []
    if not captions:
        return None
    index = tracklist.get("defaultAudioTrackIndex") or 0
    audio = audio_tracks[index] if 0 <= index < len(audio_tracks) else {}
    caption_index = audio.get("defaultCaptionTrackIndex")
    if isinstance(caption_index, int) and 0 <= caption_index < len(captions):
        track = captions[caption_index]
        if track.get("languageCode"):
            return {"language": track["languageCode"],
                    "kind": "asr" if track.get("kind") == "asr" else "manual"}
    # Original audio language: the audio stream marked "original", else the
    # audio track the player starts with.
    language = None
    for fmt in (player_response.get("streamingData") or {}).get("adaptiveFormats") or []:
        audio_track = fmt.get("audioTrack") or {}
        if "original" in (audio_track.get("displayName") or "").lower():
            language = (audio_track.get("id") or "").split(".")[0]
            break
    language = language or (audio.get("audioTrackId") or "").split(".")[0]
    if not language:
        indices = audio.get("captionTrackIndices") or [0]
        track = captions[indices[0]] if 0 <= indices[0] < len(captions) else captions[0]
        return {"language": track.get("languageCode"),
                "kind": "asr" if track.get("kind") == "asr" else "manual"}
    return {"language": language, "kind": None}


def read_panels(data: dict) -> list[dict]:
    """Every engagement panel with a timestamp list (macroMarkersListRenderer)."""
    lists = []
    for panel in data.get("engagementPanels") or []:
        renderer = panel.get("engagementPanelSectionListRenderer") or {}
        contents = (((renderer.get("content") or {})
                     .get("macroMarkersListRenderer") or {})
                    .get("contents") or [])
        items = []
        for content in contents:
            # Info rows ("These chapters are auto-generated") are skipped.
            marker = content.get("macroMarkersListItemRenderer")
            if not marker:
                continue
            time_text = text_of(marker.get("timeDescription"))
            start = ((marker.get("onTap") or {}).get("watchEndpoint") or {}).get("startTimeSeconds")
            if start is None:
                start = parse_clock(time_text)
            items.append({"start_time": start, "time_text": time_text,
                          "title": text_of(marker.get("title"))})
        if items:
            header = (renderer.get("header") or {}).get("engagementPanelTitleHeaderRenderer") or {}
            lists.append({"id": renderer.get("panelIdentifier") or "",
                          "title": text_of(header.get("title")),
                          "items": items})
    return lists


def read_player_bar(data: dict) -> list[dict]:
    """Chapters marked on the progress bar: [{key, items}].

    The key says where YouTube took them from (DESCRIPTION_CHAPTERS,
    AUTO_CHAPTERS). Heatmap entries have no chapters and are skipped.
    """
    bar = (((((data.get("playerOverlays") or {})
              .get("playerOverlayRenderer") or {})
             .get("decoratedPlayerBarRenderer") or {})
            .get("decoratedPlayerBarRenderer") or {})
           .get("playerBar") or {})
    entries = []
    for marker in (bar.get("multiMarkersPlayerBarRenderer") or {}).get("markersMap") or []:
        chapters = (marker.get("value") or {}).get("chapters") or []
        entries.append({"key": marker.get("key") or "", "chapters": chapters})
    # Older page layout without a key.
    old = (bar.get("chapteredPlayerBarRenderer") or {}).get("chapters")
    if old:
        entries.append({"key": "", "chapters": old})
    result = []
    for entry in entries:
        items = []
        for chapter in entry["chapters"]:
            renderer = chapter.get("chapterRenderer") or {}
            millis = renderer.get("timeRangeStartMillis")
            if millis is None:
                continue
            start = int(millis) // 1000
            items.append({"start_time": start, "time_text": format_duration(start),
                          "title": text_of(renderer.get("title"))})
        if items:
            result.append({"key": entry["key"], "items": items})
    return result


CLOCK_RE = re.compile(r"(?<![\d:])(?:\d{1,2}:)?\d{1,2}:\d{2}(?![\d:])")


def parse_clock(text: str) -> int | None:
    """'1:02:03' -> 3723; None if the text is not a clock time."""
    if not re.fullmatch(r"\d+(?::\d{1,2}){1,2}", text or ""):
        return None
    return sum(int(p) * 60 ** i for i, p in enumerate(reversed(text.split(":"))))


def description_chapter_starts(description: str, duration) -> list[int] | None:
    """Start times of a chapter list in the description, or None if none is valid.

    YouTube's rules: the list starts at 0:00, has at least three timestamps in
    ascending order, and every chapter is at least 10 seconds long. The list
    is the run of consecutive timestamped lines that begins with the first
    0:00 line; it ends at the first line without a timestamp.
    """
    starts = []
    for line in description.splitlines():
        match = CLOCK_RE.search(line)
        if not match:
            if starts:
                break
            continue
        seconds = parse_clock(match.group(0))
        if not starts:
            if seconds == 0:
                starts.append(0)
            continue
        if seconds <= starts[-1]:
            break
        starts.append(seconds)
    if len(starts) < 3:
        return None
    ends = starts[1:] + ([duration] if duration else [])
    if any(end - start < 10 for start, end in zip(starts, ends)):
        return None
    return starts


# Where YouTube says the chapters come from, by progress-bar key and panel id.
BAR_SOURCES = {"DESCRIPTION_CHAPTERS": "description", "AUTO_CHAPTERS": "auto"}
PANEL_SOURCES = {
    "engagement-panel-macro-markers-description-chapters": "description",
    "engagement-panel-macro-markers-auto-chapters": "auto",
}


def decide_chapter_source(claims: list[str], items: list[dict],
                          description: str, duration) -> tuple[str, str | None]:
    """Combine YouTube's own markings with a check of the description.

    Returns (source, problem): source is "description", "auto" or "uncertain";
    problem explains an "uncertain" result.
    """
    unknown = [c for c in claims if c not in ("description", "auto")]
    if unknown:
        return "uncertain", f"unknown chapter type {', '.join(unknown)}"
    if not claims:
        return "uncertain", "YouTube does not say where the chapters come from"
    if len(set(claims)) > 1:
        return "uncertain", "YouTube's own markings disagree: " + ", ".join(claims)
    claim = claims[0]
    listed = description_chapter_starts(description, duration)
    shown = [item["start_time"] for item in items]
    matches = listed is not None and listed == shown
    if claim == "description":
        if matches:
            return "description", None
        return "uncertain", ("YouTube says the chapters come from the description, "
                             "but the description has no matching chapter list")
    if listed is None:
        return "auto", None
    return "uncertain", ("YouTube says the chapters are auto-generated, "
                         "but the description has a valid chapter list")


def parse_marker_lists(data: dict, description: str = "", duration=None) -> list[dict]:
    """Chapters first, then every other timestamp list (e.g. Walkthroughs).

    Each list: {id, title, source, items: [{start_time, time_text, title}]},
    where source is "description", "auto", "uncertain" (chapters) or None.
    """
    panels = read_panels(data)
    bar = read_player_bar(data)
    chapter_panels = [p for p in panels if "chapters" in p["id"]]
    other_panels = [p for p in panels if "chapters" not in p["id"]]

    lists = []
    if bar or chapter_panels:
        # The progress bar defines the chapters; panels give name and origin.
        items = bar[0]["items"] if bar else chapter_panels[0]["items"]
        claims = [BAR_SOURCES.get(b["key"], b["key"] or "(no key)") for b in bar[:1]]
        claims += [PANEL_SOURCES.get(p["id"], p["id"]) for p in chapter_panels]
        source, problem = decide_chapter_source(claims, items, description, duration)
        if problem:
            log(f"  Chapter source unclear: {problem}. Shown without a source note.")
        title = next((p["title"] for p in chapter_panels if p["title"]), "") or "Chapters"
        lists.append({"id": "chapters", "title": title, "source": source,
                      "source_problem": problem, "items": items})
    for panel in other_panels:
        lists.append({**panel, "title": panel["title"] or "Timestamps", "source": None})
    return lists


def structured_description_items(data: dict) -> list[dict]:
    """Items of the expanded description panel, in YouTube's display order."""
    for panel in data.get("engagementPanels") or []:
        renderer = panel.get("engagementPanelSectionListRenderer") or {}
        if renderer.get("panelIdentifier") == "engagement-panel-structured-description":
            content = (renderer.get("content") or {}).get("structuredDescriptionContentRenderer") or {}
            return content.get("items") or []
    return []


def parse_description_extras(data: dict) -> list[dict]:
    """Extra elements YouTube shows below the description text.

    In YouTube's order. Types:
      video_summary   AI-generated video summary: {title, paragraphs}
      people          "People mentioned": {title, subtitle, info, people:
                      [{name, description, image}]}
      playlists       "Explore the podcast" (or course): {title, items:
                      [{title, url, byline, count, image}]}
    `info` is attributed text: {text, links: [{start, length, url}]}.
    """
    extras = []
    for item in structured_description_items(data):
        if "videoAttributesSectionViewModel" in item:
            people = parse_people_section(item["videoAttributesSectionViewModel"])
            if people:
                extras.append(people)
            continue
        if "videoDescriptionCourseSectionRenderer" in item:
            playlists = parse_playlist_section(item["videoDescriptionCourseSectionRenderer"])
            if playlists:
                extras.append(playlists)
            continue
        metadata = item.get("expandableMetadataRenderer")
        if metadata:
            summary = (metadata.get("expandedContent") or {}).get("videoSummaryContentViewModel")
            if summary:
                paragraphs = [
                    ((p.get("videoSummaryParagraphViewModel") or {}).get("text") or {}).get("content") or ""
                    for p in summary.get("paragraphs") or []
                ]
                paragraphs = [p for p in paragraphs if p.strip()]
                if paragraphs:
                    title = text_of((metadata.get("header") or {}).get("expandedTitle"))
                    extras.append({"type": "video_summary",
                                   "title": title or "AI-generated video summary",
                                   "paragraphs": paragraphs})
    return extras


def best_image_url(sources: list[dict], wanted: int = 240) -> str | None:
    """The smallest image at least `wanted` px wide, else the largest one."""
    sources = [s for s in sources or [] if s.get("url")]
    if not sources:
        return None
    sized = sorted(sources, key=lambda s: s.get("width") or 0)
    return next((s["url"] for s in sized if (s.get("width") or 0) >= wanted), sized[-1]["url"])


def attributed_text(node: dict) -> dict:
    """{text, links} from a YouTube attributed text ({content, commandRuns}).

    Only runs that open a web address become links; runs that trigger
    YouTube's own scripts (e.g. "send feedback") stay plain text.
    """
    links = []
    for run in node.get("commandRuns") or []:
        command = ((run.get("onTap") or {}).get("innertubeCommand") or {})
        url = (command.get("urlEndpoint") or {}).get("url")
        if url and run.get("startIndex") is not None and run.get("length"):
            if url.startswith("//"):
                url = "https:" + url
            links.append({"start": run["startIndex"], "length": run["length"], "url": url})
    return {"text": node.get("content") or "", "links": links}


def parse_people_section(section: dict) -> dict | None:
    """The "People mentioned" section (videoAttributesSectionViewModel)."""
    people = []
    for attribute in section.get("videoAttributeViewModels") or []:
        model = attribute.get("videoAttributeViewModel") or {}
        name = model.get("title")
        if isinstance(name, dict):
            name = text_of(name)
        if not name:
            continue
        description = model.get("subtitle")
        if isinstance(description, dict):
            description = text_of(description)
        people.append({"name": name, "description": description or "",
                       "image": best_image_url((model.get("image") or {}).get("sources"))})
    if not people:
        return None
    # The text of the (i) dialog next to the heading.
    dialog = (((((((section.get("headerInfoButton") or {}).get("buttonViewModel") or {})
                  .get("onTap") or {}).get("innertubeCommand") or {})
                .get("showDialogCommand") or {}).get("panelLoadingStrategy") or {})
              .get("inlineContent") or {}).get("dialogViewModel") or {}
    paragraphs = ((dialog.get("content") or {}).get("basicContentViewModel") or {}).get("paragraphs") or []
    info = attributed_text(paragraphs[0].get("text") or {}) if paragraphs else None
    return {"type": "people", "title": section.get("headerTitle") or "People mentioned",
            "subtitle": section.get("headerSubtitle") or "", "info": info, "people": people}


def parse_playlist_section(section: dict) -> dict | None:
    """The "Explore the podcast" section (videoDescriptionCourseSectionRenderer).

    The large "Podcasts" button below it (topicLink) is left out.
    """
    items = []
    for lockup in section.get("mediaLockups") or []:
        model = lockup.get("structuredDescriptionPlaylistLockupRenderer")
        if not model:
            continue
        browse_id = (((model.get("navigationEndpoint") or {}).get("browseEndpoint") or {})
                     .get("browseId") or "")
        playlist_id = browse_id[2:] if browse_id.startswith("VL") else browse_id
        items.append({
            "title": text_of(model.get("title")),
            "url": f"{YOUTUBE}/playlist?list={playlist_id}" if playlist_id else None,
            "byline": text_of(model.get("shortBylineText")),
            "count": text_of(model.get("videoCountShortText")),
            "image": best_image_url((model.get("thumbnail") or {}).get("thumbnails")),
        })
    if not items:
        return None
    return {"type": "playlists", "title": text_of(section.get("sectionTitle")) or "Explore the podcast",
            "items": items}


# Info cards: the content is a lockup in the description panel's infocards
# section; the time and the teaser text are in ytInitialData["cards"], in the
# same order. (ytInitialPlayerResponse["cards"] holds only YouTube's own
# "View corrections" teaser, not the author's cards.)
CARD_TYPES = {
    "structuredDescriptionVideoLockupRenderer": "video",
    "structuredDescriptionPlaylistLockupRenderer": "playlist",
    "structuredDescriptionChannelLockupRenderer": "channel",
    "mediaLockupRenderer": "link",
}


def card_cues(data: dict) -> list[dict]:
    """[{start (seconds or None), message}] of the cards, in order."""
    cues = []
    collection = (data.get("cards") or {}).get("cardCollectionRenderer") or {}
    for card in collection.get("cards") or []:
        renderer = card.get("cardRenderer") or {}
        cue = (renderer.get("cueRanges") or [{}])[0]
        try:
            start = int(cue.get("startCardActiveMs")) / 1000
        except (TypeError, ValueError):
            start = None
        teaser = (renderer.get("teaser") or {}).get("simpleCardTeaserRenderer") or {}
        cues.append({"start": start, "message": text_of(teaser.get("message")).strip()})
    return cues


def parse_card(content: dict) -> dict | None:
    """One info card's target from its lockup; None for unknown kinds."""
    kind = next((CARD_TYPES[key] for key in CARD_TYPES if key in content), None)
    if not kind:
        return None
    model = next(content[key] for key in CARD_TYPES if key in content)
    navigation = model.get("navigationEndpoint") or {}
    title = text_of(model.get("title")).strip()
    byline = re.sub(r"^by\s+", "", text_of(model.get("shortBylineText")).strip())
    url, details = None, []
    if kind == "video":
        video_id = (navigation.get("watchEndpoint") or {}).get("videoId")
        url = f"{YOUTUBE}/watch?v={video_id}" if video_id else None
        details = [byline, text_of(model.get("lengthText")), text_of(model.get("metadataDetails"))]
    elif kind == "playlist":
        playlist_id = (navigation.get("watchEndpoint") or {}).get("playlistId")
        if not playlist_id:
            browse_id = (navigation.get("browseEndpoint") or {}).get("browseId") or ""
            playlist_id = browse_id[2:] if browse_id.startswith("VL") else browse_id
        url = f"{YOUTUBE}/playlist?list={playlist_id}" if playlist_id else None
        count = text_of(model.get("videoCountShortText")).strip()
        details = [byline, f"{count} video{'' if count == '1' else 's'}" if count.isdigit() else count]
    elif kind == "channel":
        channel_id = (navigation.get("browseEndpoint") or {}).get("browseId")
        url = f"{YOUTUBE}/channel/{channel_id}" if channel_id else None
        details = [text_of(model.get("subscriberCountText"))]
    else:  # link: a YouTube redirect; the target address is not in the data
        target = ((model.get("endpoint") or {}).get("urlEndpoint") or {}).get("url")
        url = unwrap_redirect(target) if target else None
        subtitle = text_of(model.get("subtitle")).strip()
        details = [subtitle if subtitle != title else ""]
    image = best_image_url(((model.get("thumbnail") or model.get("thumbnailDetails") or {})
                            .get("thumbnails")), wanted=160)
    return {"type": kind, "title": title, "url": url, "image": image,
            "details": [d.strip() for d in details if d and d.strip()]}


# The "Are you sure you want to leave YouTube?" page behind a link card's
# redirect has the target in this button.
LEAVE_BUTTON_RE = re.compile(r'<a\b[^>]*\bid="invalid-token-redirect-goto-site-button"[^>]*>')
HREF_RE = re.compile(r'\bhref="([^"]*)"')


def resolve_card_links(ydl, cards: list[dict]) -> None:
    """Replace the one-time YouTube redirects of link cards with their target
    address and add its domain to the details. One request per link card;
    if it fails, the card keeps the redirect and the video is archived as usual."""
    links = [c for c in cards if c.get("type") == "link"
             and "youtube.com/redirect" in (c.get("url") or "")]
    if links:
        log(f"Reading the target{'s' if len(links) > 1 else ''} of "
            f"{plural(len(links), 'info card link')}...")
    for card in links:
        try:
            with ydl.urlopen(card["url"]) as response:
                page = response.read().decode("utf-8", errors="replace")
            button = LEAVE_BUTTON_RE.search(page)
            href = HREF_RE.search(button.group(0)) if button else None
            target = html.unescape(href.group(1)) if href else ""
            if not re.match(r"https?://", target):
                raise ValueError("no target address on the page")
        except Exception as error:
            log(f"  Info card link {card.get('title')!r} kept as a YouTube redirect ({error}).")
            continue
        card["url"] = target
        domain = urllib.parse.urlsplit(target).netloc.removeprefix("www.")
        if domain:
            card["details"] = [*card.get("details", []), domain]


def parse_cards(data: dict) -> list[dict]:
    """The author's info cards, sorted by time, each with its time and the teaser
    text if it differs from the title (e.g. "Check out Nonstop Eurotrip!").
    Times are used only when there are as many as cards, so a card never
    gets another card's time."""
    contents = []
    for item in structured_description_items(data):
        section = item.get("videoDescriptionInfocardsSectionRenderer")
        if section:
            contents += [(c.get("compactInfocardRenderer") or {}).get("content") or {}
                         for c in section.get("infocards") or []]
    cues = card_cues(data)
    if len(cues) != len(contents):
        cues = [{}] * len(contents)
    cards = []
    for content, cue in zip(contents, cues):
        card = parse_card(content)
        if card:
            message = cue.get("message") or ""
            card["start"] = cue.get("start")
            card["message"] = message if message.casefold() != card["title"].casefold() else ""
            cards.append(card)
    # In the order of the video, like chapters; cards without a time last.
    return sorted(cards, key=lambda c: (c["start"] is None, c["start"] or 0))


def extra_image_urls(info: dict) -> set[str]:
    """Images used by the description extras (people photos, podcast covers)
    and by the info cards."""
    urls = set()
    for extra in info.get(EXTRAS_KEY) or []:
        for entry in (extra.get("people") or []) + (extra.get("items") or []):
            if entry.get("image"):
                urls.add(entry["image"])
    for card in info.get(CARDS_KEY) or []:
        if card.get("image"):
            urls.add(card["image"])
    return urls


def marker_lists_of(info: dict) -> list[dict]:
    """Timestamp lists to show; falls back to yt-dlp's `chapters`.

    The fallback has no source note: yt-dlp may even return a Walkthroughs
    list as chapters.
    """
    lists = info.get(MARKERS_KEY)
    if lists is not None:
        return lists
    chapters = info.get("chapters") or []
    items = [{"start_time": int(c.get("start_time") or 0),
              "time_text": format_duration(c.get("start_time") or 0),
              "title": c.get("title") or ""} for c in chapters]
    return ([{"id": "chapters", "title": "Chapters", "source": None, "items": items}]
            if items else [])


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------

def base_language(code: str | None) -> str:
    """'en-US' -> 'en'; also strips yt-dlp's '-orig' suffix."""
    return (code or "").removesuffix("-orig").split("-")[0].lower()


def original_audio_language(info: dict) -> str | None:
    """Original audio language as yt-dlp knows it (formats marked "original")."""
    for fmt in info.get("formats") or []:
        if fmt.get("language_preference") == 10 and fmt.get("language"):
            return fmt["language"]
    return info.get("language")


def transcript_language(info: dict) -> str | None:
    """The video's original language for the transcript: the watch page's
    default track, else yt-dlp's original audio, else a single "-orig" track."""
    auto = [lang for lang, fmts in (info.get("automatic_captions") or {}).items() if fmts]
    origs = [lang for lang in auto if lang.endswith("-orig")]
    return ((info.get(CAPTION_KEY) or {}).get("language") or original_audio_language(info)
            or (origs[0].removesuffix("-orig") if len(origs) == 1 else None))


def choose_caption_track(info: dict) -> tuple[str, str, list[dict]] | None:
    """Pick the track YouTube's transcript panel shows by default.

    Preferred: the track the watch page names as default for the original
    audio (see default_caption_track). Otherwise: author subtitles in the
    original audio language, then automatic captions of that language, then
    any author subtitles. Without page data or yt-dlp's audio languages, a
    single "-orig" track gives the language. Auto-dubbed videos have
    automatic captions for every dubbed language, each marked "-orig" by
    yt-dlp, so the language must come from the original audio, not from the
    "-orig" mark.
    Returns (kind, language code, formats) or None.
    """
    manual = {lang: fmts for lang, fmts in (info.get("subtitles") or {}).items()
              if lang != "live_chat" and fmts}
    auto = {lang: fmts for lang, fmts in (info.get("automatic_captions") or {}).items() if fmts}

    def find_manual(language):
        exact = [lang for lang in manual if lang.lower() == language.lower()]
        same = [lang for lang in manual if base_language(lang) == base_language(language)]
        lang = (exact or same or [None])[0]
        return ("manual", lang, manual[lang]) if lang else None

    def find_auto(language):
        for lang in (f"{language}-orig", f"{base_language(language)}-orig",
                     language, base_language(language)):
            if auto.get(lang):
                return ("auto", lang.removesuffix("-orig"), auto[lang])
        return None

    default = info.get(CAPTION_KEY) or {}
    language = transcript_language(info)
    if language:
        if default.get("kind") == "asr":
            order = (find_auto, find_manual)
        else:
            order = (find_manual, find_auto)
        for find in order:
            found = find(language)
            if found:
                return found
    # Last resort (original language unknown): any author subtitles.
    if manual:
        lang = next(iter(manual))
        return "manual", lang, manual[lang]
    return None


def parse_json3_captions(data: dict) -> list[dict]:
    """Caption cues from YouTube's json3 format: [{start (s), text}].

    Line breaks inside a cue become spaces; events that hold only a line
    break (used by automatic captions) are skipped.
    """
    cues = []
    for event in data.get("events") or []:
        segs = event.get("segs")
        if not segs:
            continue
        text = " ".join("".join(seg.get("utf8", "") for seg in segs).split())
        if text:
            cues.append({"start": round((event.get("tStartMs") or 0) / 1000, 3),
                         "text": text})
    return cues


class TranscriptError(Exception):
    """The requested transcript (translation) cannot be made; nothing is saved."""


def download_cues(ydl: yt_dlp.YoutubeDL, url: str) -> list[dict]:
    with ydl.urlopen(url) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return parse_json3_captions(json.loads(raw)) if raw.strip() else []


def with_query(url: str, **params: str) -> str:
    """The URL with query parameters set (replacing existing ones)."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def wait(seconds: float, reason: str) -> None:
    if seconds > 0:
        log(f"  Waiting {seconds:.0f} s ({reason})...")
        time.sleep(seconds)


def is_too_many_requests(error: Exception) -> bool:
    return getattr(error, "status", None) == 429 or "429" in str(error)


def fetch_translation(ydl: yt_dlp.YoutubeDL, info: dict, transcript: dict,
                      url: str, target: str, not_before: float = 0.0) -> dict:
    """YouTube's auto-translation of the transcript track into `target`.

    It is the same track with `tlang=<target>`, so the translated captions
    keep the original timing. YouTube refuses such requests (HTTP 429) for
    about a minute after the video data was fetched, so this waits until
    `not_before` (a time.monotonic() value) and retries after 429.
    Raises TranscriptError if the translation cannot be had.
    """
    languages = info.get(TRANSLATIONS_KEY)
    if base_language(target) == base_language(transcript["language"]):
        raise TranscriptError(f"'{target}' is the language of the transcript itself "
                              f"({transcript['name']}); choose another language.")
    if languages is not None and target not in languages:
        available = ", ".join(sorted(languages)) or "none"
        raise TranscriptError(f"YouTube offers no translation of this transcript into "
                              f"'{target}'. Available: {available}.")
    name = (languages or {}).get(target, target)
    log(f"Downloading transcript translation ({name}, auto-translated)...")
    wait(not_before - time.monotonic(), "YouTube refuses translations requested too soon")
    for attempt, pause in enumerate((0, *TRANSLATION_RETRY_WAITS)):
        wait(pause, f"HTTP 429 from YouTube, retry {attempt} of {len(TRANSLATION_RETRY_WAITS)}")
        try:
            cues = download_cues(ydl, with_query(url, tlang=target))
            break
        except Exception as error:
            if is_too_many_requests(error) and attempt < len(TRANSLATION_RETRY_WAITS):
                continue
            hint = (" YouTube keeps refusing the translation; try again later, or with "
                    "--cookies-from-browser firefox after opening the video with "
                    "auto-translated subtitles in that browser."
                    if is_too_many_requests(error) else "")
            raise TranscriptError(f"could not download the translation into '{target}' "
                                  f"({error}).{hint}")
    if not cues:
        raise TranscriptError(f"YouTube returned no translation into '{target}'.")
    return {"language": target, "name": name, "cues": cues}


def bilingual_languages(spec: list[tuple[str, str | None]]) -> list[str]:
    """Target languages of the bilingual versions in a --transcript spec."""
    return [language for version, language in spec if version == "bilingual"]


def translations_of(transcript: dict) -> dict[str, dict]:
    """{language: translation} of a transcript (empty if it has none)."""
    return transcript.get("translations") or {}


def fetch_transcript(ydl: yt_dlp.YoutubeDL, info: dict,
                     translate_to: list[str] | None = None,
                     not_before: float = 0.0) -> dict | None:
    """Download the transcript as caption cues (and its translations if asked).

    YouTube's own transcript panel (get_transcript API) requires a token that
    only a real browser can produce, so the same text is taken from the
    caption track the panel shows, through the URLs yt-dlp has extracted.
    With `translate_to` (a list of language codes), any failure raises
    TranscriptError instead of leaving the transcript out.
    """
    def unavailable(reason: str) -> None:
        # A requested translation needs the transcript: stop; otherwise the
        # page is simply made without a transcript (the reason is kept for
        # "About this copy").
        info[TRANSCRIPT_MISSING_KEY] = reason
        if translate_to:
            raise TranscriptError(f"{reason}; nothing was saved.")
        log(f"No transcript: {reason}.")

    track = choose_caption_track(info)
    if not track:
        return unavailable("the video has no captions")
    kind, lang, formats = track
    fmt = next((f for f in formats if f.get("ext") == "json3" and f.get("url")), None)
    name = re.sub(r"\s*\((Original|auto-generated)\)$", "", (formats[0].get("name") or lang))
    if not fmt:
        return unavailable(f"no usable captions for {name}")
    label = f"{name}, auto-generated" if kind == "auto" else name
    log(f"Downloading transcript ({label})...")
    try:
        cues = download_cues(ydl, fmt["url"])
    except Exception as error:
        return unavailable(f"could not download it ({error})")
    if not cues:
        return unavailable("YouTube returned it empty")
    original = transcript_language(info)
    transcript = {"language": lang, "name": name, "kind": kind, "cues": cues,
                  "original": bool(original) and base_language(original) == base_language(lang)}
    if translate_to:
        transcript["translations"] = {
            target: fetch_translation(ydl, info, transcript, fmt["url"], target, not_before)
            for target in translate_to}
    return transcript


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def fetch_image(url: str) -> str | None:
    """Download an image and return it as a data URI, or None on failure."""
    if not url:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for _ in range(IMAGE_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=IMAGE_TIMEOUT) as response:
                data = response.read()
                mime = response.headers.get_content_type()
            if not mime.startswith("image/"):
                mime = sniff_image_type(data)
            if not data or not mime:
                return None
            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:  # network errors, HTTP errors, timeouts
            continue
    return None


def sniff_image_type(data: bytes) -> str | None:
    """Guess an image MIME type from its first bytes."""
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return None


def resize_avatar_url(url: str) -> str:
    """Ask YouTube's image server for a small avatar (=s88-... -> =s80-...)."""
    return re.sub(r"=s\d+(?=-|$)", f"=s{AVATAR_SIZE}", url, count=1)


def fetch_thumbnail(info: dict) -> str | None:
    """Embed the best available thumbnail, falling back to smaller ones."""
    candidates = [info.get("thumbnail")]
    thumbs = sorted(
        info.get("thumbnails") or [],
        key=lambda t: (t.get("preference") or 0, t.get("width") or 0),
        reverse=True,
    )
    candidates += [t.get("url") for t in thumbs]
    seen = set()
    for url in candidates:
        if url and url not in seen:
            seen.add(url)
            data_uri = fetch_image(url)
            if data_uri:
                return data_uri
    return None


def fetch_images(urls: set, what: str, resize=None) -> dict[str, str]:
    """Download images in parallel; returns {original url: data URI}.

    `resize` may turn a URL into one for a smaller image (avatars). Images
    that cannot be downloaded are left out; the page shows a placeholder.
    """
    urls = sorted(u for u in urls if u)
    if not urls:
        return {}
    log(f"Downloading {len(urls)} {what}...")
    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
        results = pool.map(lambda u: fetch_image(resize(u) if resize else u), urls)
        images = {u: d for u, d in zip(urls, results) if d}
    if len(images) < len(urls):
        log(f"  {len(urls) - len(images)} could not be downloaded (placeholders used).")
    return images


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def log(message: str) -> None:
    print(f"[yt2html] {message}", flush=True)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def format_int(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", THOUSANDS_SEPARATOR)


def format_compact(value) -> str:
    """YouTube-style short number: 987, 1.23K, 45.6K, 1.2M (truncated)."""
    if value is None:
        return "—"
    n = int(value)
    for unit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= unit:
            v = n / unit
            decimals = 2 if v < 10 else 1 if v < 100 else 0
            factor = 10 ** decimals
            text = f"{int(v * factor) / factor:.{decimals}f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text + suffix
    return str(n)


def format_duration(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_timestamp(ts) -> str | None:
    """Unix timestamp -> DD.MM.YYYY in the computer's local time zone."""
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts).strftime(DATE_FORMAT)


def published_date(info: dict) -> str:
    """Publication date as YouTube shows it (premiere/stream start if any)."""
    for key in ("release_timestamp", "timestamp"):
        text = format_timestamp(info.get(key))
        if text:
            return text
    for key in ("release_date", "upload_date"):
        raw = info.get(key)
        if raw and len(raw) == 8:
            return f"{raw[6:8]}.{raw[4:6]}.{raw[0:4]}"
    return "—"


def avatar_hue(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) % 360


def initial_of(name: str) -> str:
    name = (name or "?").lstrip("@")
    return (name[:1] or "?").upper()


# --------------------------------------------------------------------------
# Turning plain text into HTML with clickable links
# --------------------------------------------------------------------------

TOKEN_RE = re.compile(
    r"""
    (?P<url>\bhttps?://[^\s<>"]+|\bwww\.[^\s<>"]+\.[^\s<>"]+)
  | (?P<time>(?<![\w:.])(?:\d{1,2}:)?\d{1,2}:\d{2}(?![\w:]))
  | (?P<tag>(?<![\w&#/])\#\w+)
  | (?P<mention>(?<![\w.@/])@[\w.\-]{3,30})
    """,
    re.VERBOSE,
)

TRAILING_PUNCTUATION = ".,;:!?'…"


def trim_url(url: str) -> tuple[str, str]:
    """Split trailing punctuation (and unbalanced brackets) off a URL."""
    tail = ""
    while url:
        last = url[-1]
        if last in TRAILING_PUNCTUATION:
            pass
        elif last == ")" and url.count("(") < url.count(")"):
            pass
        elif last == "]" and url.count("[") < url.count("]"):
            pass
        else:
            break
        tail = last + tail
        url = url[:-1]
    return url, tail


def link(href: str, text: str) -> str:
    """A link opening in a new tab; `text` is escaped."""
    return link_html(href, esc(text))


def link_html(href: str, inner_html: str) -> str:
    """Like link(), but around HTML that is already escaped."""
    return (f'<a href="{esc(href)}" target="_blank" '
            f'rel="noopener noreferrer">{inner_html}</a>')


def time_link(video_id: str, start: float, text: str | None = None) -> str:
    """A timestamp linking to the video at that second (plain text without an id).

    `class="t"` and `data-t` (seconds) let the player script play the local
    copy from there instead of opening YouTube while the player is open."""
    seconds = int(start)
    text = text or format_duration(seconds)
    if not video_id:
        return esc(text)
    return (f'<a class="t" data-t="{seconds}" href="{esc(f"{YOUTUBE}/watch?v={video_id}&t={seconds}s")}" '
            f'target="_blank" rel="noopener noreferrer">{esc(text)}</a>')


def note_html(note: str | None) -> str:
    """The grey note after a section heading (information added by yt2html)."""
    return f' <span class="source">{esc(note)}</span>' if note else ""


def linkify(text: str, video_id: str | None, duration) -> str:
    """Escape text and turn URLs, timestamps, #hashtags and @handles into links."""
    out = []
    pos = 0
    for m in TOKEN_RE.finditer(text or ""):
        out.append(esc(text[pos:m.start()]))
        pos = m.end()
        token = m.group(0)
        kind = m.lastgroup
        if kind == "url":
            url, tail = trim_url(token)
            href = url if url.startswith("http") else "https://" + url
            out.append(link(href, url) + esc(tail))
        elif kind == "time":
            parts = [int(p) for p in token.split(":")]
            seconds = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
            valid = parts[-1] < 60 and (len(parts) < 3 or parts[-2] < 60)
            if video_id and valid and (duration is None or seconds <= duration):
                out.append(time_link(video_id, seconds, token))
            else:
                out.append(esc(token))
        elif kind == "tag":
            name = token[1:]
            if name.isdigit() or name.startswith("_"):
                out.append(esc(token))
            else:
                out.append(link(f"{YOUTUBE}/hashtag/{name.lower()}", token))
        elif kind == "mention":
            handle = token.rstrip(".-")
            out.append(link(f"{YOUTUBE}/{handle}", handle) + esc(token[len(handle):]))
    out.append(esc((text or "")[pos:]))
    return "".join(out)


# --------------------------------------------------------------------------
# Comment tree
# --------------------------------------------------------------------------

def chronological(items: list[dict]) -> list[dict]:
    """Stable sort by (estimated) timestamp, keeping the given order on ties.

    YouTube only provides relative times ("2 years ago"), so timestamps are
    coarse. The input order is already correct in most cases; the sort only
    moves items whose coarse time clearly disagrees (e.g. a pinned comment).
    Missing timestamps inherit the previous item's value.
    """
    keyed = []
    last = float("-inf")
    for item in items:
        ts = item.get("timestamp")
        if ts is not None:
            last = ts
        keyed.append((last, item))
    keyed.sort(key=lambda pair: pair[0])
    return [item for _, item in keyed]


def build_comment_tree(comments: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Return (top-level comments oldest first, {parent_id: replies oldest first})."""
    ids = {c.get("id") for c in comments}
    roots, children = [], defaultdict(list)
    for c in comments:
        parent = c.get("parent") or "root"
        if parent == "root" or parent not in ids:
            roots.append(c)
        else:
            children[parent].append(c)
    # Top level arrives newest first (comment_sort=new): reverse it.
    roots = chronological(list(reversed(roots)))
    # Replies arrive in YouTube's thread order, which is oldest first.
    children = {pid: chronological(items) for pid, items in children.items()}
    return roots, children


# --------------------------------------------------------------------------
# HTML rendering
# --------------------------------------------------------------------------

CSS = """
:root{
  --bg:#0f0f0f; --text:#f1f1f1; --muted:#aaaaaa; --panel:#272727;
  --line:#3f3f3f; --link:#3ea6ff; --chip:#3a3a3a; --media-bg:#000;
  --accent:#0e8f85; --on-accent:#fff; --accent-text:#3cc8b4;
  --accent-soft:rgba(14,143,133,.28); --hover:brightness(1.15);
  --planned:#9cc3e8;
  --player-w:clamp(320px,40vw,720px);
  color-scheme:dark;
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:Roboto,"Segoe UI",Arial,sans-serif;font-size:14px;line-height:20px}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
.page{max-width:1280px;margin:0 auto;padding:24px}
.thumbnail{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;
  border-radius:12px;background:var(--media-bg)}
.thumbnail.missing{display:flex;align-items:center;justify-content:center;color:var(--muted)}
h1.title{font-size:20px;line-height:28px;font-weight:700;margin:12px 0 0;
  overflow-wrap:anywhere}
h1.title a{color:var(--text)}
.channel{display:flex;align-items:center;gap:12px;margin-top:12px}
.channel .name{font-size:16px;font-weight:700;line-height:22px}
.channel .name a{color:var(--text)}
.channel .subs{color:var(--muted);font-size:12px;line-height:18px}
.badge{color:var(--muted);font-size:12px;margin-left:4px}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.fact{background:var(--panel);border-radius:12px;padding:10px 16px;min-width:120px}
.fact .value{font-size:16px;font-weight:700;line-height:22px;white-space:nowrap}
.fact .label{color:var(--muted);font-size:12px;line-height:18px}
.fact.extra .value{color:var(--muted)}
/* Panels below the facts share one look */
.description,.desc-extra,.markers,.transcript{background:var(--panel);border-radius:12px;
  padding:12px;margin-top:12px}
.desc-extra h2,.markers h2,.transcript h2{font-size:16px;line-height:22px;font-weight:700;
  margin:0 0 8px}
h2 .source{color:var(--muted);font-size:12px;font-weight:400;margin-left:6px}
.desc-extra p{margin:0 0 8px;white-space:pre-wrap;overflow-wrap:anywhere}
.desc-extra p:last-child{margin-bottom:0}
.desc-extra .subtitle{color:var(--muted);font-size:12px;line-height:18px;margin-top:-6px}
.desc-extra .info{color:var(--muted);font-size:12px;line-height:18px;margin-top:2px}
.people-list{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
.person{width:150px}
.person .photo{display:flex;width:150px;height:150px;object-fit:cover;border-radius:12px;
  background:var(--chip)}
.person .name{font-weight:600;margin-top:6px;overflow-wrap:anywhere}
.person .about{color:var(--muted);font-size:12px;line-height:18px;overflow-wrap:anywhere}
.img-placeholder{align-items:center;justify-content:center;
  color:var(--muted);font-size:40px;font-weight:500;user-select:none}
.playlist{display:flex;gap:16px;align-items:flex-start;margin-top:4px}
.playlist + .playlist{margin-top:12px}
.playlist .cover{display:flex;width:120px;height:120px;flex:none;object-fit:cover;
  border-radius:8px;background:var(--chip)}
.playlist .name{font-size:14px;line-height:20px;font-weight:700;overflow-wrap:anywhere}
.playlist .name a{color:var(--text)}
.playlist .meta{color:var(--muted);font-size:12px;line-height:18px}
/* Info cards: 160x90 images (the area of a 120x120 podcast cover), channel round */
.cards ol{list-style:none;margin:0;padding:0}
.cards li{display:flex;gap:12px;align-items:flex-start;padding:6px 0}
.cards .time{flex:none;min-width:56px;font-variant-numeric:tabular-nums;font-weight:600}
.cards .card-image{display:flex;flex:none;width:160px;height:90px;object-fit:cover;
  border-radius:8px;background:var(--chip)}
.cards .card-image.round{width:90px;height:90px;margin:0 35px;border-radius:50%}
.cards .details{min-width:0}
.cards .name{font-weight:700;overflow-wrap:anywhere}
.cards .name a{color:var(--text)}
.cards .meta{color:var(--muted);font-size:12px;line-height:18px;overflow-wrap:anywhere}
.markers ol{list-style:none;margin:0;padding:0}
.markers li{display:flex;gap:12px;padding:3px 0}
.markers .time{flex:none;min-width:56px;font-variant-numeric:tabular-nums;font-weight:600}
.markers .label{overflow-wrap:anywhere}
.transcript h3{font-size:14px;line-height:20px;font-weight:700;margin:16px 0 4px}
.transcript h2 + h3{margin-top:4px}
.transcript p{margin:0;overflow-wrap:anywhere}
.transcript .cue{display:flex;gap:12px;padding:1px 0}
.transcript .cue .time{flex:none;min-width:56px;font-variant-numeric:tabular-nums;font-weight:600}
.transcript .cue .text{overflow-wrap:anywhere}
.transcript .bi-row{display:grid;grid-template-columns:56px 1fr 1fr;column-gap:12px;
  padding:2px 0}
.transcript .bi-row .time{font-variant-numeric:tabular-nums;font-weight:600}
.transcript .bi-row .text{overflow-wrap:anywhere}
.transcript .bi-row .text + .text{border-left:1px solid var(--line);padding-left:12px}
.transcript .bi-head{color:var(--muted);font-size:12px;line-height:18px;padding-bottom:4px}
/* Speaker change (">>" in YouTube's captions): grey mark */
.transcript .speaker{color:var(--muted)}
/* a speech bubble outline, drawn in the grey text colour through a mask */
.transcript .cue .speaker::before,.transcript .bi-row .speaker::before{content:"";
  display:inline-block;width:.95em;height:.95em;vertical-align:-.12em;background:currentColor;
  -webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6a1.5 1.5 0 0 1-1.5 1.5H7l-3 3v-3h-.5A1.5 1.5 0 0 1 2 9.5z' fill='none' stroke='black' stroke-width='1.4' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
  mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6a1.5 1.5 0 0 1-1.5 1.5H7l-3 3v-3h-.5A1.5 1.5 0 0 1 2 9.5z' fill='none' stroke='black' stroke-width='1.4' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat}
.description{white-space:pre-wrap;overflow-wrap:anywhere}
.description.empty{color:var(--muted)}
.comments{margin-top:24px}
.comments h2{font-size:20px;line-height:28px;font-weight:700;margin:0}
.comments .note{color:var(--muted);font-size:12px;margin:4px 0 16px}
.comment{display:flex;gap:16px;margin-top:16px}
.comment .body{min-width:0;flex:1}
.comment .head{font-size:13px;line-height:18px}
.comment .author{font-weight:600}
.comment .author a{color:var(--text)}
.comment .author.owner{background:var(--chip);border-radius:12px;padding:1px 6px}
.comment .time{color:var(--muted);margin-left:4px}
.comment .pinned{color:var(--muted);font-size:12px;line-height:18px}
.comment .text{margin-top:2px;white-space:pre-wrap;overflow-wrap:anywhere}
.comment .foot{color:var(--muted);font-size:12px;line-height:18px;margin-top:4px}
.comment .heart{color:#ff4e45}
.avatar{flex:none;width:40px;height:40px;border-radius:50%;
  background-color:hsl(var(--hue,0) 45% 40%);background-size:cover;background-position:center;
  display:flex;align-items:center;justify-content:center;
  font-size:18px;font-weight:500;color:#fff;user-select:none}
.replies{margin-left:56px;padding-left:12px;border-left:2px solid var(--line)}
.replies .avatar{width:24px;height:24px;font-size:12px}
.replies .comment{gap:12px;margin-top:12px}
.replies .replies{margin-left:36px}
/* Video formats and About this copy: folded at the bottom; open, only the
   title stays in the summary */
.formats-info,.copy-info{margin-top:32px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px;line-height:18px}
.formats-info+.copy-info{margin-top:12px}
.formats-info summary,.copy-info summary{cursor:pointer;width:max-content;max-width:100%}
.formats-info[open] summary .rest,.copy-info[open] summary .rest{display:none}
.copy-info dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:10px 0 0 14px}
.copy-info dt{font-weight:600}
.copy-info dd{margin:0;overflow-wrap:anywhere}
/* Video formats: tables, scrolled sideways on narrow screens; the planned default marked */
.formats-info .formats{overflow-x:auto;max-width:100%;margin:10px 0 0 14px}
.formats-info .formats table{border-collapse:collapse}
.formats-info .formats caption{caption-side:top;text-align:left;font-weight:600;padding-bottom:2px}
.formats-info .formats th,.formats-info .formats td{border:1px solid var(--line);padding:1px 8px;
  text-align:left;vertical-align:top;font-weight:400}
.formats-info .formats th{font-weight:600;white-space:nowrap}
.formats-info .formats .size{text-align:right;white-space:nowrap}
.formats-info .formats .planned{color:var(--planned)}
.formats-info .translation,.formats-info .none{margin:4px 0 0 14px}
.copy-info a{color:var(--muted);text-decoration:underline;text-underline-offset:2px}
.copy-info .command{font-family:ui-monospace,Consolas,"Cascadia Mono",monospace;font-size:12px;
  background:var(--chip);border-radius:4px;padding:1px 6px;
  -webkit-box-decoration-break:clone;box-decoration-break:clone}
@media (max-width:600px){
  .copy-info dl{grid-template-columns:1fr;gap:0;margin-left:0}
  .copy-info dd{margin-bottom:8px}
}
.footer{color:var(--muted);font-size:12px;margin-top:32px;padding-top:12px;
  border-top:1px solid var(--line)}
/* Index of a playlist or a list file: a table without visible lines */
.page.index{max-width:960px}
.index-meta{color:var(--muted);font-size:12px;line-height:18px;margin-top:4px}
.index-section{margin-top:20px}
.index-section h2{font-size:16px;line-height:22px;font-weight:700;margin:0 0 4px;
  overflow-wrap:anywhere}
.index-section h2 a{color:var(--text)}
.index-table{border-collapse:collapse;width:100%}
.index-table td{padding:6px 16px 6px 0;vertical-align:top}
.index-table td:last-child{padding-right:0}
.index-table .pos{width:1%;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.index-table .name{overflow-wrap:anywhere}
.index-table .name a{color:var(--text);font-weight:600}
.index-table .sub{color:var(--muted);font-size:12px;line-height:18px}
.index-table .date{width:1%;white-space:nowrap;font-variant-numeric:tabular-nums}
.index-table .yt{width:1%;white-space:nowrap}
.index-table .missing .name,.index-table .reason{color:var(--muted)}
.index-table .reason[title]{cursor:help}
/* Local video player (shown by the script only when a video file is next to
   the page): turquoise Player tile, player pinned on the left, page on the right */
.fact.play{display:none;border:0;cursor:pointer;text-align:left;font:inherit;
  background:var(--accent);color:var(--on-accent)}
.has-video .fact.play{display:block}
.fact.play .value,.fact.play .label{color:var(--on-accent)}
.fact.play .label{opacity:.85;width:0;min-width:100%;overflow:hidden;white-space:nowrap}
.fact.play:hover{filter:var(--hover)}
.fact.play:focus-visible{outline:2px solid var(--text);outline-offset:2px}
.player{display:none}
.player-open .player{display:flex;flex-direction:column;gap:12px;position:fixed;top:0;left:0;
  width:var(--player-w);height:100vh;padding:24px;background:var(--bg);
  border-right:1px solid var(--line);overflow-y:auto;z-index:10}
.player video{display:block;width:100%;aspect-ratio:16/9;background:var(--media-bg);
  border-radius:12px}
.player .seek{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:12px;
  font-variant-numeric:tabular-nums}
.player .seek input{flex:1;accent-color:var(--accent)}
.player .buttons{display:flex;flex-wrap:wrap;gap:8px}
.player button{width:60px;padding:7px 0;border:0;border-radius:18px;background:var(--chip);
  color:var(--text);font:inherit;font-weight:600;cursor:pointer}
.player button:hover{filter:var(--hover)}
/* the repeat arrow is a small glyph: larger, the button keeps its height */
.player button[data-do=repeat]{font-size:19px}
.player button.main{width:72px;background:var(--accent);color:var(--on-accent)}
.player .speed button{width:52px;padding:4px 0;font-size:12px}
.player .speed button[aria-pressed=true]{background:var(--accent);color:var(--on-accent)}
/* Follow row: as high as the speeds, both buttons as wide as "Stop following" */
.player .buttons.follow button{width:116px;padding:4px 0;font-size:12px}
.player .buttons.follow .langs{display:contents}
.player .buttons.follow button.lang{width:40px}
.player button:disabled{opacity:.45;cursor:default;filter:none}
.player .buttons.follow button[aria-pressed=true]{background:var(--accent);color:var(--on-accent)}
.player .now-playing{margin-top:4px;cursor:pointer}
/* the chapter in a softened accent colour, one line (cut with an ellipsis);
   its line stays reserved while chapters are off, so the sentences never move */
.player .now-playing .chapter{color:var(--accent-text);opacity:.75;font-size:12px;line-height:18px;
  font-weight:600;margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.player .now-playing .chapter.off{visibility:hidden}
.player .now-playing .line{color:var(--muted);font-size:18px;line-height:26px;overflow-wrap:anywhere}
.player .now-playing .current{color:var(--text);font-weight:600}
/* the translation: grey with a faint touch of the accent (weaker than the chapter),
   so the eye tells it from the original lines; older browsers keep the grey */
.player .now-playing .translation{font-weight:600;
  color:color-mix(in srgb,var(--accent-text) 30%,var(--muted))}
.player .now-playing .line:empty{display:none}
.player .now-playing .chapter:empty{display:none}
/* Subtitles: the mode as small dots, hollow (sentence only) or filled (context lines) */
.player .dots{display:inline-flex;gap:3px;margin-left:5px;vertical-align:1px}
.player .dots i{width:6px;height:6px;border-radius:50%;border:1.5px solid currentColor;
  box-sizing:border-box}
.player .dots i.full{background:currentColor}
/* While the player is open: timestamps play the local copy, current places marked */
.player-open a.t{color:var(--accent-text)}
.player-open .transcript p span[data-c]{cursor:pointer}
.player-open #chapters li.now,.transcript .cue.now,.transcript .bi-row.now,
.transcript .cue.found,.transcript .bi-row.found{margin:0 -6px;padding-left:6px;padding-right:6px;
  border-radius:6px;background:var(--accent-soft)}
.transcript p span.now,.transcript p span.found{border-radius:4px;background:var(--accent-soft)}
.player-open .page{max-width:none;margin:0 0 0 var(--player-w)}
.player-open .page > .thumbnail{display:none}
.player-open.pip .player{display:none}
.player-open.pip .page{max-width:1280px;margin:0 auto}
.player-open.pip .page > .thumbnail{display:block}
@media (max-width:900px){
  .player-open .player{position:sticky;width:auto;height:auto;padding:12px;border-right:0;
    border-bottom:1px solid var(--line)}
  .player-open .page{margin:0}
}
/* Phones */
@media (max-width:600px){
  .page{padding:12px}
  .replies{margin-left:20px}
  .replies .replies{margin-left:12px}
  .transcript .bi-row{grid-template-columns:48px 1fr}
  .cards .card-image{width:112px;height:63px}
  .cards .card-image.round{width:63px;height:63px;margin:0 24.5px}
  .transcript .bi-row .text + .text{grid-column:2;border-left:0;padding-left:0;color:var(--muted)}
}
"""


# Light colours, as on YouTube's light theme; the dark ones are in CSS above.
LIGHT_VARIABLES = """
  --bg:#ffffff; --text:#0f0f0f; --muted:#606060; --panel:#f2f2f2;
  --line:#e5e5e5; --link:#065fd4; --chip:#e5e5e5; --media-bg:#e5e5e5;
  --accent:#0f766e; --accent-text:#0f766e; --accent-soft:rgba(15,118,110,.16);
  --hover:brightness(.92); --planned:#2f6a9e;
  color-scheme:light;
"""

THEMES = ("dark", "light", "auto")
COLOR_SCHEMES = {"dark": "dark", "light": "light", "auto": "dark light"}


def theme_css(theme: str) -> str:
    """CSS that switches the dark base colours to the chosen theme.

    "auto" follows the device setting (prefers-color-scheme), like YouTube's
    "Use device theme"; the page then changes along with the system.
    """
    if theme == "light":
        return f":root{{{LIGHT_VARIABLES}}}"
    if theme == "auto":
        return f"@media (prefers-color-scheme: light){{:root{{{LIGHT_VARIABLES}}}}}"
    return ""


class AvatarStyles:
    """Stores each embedded avatar once, as a CSS class."""

    def __init__(self, data_uris: dict[str, str]):
        self.data_uris = data_uris
        self.classes: dict[str, str] = {}

    def html(self, url: str | None, name: str) -> str:
        data_uri = self.data_uris.get(url or "")
        if data_uri:
            cls = self.classes.setdefault(url, f"av{len(self.classes)}")
            return f'<span class="avatar {cls}" aria-hidden="true"></span>'
        return (f'<span class="avatar" style="--hue:{avatar_hue(name or "")}" '
                f'aria-hidden="true">{esc(initial_of(name))}</span>')

    def css(self) -> str:
        return "\n".join(
            f'.{cls}{{background-image:url("{self.data_uris[url]}")}}'
            for url, cls in self.classes.items()
        )


def render_comment(c: dict, children: dict, avatars: AvatarStyles,
                   video_id: str, duration, uploader: str) -> str:
    """One comment and, below it, its replies (recursively)."""
    author = c.get("author") or "(unknown)"
    author_html = esc(author)
    if c.get("author_url"):
        author_html = link(c["author_url"], author)
    owner = " owner" if c.get("author_is_uploader") else ""
    verified = ' <span class="badge" title="Verified">✓</span>' if c.get("author_is_verified") else ""

    time_text = c.get("_time_text") or ""
    approx = format_timestamp(c.get("timestamp"))
    time_title = f' title="≈ {approx}"' if approx else ""

    parts = [f'<article class="comment" id="comment-{esc(c.get("id", ""))}">',
             avatars.html(c.get("author_thumbnail"), author),
             '<div class="body">']
    if c.get("is_pinned"):
        parts.append(f'<div class="pinned">📌 Pinned by {esc(uploader)}</div>')
    parts.append(f'<div class="head"><span class="author{owner}">{author_html}</span>'
                 f'{verified}<span class="time"{time_title}>{esc(time_text)}</span></div>')
    parts.append(f'<div class="text">{linkify(c.get("text") or "", video_id, duration)}</div>')

    foot = []
    likes = c.get("like_count")
    if likes:
        foot.append(f'{format_compact(likes)} {"like" if likes == 1 else "likes"}')
    if c.get("is_favorited"):
        foot.append(f'<span class="heart">♥</span> by {esc(uploader)}')
    replies = children.get(c.get("id"), [])
    if replies:
        foot.append(f'{len(replies)} {"reply" if len(replies) == 1 else "replies"}')
    if foot:
        parts.append(f'<div class="foot">{" · ".join(foot)}</div>')
    parts.append("</div></article>")

    if replies:
        parts.append('<div class="replies">')
        for reply in replies:
            parts.append(render_comment(reply, children, avatars, video_id, duration, uploader))
        parts.append("</div>")
    return "\n".join(parts)


def fact(label: str, value: str, extra: bool = False) -> str:
    """One fact tile; extra=True marks information added by this program (grey)."""
    cls = "fact extra" if extra else "fact"
    return (f'<div class="{cls}"><div class="value">{esc(value)}</div>'
            f'<div class="label">{esc(label)}</div></div>')


# Grey note next to a chapter heading; only for a source the program verified.
SOURCE_NOTES = {"description": "from description", "auto": "auto-generated"}


def render_attributed(info: dict) -> str:
    """Attributed text as HTML, with its web links clickable."""
    text = info.get("text") or ""
    # YouTube counts positions in UTF-16 code units, like JavaScript.
    units = text.encode("utf-16-le")
    piece = lambda a, b: units[2 * a:2 * b].decode("utf-16-le", errors="replace")
    out, pos = [], 0
    for item in sorted(info.get("links") or [], key=lambda x: x["start"]):
        start, end = item["start"], item["start"] + item["length"]
        if start < pos:
            continue
        out.append(esc(piece(pos, start)))
        out.append(link(item["url"], piece(start, end)))
        pos = end
    out.append(esc(piece(pos, len(units) // 2)))
    return "".join(out)


def image_or_placeholder(url: str | None, images: dict[str, str], label: str, cls: str) -> str:
    """An embedded image, or a grey box with the first letter of `label`."""
    data_uri = images.get(url or "")
    if data_uri:
        return f'<img class="{cls}" src="{data_uri}" alt="">'
    return f'<div class="{cls} img-placeholder" aria-hidden="true">{esc(initial_of(label))}</div>'


def render_people(extra: dict, images: dict[str, str]) -> str:
    cards = []
    for person in extra.get("people") or []:
        # Not "description": that class styles the video description box.
        description = (f'<div class="about">{esc(person["description"])}</div>'
                       if person.get("description") else "")
        cards.append(f'<div class="person">'
                     f'{image_or_placeholder(person.get("image"), images, person["name"], "photo")}'
                     f'<div class="name">{esc(person["name"])}</div>{description}</div>')
    subtitle = f'<div class="subtitle">{esc(extra["subtitle"])}</div>' if extra.get("subtitle") else ""
    info = f'<div class="info">{render_attributed(extra["info"])}</div>' if extra.get("info") else ""
    return (f'<section class="desc-extra people"><h2>{esc(extra.get("title") or "")}</h2>'
            f'{subtitle}{info}<div class="people-list">{"".join(cards)}</div></section>')


def render_playlists(extra: dict, images: dict[str, str]) -> str:
    rows = []
    for item in extra.get("items") or []:
        cover = image_or_placeholder(item.get("image"), images, item.get("title") or "", "cover")
        title = esc(item.get("title") or "")
        if item.get("url"):
            cover = link_html(item["url"], cover)
            title = link_html(item["url"], title)
        lines = "".join(f'<div class="meta">{esc(item[key])}</div>'
                        for key in ("byline", "count") if item.get(key))
        rows.append(f'<div class="playlist">{cover}<div class="details">'
                    f'<div class="name">{title}</div>{lines}</div></div>')
    return (f'<section class="desc-extra playlists"><h2>{esc(extra.get("title") or "")}</h2>'
            f'{"".join(rows)}</section>')


CARD_LABELS = {"video": "Video", "playlist": "Playlist", "channel": "Channel", "link": "Link"}


def render_cards(cards: list[dict], video_id: str, images: dict[str, str]) -> str:
    """The "Info cards" section: time, image, title linking to the target and
    a grey line with the kind, details and the author's message."""
    rows = []
    for card in cards:
        start = card.get("start")
        time = time_link(video_id, start) if start is not None else ""
        cls = "card-image round" if card.get("type") == "channel" else "card-image"
        image = image_or_placeholder(card.get("image"), images, card.get("title") or "", cls)
        title = esc(card.get("title") or "")
        if card.get("url"):
            image = link_html(card["url"], image)
            title = link_html(card["url"], title)
        meta = " · ".join([CARD_LABELS.get(card.get("type"), "Card"), *(card.get("details") or [])])
        if card.get("message"):
            meta += f" · “{card['message']}”"
        rows.append(f'<li><span class="time">{time}</span>{image}<div class="details">'
                    f'<div class="name">{title}</div><div class="meta">{esc(meta)}</div></div></li>')
    return (f'<section class="desc-extra cards" id="info-cards"><h2>Info cards</h2>'
            f'<ol>{"".join(rows)}</ol></section>')


def render_description_extras(extras: list[dict], video_id: str, duration,
                              images: dict[str, str] | None = None) -> str:
    """Extra description elements: AI summary, People mentioned, podcast."""
    images = images or {}
    sections = []
    for extra in extras:
        kind = extra.get("type")
        if kind == "video_summary":
            paragraphs = "".join(f"<p>{linkify(p, video_id, duration)}</p>"
                                 for p in extra.get("paragraphs") or [])
            sections.append(f'<section class="desc-extra video-summary">'
                            f'<h2>{esc(extra.get("title") or "")}</h2>{paragraphs}</section>')
        elif kind == "people":
            sections.append(render_people(extra, images))
        elif kind == "playlists":
            sections.append(render_playlists(extra, images))
    return "\n".join(sections)


def render_marker_lists(lists: list[dict], video_id: str) -> str:
    """Timestamp lists (Walkthroughs, Chapters, ...) as plain vertical lists."""
    sections = []
    for marker_list in lists:
        rows = []
        for item in marker_list.get("items") or []:
            start = item.get("start_time")
            time_html = (time_link(video_id, start, item.get("time_text")) if start is not None
                         else esc(item.get("time_text") or ""))
            rows.append(f'<li><span class="time">{time_html}</span>'
                        f'<span class="label">{esc(item.get("title") or "")}</span></li>')
        list_id = re.sub(r"[^a-z0-9-]+", "-", (marker_list.get("id") or "markers").lower())
        note = note_html(SOURCE_NOTES.get(marker_list.get("source")))
        sections.append(f'<section class="markers" id="{esc(list_id)}">'
                        f'<h2>{esc(marker_list.get("title") or "Chapters")}{note}</h2>'
                        f'<ol>{"".join(rows)}</ol></section>')
    return "\n".join(sections)


def render_license(license_info: dict) -> str:
    text = "".join(link(r["url"], r["text"]) if r.get("url") else esc(r["text"])
                   for r in license_info["runs"])
    return (f'<section class="desc-extra license">'
            f'<h2>{esc(license_info.get("title") or "License")}</h2><p>{text}</p></section>')


def render_transcripts(transcript: dict, chapters: list[dict], video_id: str,
                       spec: list[tuple[str, str | None]]) -> list[str]:
    """One transcript section per version, in the order of the spec.

    When more than one plain/timestamped version is shown, the timestamped one
    gets "with timestamps" in its grey note so the two can be told apart.
    """
    several = sum(version != "bilingual" for version, _ in spec) > 1
    translations = translations_of(transcript)
    sections = []
    for version, language in spec:
        if version == "plain":
            sections.append(render_transcript(transcript, chapters, video_id, False))
        elif version == "timestamps":
            sections.append(render_transcript(transcript, chapters, video_id, True,
                                              note_suffix="with timestamps" if several else ""))
        elif version == "bilingual":
            sections.append(render_bilingual_transcript(transcript, translations[language],
                                                        chapters, video_id))
    return sections


# YouTube's automatic captions start a new speaker's words with ">>" (">>>" for
# a new topic), a convention of TV captions. The page shows its own grey mark.
SPEAKER_RE = re.compile(r"\s*>>>?\s*")
SPEAKER_MARK = '<span class="speaker" aria-hidden="true"></span>'
PLAIN_SPEAKER_MARK = '<span class="speaker">|</span>'


def speaker_html(text: str, mark: str = SPEAKER_MARK, at_start: bool = True) -> tuple[str, bool]:
    """(escaped text with each ">>" replaced by `mark`, whether there was one).
    A mark at the very start is left out when `at_start` is false."""
    parts = SPEAKER_RE.split(text or "")
    if len(parts) == 1:
        return esc(text or ""), False
    pieces = []
    for i, part in enumerate(parts):
        if i and (pieces or at_start):
            pieces.append(mark)
        if part:
            pieces.append(esc(part))
    return " ".join(pieces), True


def cue_start(cue: dict) -> str:
    """A cue's start for its data-c attribute: seconds with two decimals."""
    return f"{float(cue.get('start') or 0):.2f}".rstrip("0").rstrip(".")


def group_by_chapters(cues: list[dict], chapters: list[dict]) -> list[dict]:
    """[{title or None, cues}]: cues under the last chapter starting before them."""
    starts = [c.get("start_time") or 0 for c in chapters]
    groups = [{"title": None, "cues": []}] + [{"title": c.get("title") or "", "cues": []}
                                              for c in chapters]
    for cue in cues:
        index = 0
        for i, start in enumerate(starts):
            if cue["start"] >= start:
                index = i + 1
        groups[index]["cues"].append(cue)
    return [g for g in groups if g["cues"]]


def transcript_note(transcript: dict) -> str:
    note = transcript.get("name") or transcript.get("language") or ""
    if transcript.get("kind") == "auto":
        note = f"{note}, auto-generated" if note else "auto-generated"
    return note


def align_translation(cues: list[dict], translated: list[dict]) -> list[str]:
    """Translated text for each original cue, matched by start time.

    The translation keeps the original timing, so normally each translated
    cue starts with an original one; a translated cue goes to the last
    original cue that starts at or before it.
    """
    texts = [[] for _ in cues]
    starts = [c["start"] for c in cues]
    for cue in translated:
        index = 0
        for i, start in enumerate(starts):
            if start <= cue["start"] + 0.001:
                index = i
            else:
                break
        texts[index].append(cue["text"])
    return [" ".join(t) for t in texts]


def render_bilingual_transcript(transcript: dict, translation: dict,
                                chapters: list[dict], video_id: str) -> str:
    """Timestamped transcript with the translation in a second column."""
    cues = transcript.get("cues") or []
    translated = dict(zip(map(id, cues), align_translation(cues, translation.get("cues") or [])))
    original_label = transcript_note(transcript)
    target_label = f'{translation.get("name") or translation.get("language")}, auto-translated'
    parts = [f'<div class="bi-row bi-head"><span class="time"></span>'
             f'<span class="text">{esc(original_label)}</span>'
             f'<span class="text">{esc(target_label)}</span></div>']
    for group in group_by_chapters(cues, chapters):
        if group["title"] is not None:
            parts.append(f'<h3>{esc(group["title"])}</h3>')
        for cue in group["cues"]:
            original, speaker = speaker_html(cue["text"])
            translated_html, marked = speaker_html(translated[id(cue)])
            if speaker and not marked:  # the translation keeps the speaker change
                translated_html = f"{SPEAKER_MARK} {translated_html}"
            parts.append(f'<div class="bi-row" data-c="{cue_start(cue)}"><span class="time">'
                         f'{time_link(video_id, cue["start"])}</span>'
                         f'<span class="text">{original}</span>'
                         f'<span class="text">{translated_html}</span></div>')
    # The languages are named in the column headings, not again in the heading.
    language = translation.get("language") or ""
    return (f'<section class="transcript bilingual" data-lang="{esc(language)}"><h2>Transcript</h2>'
            f'{"".join(parts)}</section>')


def render_transcript(transcript: dict, chapters: list[dict], video_id: str,
                      timestamps: bool, note_suffix: str = "") -> str:
    """Transcript, split under chapter headings when the video has chapters.

    Without timestamps each part is one continuous text (cues joined with
    spaces, no added paragraphs); with timestamps every cue is its own line.
    """
    parts = []
    for group in group_by_chapters(transcript.get("cues") or [], chapters):
        if group["title"] is not None:
            parts.append(f"<h3>{esc(group['title'])}</h3>")
        if timestamps:
            rows = []
            for cue in group["cues"]:
                rows.append(f'<div class="cue" data-c="{cue_start(cue)}"><span class="time">'
                            f'{time_link(video_id, cue["start"])}</span>'
                            f'<span class="text">{speaker_html(cue["text"])[0]}</span></div>')
            parts.append(f'<div class="cues">{"".join(rows)}</div>')
        else:
            # One invisible span per cue, so the player can mark and seek it.
            # A speaker change is a grey " | " (none at the start of a part).
            parts.append("<p>" + " ".join(
                f'<span data-c="{cue_start(c)}">'
                f'{speaker_html(c["text"], PLAIN_SPEAKER_MARK, at_start=i > 0)[0]}</span>'
                for i, c in enumerate(group["cues"])) + "</p>")

    note = transcript_note(transcript)
    if note_suffix:
        note = f"{note}, {note_suffix}" if note else note_suffix
    css = "transcript with-timestamps" if timestamps else "transcript"
    return (f'<section class="{css}"><h2>Transcript{note_html(note)}</h2>'
            f'{"".join(parts)}</section>')


def comment_status(info: dict) -> str:
    """How the comment download ended, for the page's metadata and the index:
    complete, limited (--max-comments reached), interrupted (Ctrl+C),
    not-downloaded (--no-comments) or turned-off (by the author)."""
    if "comments" not in info:
        return "not-downloaded"
    if info.get("comments") is None:
        return "turned-off"
    if comments_interrupted(info):
        return "interrupted"
    downloaded = len(info["comments"])
    expected = youtube_comment_count(info)[1]
    limit = info.get(LIMIT_KEY)
    if limit is not None and downloaded >= limit and (expected is None or expected > downloaded):
        return "limited"
    return "complete"


def page_metadata(info: dict, archived: dt.date) -> dict[str, str]:
    """Data for the index, kept invisibly in the page's <head> as
    <meta name="yt2html:..."> (read back by read_page_metadata())."""
    count_text = youtube_comment_count(info)[0] or ""  # as on the Comments tile
    return {
        "yt2html:video-id": info.get("id") or "",
        "yt2html:title": info.get("title") or "",
        "yt2html:archived": archived.isoformat(),
        "yt2html:comments-status": comment_status(info),
        "yt2html:comments-downloaded": str(len(info.get("comments") or [])),
        "yt2html:comments-youtube": count_text,
    }


META_RE = re.compile(r'<meta name="(yt2html:[a-z-]+)" content="([^"]*)">')
META_READ_BYTES = 16384  # the metadata is at the top, before the large <style>


def read_page_metadata(path: Path) -> dict[str, str]:
    """The yt2html:... metadata of a page made by this program ({} if none)."""
    try:
        with open(path, "rb") as file:
            head = file.read(META_READ_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return {}
    return {name: html.unescape(value) for name, value in META_RE.findall(head)}


PLAYER_TILE = ('<button class="fact play" type="button"><div class="value">▶ Play</div>'
               '<div class="label">Player</div></button>')

PLAYER_SPEEDS = ("0.5", "0.75", "1", "1.25", "1.5")
VIDEO_EXTENSIONS = ("mp4", "webm", "m4v", "mkv")


def render_player(video_names: list[str], video_files: list[str] | None = None,
                  qualities: dict[str, str] | None = None, language: str | None = None) -> str:
    """The player panel; hidden until the script finds a video file next to
    the page: `video_files` first (files with the video's id found next to the
    page when it was built), then the page's own name and `video_names`, each
    with every video extension (for videos downloaded later). `qualities`
    ({"1920x1080": "1080p"}) and `language` (of the audio in the video) give
    the tile its label once the file's size is known."""
    speeds = "".join(f'<button type="button" data-speed="{v}" aria-pressed="{str(v == "1").lower()}">'
                     f'{v}×</button>' for v in PLAYER_SPEEDS)
    return (
        f'<aside class="player" aria-label="Local video player" '
        f'data-files="{esc(json.dumps(video_files or [], ensure_ascii=False))}" '
        f'data-names="{esc(json.dumps(video_names, ensure_ascii=False))}" '
        f'data-qualities="{esc(json.dumps(qualities or {}))}" data-language="{esc(language or "")}" '
        f'data-extensions="{" ".join(VIDEO_EXTENSIONS)}">'
        '<video preload="metadata"></video>'
        '<div class="seek"><span class="now">0:00</span>'
        '<input type="range" min="0" max="0" step="0.1" value="0" aria-label="Position">'
        '<span class="total">0:00</span></div>'
        '<div class="buttons">'
        '<button type="button" data-do="start" title="To the start (Ctrl+←)">|◀</button>'
        '<button type="button" data-do="back" title="10 s back (←)">−10 s</button>'
        '<button type="button" data-do="repeat" title="Previous sentence (Shift+←); '
        'click again for one more">↺</button>'
        '<button type="button" data-do="toggle" class="main" title="Play / pause (space)">▶</button>'
        '<button type="button" data-do="forward" title="10 s forward (→)">+10 s</button>'
        '<button type="button" data-do="end" title="To the end (Ctrl+→)">▶|</button>'
        '</div>'
        f'<div class="buttons speed">{speeds}</div>'
        '<div class="buttons follow">'
        '<button type="button" data-do="follow" aria-pressed="false" '
        'title="Mark the current sentence in the transcript and keep it in view; again: '
        'the next transcript, after the last one off">Follow</button>'
        '<button type="button" data-do="chapters" aria-pressed="false" '
        'title="Show the current chapter under the video">Chapters</button>'
        '<button type="button" data-do="subtitles" aria-pressed="false" '
        'title="Show the current sentence under the video; again: with one line before '
        'and after, with two, off">Subtitles<span class="dots"></span></button>'
        '<span class="langs"></span></div>'
        '<div class="now-playing" title="Show in the transcript"><div class="chapter"></div>'
        '<div class="line prev2"></div><div class="line prev"></div><div class="line current"></div>'
        '<div class="line translation"></div><div class="line next"></div>'
        '<div class="line next2"></div></div>'
        '</aside>')


# The only script of a page: it adds the player when a video file with the
# page's name (or the original yt-dlp name) lies next to it, makes timestamps
# play the local copy, shows the current chapter and sentences under the video
# and marks them on the page (Follow). Without the script, or without a video
# file, the page is exactly as before.
PLAYER_JS = r"""
(function () {
  var body = document.body, panel = document.querySelector('.player');
  if (!panel) return;
  var video = panel.querySelector('video'), tile = document.querySelector('.fact.play');
  var seek = panel.querySelector('.seek input'), toggle = panel.querySelector('[data-do=toggle]');
  var page = decodeURIComponent(location.pathname.split('/').pop()).replace(/\.html?$/i, '');
  var names = [page].concat(JSON.parse(panel.dataset.names || '[]'));
  var candidates = JSON.parse(panel.dataset.files || '[]').map(encodeURIComponent);
  names.forEach(function (name) {
    panel.dataset.extensions.split(' ').forEach(function (ext) {
      var file = encodeURIComponent(name + '.' + ext);
      if (candidates.indexOf(file) < 0) candidates.push(file);
    });
  });
  function clock(s) {
    s = Math.floor(s || 0);
    var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = String(s % 60);
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + sec.padStart(2, '0');
  }
  function probe(i) {  // the first candidate that loads becomes the video
    if (i >= candidates.length) return;
    video.onerror = function () { probe(i + 1); };
    video.src = candidates[i];
  }
  // The tile's label: the video's quality as YouTube names it (from the data
  // by the file's size, else from the size as YouTube does) and the language
  // of its audio; the full code only if "1080p · CODE" fits, so a code looks
  // the same at any quality; what still does not fit is cut off.
  var tileLabel = tile.querySelector('.label');
  var qualities = JSON.parse(panel.dataset.qualities || '{}');
  var audioLanguage = panel.dataset.language;
  var LADDER = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320];
  function qualityOf(w, h) {
    if (qualities[w + 'x' + h]) return qualities[w + 'x' + h];
    var size = Math.max(Math.min(w, h), Math.max(w, h) * 9 / 16), step = LADDER[0];
    LADDER.forEach(function (s) { if (Math.abs(s - size) < Math.abs(step - size)) step = s; });
    return step + 'p';
  }
  function labelTile() {
    var code = audioLanguage;
    if (code) {
      tileLabel.textContent = '1080p · ' + code;
      if (tileLabel.scrollWidth > tileLabel.clientWidth) code = code.split('-')[0];
    }
    tileLabel.textContent = qualityOf(video.videoWidth, video.videoHeight) + (code ? ' · ' + code : '');
  }
  video.addEventListener('loadedmetadata', function () {
    video.onerror = null;
    body.classList.add('has-video');
    if (video.videoWidth) labelTile();
    seek.max = video.duration;
    panel.querySelector('.total').textContent = clock(video.duration);
  });
  probe(0);
  function isOpen() { return body.classList.contains('player-open'); }
  function jump(t) { video.currentTime = Math.max(0, Math.min(video.duration || 0, t)); }
  function all(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
  function indexAt(times, t) {  // the last index whose time is <= t, or -1
    var low = 0, high = times.length - 1, found = -1;
    while (low <= high) {
      var mid = (low + high) >> 1;
      if (times[mid] <= t + 0.05) { found = mid; low = mid + 1; } else { high = mid - 1; }
    }
    return found;
  }

  // Timestamps (chapters, info cards, transcripts, description, summary,
  // comments) and the sentences of the continuous transcript play the local
  // copy while the player is open; otherwise links open YouTube as usual.
  document.addEventListener('click', function (e) {
    if (!isOpen() || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var target = e.target.closest('a.t[data-t], .transcript span[data-c]');
    if (!target) return;
    if (target.tagName === 'SPAN' && !window.getSelection().isCollapsed) return;  // selecting text
    e.preventDefault();
    jump(Number(target.dataset.t || target.dataset.c));
    video.play();
  });

  // Transcripts and chapters: each transcript version is a list of cues.
  var lists = all('section.transcript').map(function (section) {
    var els = all('[data-c]', section);
    return {section: section, els: els, times: els.map(function (el) { return Number(el.dataset.c); })};
  }).filter(function (list) { return list.els.length; });
  function timed(list) {
    return list.section.classList.contains('with-timestamps') || list.section.classList.contains('bilingual');
  }
  // Under the video: the first transcript with timestamps, else the continuous one.
  var source = lists.filter(timed)[0] || lists[0];
  // Translations: one per bilingual transcript, in page order, chosen by the
  // language buttons (none at first).
  var bilinguals = lists.filter(function (l) { return l.section.classList.contains('bilingual'); });
  // subtitles: 0 off, 1 the sentence only, 2 and 3 with 1 or 2 lines before and after
  var language = -1, subtitles = 0, showChapters = false;
  var chapters = all('#chapters li').filter(function (li) { return li.querySelector('a.t[data-t]'); });
  var chapterTimes = chapters.map(function (li) { return Number(li.querySelector('a.t').dataset.t); });
  function textOf(el, column) {  // without the speaker marks
    var texts = el.querySelectorAll('.text');
    var node = texts.length ? texts[column || 0] : el;
    if (!node) return '';
    var copy = node.cloneNode(true);
    all('.speaker', copy).forEach(function (mark) { mark.remove(); });
    return copy.textContent.replace(/\s+/g, ' ').trim();
  }
  var box = panel.querySelector('.now-playing');
  var boxPart = {};
  ['chapter', 'prev2', 'prev', 'current', 'translation', 'next', 'next2'].forEach(function (name) {
    boxPart[name] = box.querySelector('.' + name);
  });
  var subtitlesButton = panel.querySelector('[data-do=subtitles]');
  var langs = panel.querySelector('.langs');
  var langButtons = bilinguals.map(function (list, i) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'lang';
    button.disabled = true;
    button.setAttribute('aria-pressed', 'false');
    button.textContent = list.section.dataset.lang || String(i + 1);
    button.title = 'Translation: ' + button.textContent;
    button.addEventListener('click', function () { setLanguage(i); button.blur(); });
    langs.appendChild(button);
    return button;
  });
  function setLanguage(i) {  // a switch: another language replaces it, the same one turns it off
    language = language === i ? -1 : i;
    langButtons.forEach(function (b, n) { b.setAttribute('aria-pressed', String(n === language)); });
    update(true);
  }
  var chaptersButton = panel.querySelector('[data-do=chapters]');
  function showBox() {  // the box shows when the sentences or the chapter are on
    box.hidden = !subtitles && !showChapters;
    boxPart.chapter.classList.toggle('off', !showChapters);
  }
  function setSubtitles(mode) {  // 0-3; off resets the languages
    subtitles = mode;
    subtitlesButton.setAttribute('aria-pressed', String(mode > 0));
    subtitlesButton.querySelector('.dots').innerHTML =
      ['', '<i></i>', '<i class="full"></i>', '<i class="full"></i><i class="full"></i>'][mode];
    langButtons.forEach(function (b) { b.disabled = !mode; });
    if (!mode) setLanguage(-1);
    showBox();
    update(true);
  }
  function setChapters(on) {
    showChapters = on;
    chaptersButton.setAttribute('aria-pressed', String(on));
    showBox();
  }
  box.hidden = true;
  var shownCue = null, shownChapter = null, marked = null;
  // follow: the followed transcript (-1 = off); lastFollow: the one used last,
  // where Follow starts again after a scroll ended it (until reload); after
  // the last transcript Follow is off and starts again with the first
  var follow = -1, lastFollow = 0, followButton = panel.querySelector('[data-do=follow]');
  var autoScroll = false, autoTimer = null;
  function scrollToCue(el) {
    autoScroll = true;
    clearTimeout(autoTimer);
    autoTimer = setTimeout(function () { autoScroll = false; }, 1500);
    el.scrollIntoView({block: 'center', behavior: 'smooth'});
  }
  function mark(el) {  // the current cue of the followed transcript
    if (marked === el) return false;
    if (marked) marked.classList.remove('now');
    marked = el;
    if (el) el.classList.add('now');
    return true;
  }
  function placeFollow(force) {
    var list = lists[follow];
    var i = Math.max(0, indexAt(list.times, video.currentTime));
    if (mark(list.els[i]) || force) scrollToCue(list.els[i]);
  }
  function setFollow(n) {
    follow = n;
    if (n >= 0) lastFollow = n;
    if (followButton) {
      followButton.setAttribute('aria-pressed', String(n >= 0));
      followButton.textContent = n >= 0 && lists.length > 1
        ? 'Follow ' + (n + 1) + '/' + lists.length : 'Follow';
    }
    mark(null);
    if (n >= 0) placeFollow(true);
  }
  function update(force) {
    var t = video.currentTime;
    var c = isOpen() ? indexAt(chapterTimes, t) : -1;
    if (force || c !== shownChapter) {
      chapters.forEach(function (li, i) { li.classList.toggle('now', i === c); });
      boxPart.chapter.textContent = c >= 0 ? chapters[c].querySelector('.label').textContent : '';
      shownChapter = c;
    }
    if (!source) return;
    var i = indexAt(source.times, t);
    if (force || i !== shownCue) {
      shownCue = i;
      var context = Math.max(0, subtitles - 1);  // lines before and after the sentence
      function cue(k) { return k >= 0 && source.els[k] ? textOf(source.els[k]) : ''; }
      boxPart.prev2.textContent = context >= 2 && i >= 0 ? cue(i - 2) : '';
      boxPart.prev.textContent = context >= 1 && i >= 0 ? cue(i - 1) : '';
      boxPart.current.textContent = subtitles ? cue(i) : '';  // Chapters alone: no sentence
      boxPart.next.textContent = context >= 1 ? cue(i + 1) : '';
      boxPart.next2.textContent = context >= 2 ? cue(i + 2) : '';
      var translation = bilinguals[language];
      var j = translation ? indexAt(translation.times, t) : -1;
      boxPart.translation.textContent = j >= 0 ? textOf(translation.els[j], 1) : '';
    }
    if (follow >= 0) placeFollow(false);
  }
  // Clicking the lines under the video shows them in the transcript on the right.
  box.addEventListener('click', function () {
    var list = follow >= 0 ? lists[follow] : source;
    if (!list) return;
    var el = list.els[Math.max(0, indexAt(list.times, video.currentTime))];
    el.classList.add('found');
    setTimeout(function () { el.classList.remove('found'); }, 2000);
    if (follow >= 0) scrollToCue(el); else el.scrollIntoView({block: 'center', behavior: 'smooth'});
  });
  window.addEventListener('scrollend', function () {
    clearTimeout(autoTimer);
    autoTimer = setTimeout(function () { autoScroll = false; }, 100);
  });
  window.addEventListener('scroll', function () {  // scrolling by hand ends Follow
    if (follow >= 0 && !autoScroll) setFollow(-1);
  }, {passive: true});
  // Only the buttons that have something to show: no transcript, no Follow and
  // Subtitles; no chapters, no Chapters; neither, no row at all.
  if (!lists.length) { followButton.remove(); subtitlesButton.remove(); langs.remove(); }
  if (!chapters.length) chaptersButton.remove();
  if (!lists.length && !chapters.length) panel.querySelector('.buttons.follow').remove();

  function setOpen(open) {
    body.classList.toggle('player-open', open);
    tile.querySelector('.value').textContent = open ? '✕ Close' : '▶ Play';
    all('a.t[data-t]').forEach(function (a) {
      if (open) a.title = 'Play from ' + a.textContent; else a.removeAttribute('title');
    });
    if (!open) setFollow(-1);
    update(true);
  }
  var actions = {
    start: function () { video.pause(); jump(0); },  // always stopped at the start
    end: function () { jump(video.duration); },
    back: function () { jump(video.currentTime - 10); },
    // To the start of the previous sentence; pressed again, one more. A jump
    // of more than 20 s (a long pause, music) or no transcript: 5 s back.
    repeat: function () {
      var t = video.currentTime, i = source ? indexAt(source.times, t) : -1;
      var target = i > 0 ? source.times[i - 1] : -1;
      jump(target >= 0 && t - target <= 20 ? target : t - 5);
    },
    forward: function () { jump(video.currentTime + 10); },
    toggle: function () { if (video.paused) video.play(); else video.pause(); },
    // Follow marks the current sentence in a transcript and keeps it in view,
    // starting with the one used last; pressed again it moves on to the next
    // transcript, and after the last one it is off.
    follow: function () {
      if (follow < 0) setFollow(lastFollow);
      else if (follow + 1 < lists.length) setFollow(follow + 1);
      else { setFollow(-1); lastFollow = 0; }
    },
    chapters: function () { setChapters(!showChapters); },
    subtitles: function () { setSubtitles((subtitles + 1) % 4); }
  };
  tile.addEventListener('click', function () {
    tile.blur();  // so that the space key then pauses the video, not the tile
    if (isOpen()) {
      video.pause();
      if (document.pictureInPictureElement) document.exitPictureInPicture();
      setOpen(false);
    } else {
      setOpen(true);
      video.play();
    }
  });
  all('[data-do]', panel).forEach(function (button) {
    button.addEventListener('click', function () { actions[button.dataset.do](); button.blur(); });
  });
  all('[data-speed]', panel).forEach(function (button) {
    button.addEventListener('click', function () {
      video.playbackRate = Number(button.dataset.speed);
      all('[data-speed]', panel).forEach(function (other) {
        other.setAttribute('aria-pressed', String(other === button));
      });
      button.blur();
    });
  });
  seek.addEventListener('input', function () { jump(Number(seek.value)); });
  video.addEventListener('timeupdate', function () {
    seek.value = video.currentTime;
    panel.querySelector('.now').textContent = clock(video.currentTime);
    update(false);
  });
  video.addEventListener('seeked', function () { update(false); });
  video.addEventListener('play', function () { toggle.textContent = '❚❚'; });
  video.addEventListener('pause', function () { toggle.textContent = '▶'; });
  video.addEventListener('enterpictureinpicture', function () { body.classList.add('pip'); });
  video.addEventListener('leavepictureinpicture', function () { body.classList.remove('pip'); });
  if ('mediaSession' in navigator) {  // ±10 s in the browser's own picture-in-picture window
    try {
      navigator.mediaSession.setActionHandler('seekbackward', actions.back);
      navigator.mediaSession.setActionHandler('seekforward', actions.forward);
    } catch (e) { /* not supported */ }
  }
  document.addEventListener('keydown', function (e) {
    if (!isOpen() || e.altKey || e.metaKey
        || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(e.target.tagName)) return;
    // Space play/pause, arrows 10 s, Shift+left the previous sentence,
    // Ctrl+arrows the start and the end.
    var key = (e.ctrlKey ? 'Ctrl+' : '') + (e.shiftKey ? 'Shift+' : '') + e.key;
    var action = {' ': 'toggle', 'ArrowLeft': 'back', 'ArrowRight': 'forward',
                  'Shift+ArrowLeft': 'repeat', 'Ctrl+ArrowLeft': 'start',
                  'Ctrl+ArrowRight': 'end'}[key];
    if (action) { e.preventDefault(); actions[action](); }
  });
})();
"""


def video_files_for(info: dict, folder: Path) -> list[str]:
    """Video files next to the page with the video's id in their name, found
    when the page is built: the page tries them first, so a renamed video is
    found too (the browser itself cannot look into the folder)."""
    files = files_with_id(folder, info.get("id"), VIDEO_FILE_EXTENSIONS)
    if len(files) > 1:
        log(f"Note: several videos for [{info.get('id')}] in {folder}: "
            + ", ".join(p.name for p in files) + f"; the page tries {files[0].name} first")
    return [p.name for p in files]


def video_names(info: dict) -> list[str]:
    """Base names a downloaded video file may have, besides the page's own:
    yt-dlp's default name "Title [id]" (as the page was named originally)."""
    try:
        return [Path(output_path(info, Path("."))).stem]
    except Exception:
        return []


# --------------------------------------------------------------------------
# About this copy (the folded block at the bottom of the page)
# --------------------------------------------------------------------------

REPOSITORY = "https://github.com/mwislowski/yt-video-archiver"
COPYRIGHT = "© 2026 Mateusz Wisłowski"
DISCLAIMER = ("The video, its texts and images belong to their authors and to the commenters. "
              "This copy is meant for personal use; please support the authors by watching "
              "and subscribing on YouTube. The software comes without any warranty; the user "
              "is responsible for complying with YouTube's terms and the law.")
UNREAD_PAGE = "the video page could not be read"


def browser_name(spec: str | None) -> str | None:
    """'firefox:profile' -> 'firefox': the browser only, no profile or path."""
    return re.split(r"[:+]", spec)[0].strip().lower() if spec else None


# BROWSER[+KEYRING][:PROFILE][::CONTAINER], as yt-dlp's own command line reads it
COOKIES_SPEC_RE = re.compile(r"""(?x)
    (?P<name>[^+:]+)
    (?:\s*\+\s*(?P<keyring>[^:]+))?
    (?:\s*:\s*(?!:)(?P<profile>.+?))?
    (?:\s*::\s*(?P<container>.+))?""")


def cookies_spec(spec: str) -> tuple:
    """'firefox:fTW1hk7Y.Profile 1' -> ('firefox', 'fTW1hk7Y.Profile 1', None, None):
    the tuple yt-dlp's library wants (browser, profile, keyring, container).
    The library takes no text; given one, it reads all of it as the browser."""
    match = COOKIES_SPEC_RE.fullmatch(spec.strip())
    if not match:
        raise ValueError(f"not a browser: {spec!r}")
    name, keyring, profile, container = match.group("name", "keyring", "profile", "container")
    name = name.strip().lower()
    if name not in SUPPORTED_BROWSERS:
        raise ValueError(f"unsupported browser {name!r}; use one of: "
                         + ", ".join(sorted(SUPPORTED_BROWSERS)))
    if keyring is not None:
        keyring = keyring.strip().upper()
        if keyring not in SUPPORTED_KEYRINGS:
            raise ValueError(f"unsupported keyring {keyring!r}; use one of: "
                             + ", ".join(sorted(SUPPORTED_KEYRINGS)))
    return name, profile, keyring, container


def cookies_argument(spec: str) -> str:
    """Checked when the command is read, before anything is downloaded."""
    try:
        cookies_spec(spec)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return spec


def image_size(data_uri: str | None) -> tuple[int, int] | None:
    """(width, height) of an embedded JPEG, PNG, WebP or GIF image."""
    try:
        data = base64.b64decode(data_uri.split(",", 1)[1])
    except Exception:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 ":
            return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF)
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X":
            return (int.from_bytes(data[24:27], "little") + 1,
                    int.from_bytes(data[27:30], "little") + 1)
        return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 1 if marker == 0xFF else 2
                continue
            length = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return (int.from_bytes(data[i + 7:i + 9], "big"),
                        int.from_bytes(data[i + 5:i + 7], "big"))
            i += 2 + length
    return None


def watch_page_status(info: dict) -> str:
    """"read", "partial" or "failed" (pages made before the status was kept:
    read if the description extras are there)."""
    return info.get(WATCH_PAGE_KEY) or ("read" if info.get(EXTRAS_KEY) is not None else "failed")


def date_time(moment: dt.datetime) -> str:
    return moment.strftime(f"{DATE_FORMAT} %H:%M")


def about_downloaded(info: dict, archived: dt.date) -> str:
    epoch = info.get("epoch")
    when = date_time(dt.datetime.fromtimestamp(epoch)) if epoch else archived.strftime(DATE_FORMAT)
    video_id = info.get("id") or ""
    source = f'<a href="{esc(f"{YOUTUBE}/watch?v={video_id}")}">youtube.com/watch?v={esc(video_id)}</a>'
    programs = []
    version = (info.get(DOWNLOAD_KEY) or {}).get("yt2html")
    if version:
        programs.append(f"yt2html {esc(version)}")
    ytdlp = (info.get("_version") or {}).get("version")
    if ytdlp:
        programs.append(f"yt-dlp {esc(ytdlp)}")
    return f"{when} from {source}" + (f" with {' and '.join(programs)}" if programs else "")


def about_page_built(from_json: bool, built: dt.datetime) -> str:
    if not from_json:
        return "at download"
    return f"{date_time(built)} from the saved data, with yt2html {__version__}"


def about_playlist(info: dict) -> str:
    playlist = info.get(PLAYLIST_KEY)
    if not playlist:
        return "none"
    url = f"{YOUTUBE}/playlist?list={playlist.get('id') or ''}"
    return (f'video {playlist.get("position") or "?"} of {playlist.get("count") or "?"} in '
            f'<a href="{esc(url)}">{esc(playlist.get("title") or playlist.get("id") or "")}</a>')


def format_transcript_spec(spec: list[tuple[str, str | None]]) -> str:
    return ",".join(version if language is None else f"{version}:{language}"
                    for version, language in spec) or "none"


def about_command(info: dict, spec: list, theme: str, show_snapshot: bool,
                  no_avatars: bool) -> str:
    """The command that makes an equivalent copy today: the settings that
    shaped this page, from the download and (for a page built from saved
    data) from the build; defaults are left out, paths never appear."""
    video_id = info.get("id") or ""
    # An id starting with "-" would be taken for an option; PowerShell 5.1 may
    # drop a "--" before it, so the full address is used instead.
    parts = ["yt2html", video_id if video_id and not video_id.startswith("-")
             else f"{YOUTUBE}/watch?v={video_id}"]
    if "comments" not in info:
        parts.append("--no-comments")
    elif info.get(LIMIT_KEY) is not None:
        parts += ["--max-comments", str(info[LIMIT_KEY])]
    if CARDS_KEY not in info:
        parts.append("--no-cards")
    if no_avatars:
        parts.append("--no-avatars")
    versions = format_transcript_spec(spec if TRANSCRIPT_KEY in info else [])
    if versions != "plain":
        parts += ["--transcript", versions]
    if theme != "auto":
        parts += ["--theme", theme]
    if not show_snapshot:
        parts.append("--no-snapshot")
    browser = (info.get(DOWNLOAD_KEY) or {}).get("cookies_from_browser")
    if browser:
        parts += ["--cookies-from-browser", browser]
    return f'<code class="command">{esc(" ".join(parts))}</code>'


def about_video_page(info: dict) -> str:
    status = watch_page_status(info)
    if status == "read":
        return "read"
    if status == "partial":
        return "read, without the player data (transcript language guessed)"
    return (f"{UNREAD_PAGE}: this copy may lack the AI summary, other timestamp lists, "
            "info cards, People mentioned and the podcast; chapters without their origin, "
            "the license without its link, YouTube's comment count unknown, the channel "
            "avatar only from the author's comments")


CHAPTER_ORIGINS = {"description": "from the description", "auto": "auto-generated by YouTube"}


def about_markers(info: dict) -> str:
    """The "Markers" row: chapters and the other timestamp lists YouTube shows
    (e.g. Walkthroughs, points in the video, not a division of it), each named
    in lower case with its count in brackets; "none" when the page has no list."""
    lists = marker_lists_of(info)
    chapters = next((m for m in lists if m.get("id") == "chapters"), None)
    others = [m for m in lists if m.get("id") != "chapters"]
    read = watch_page_status(info) != "failed"
    if chapters:
        count = len(chapters.get("items") or [])
        origin = (CHAPTER_ORIGINS.get(chapters.get("source"), "origin unclear") if read
                  else f"origin unknown ({UNREAD_PAGE})")
        parts = [f"chapters ({count}), {origin}"]
    elif not read:
        parts = [f"none in the yt-dlp data ({UNREAD_PAGE})"]
    else:
        # Deliberately simple: lines with a time, not a check of YouTube's rules.
        timed = sum(1 for line in (info.get("description") or "").splitlines()
                    if CLOCK_RE.search(line))
        parts = [f"no chapters; {timed} lines of the description contain times, but YouTube "
                 "made no chapters from them"] if timed >= 3 else (["no chapters"] if others else [])
    # list names in lower case, like every other row ("walkthroughs")
    parts += [f'{esc((m.get("title") or "timestamps").lower())} ({len(m.get("items") or [])})'
              for m in others]
    return "; ".join(parts) or "none"


def about_cards(info: dict) -> str:
    if CARDS_KEY not in info:
        return "left out"
    cards = info[CARDS_KEY]
    if cards is None:
        return f"unknown ({UNREAD_PAGE})"
    if not cards:
        return "none"
    kinds = []
    for kind in CARD_TYPES.values():  # video, playlist, channel, link
        count = sum(1 for c in cards if c.get("type") == kind)
        if not count:
            continue
        text = plural(count, kind)
        if kind == "link":  # only links can fail: resolving the redirect
            redirects = sum(1 for c in cards if c.get("type") == "link"
                            and "youtube.com/redirect" in (c.get("url") or ""))
            text += (" (all to their websites)" if not redirects else
                     f" ({redirects} kept as a YouTube redirect)" if redirects == 1 else
                     f" ({redirects} kept as YouTube redirects)")
        kinds.append(text)
    parts = [f"{plural(len(cards), 'card')}: {', '.join(kinds)}"]
    if all(c.get("start") is None for c in cards):
        parts.append("times not shown (YouTube's data did not match)")
    return "; ".join(parts)


def about_transcript(info: dict, spec: list) -> str:
    """Language (features); N versions: the versions shown, each bilingual one
    with its translation. Like every row, it describes the page, not what a
    saved data file holds besides."""
    if TRANSCRIPT_KEY not in info or not spec:
        return "left out"
    transcript = info[TRANSCRIPT_KEY]
    if transcript is None:
        reason = info.get(TRANSCRIPT_MISSING_KEY)
        return f"none: {esc(reason)}" if reason else "none"
    kind = "auto-generated" if transcript.get("kind") == "auto" else "by the author"
    features = (f"original language, {kind}" if transcript.get("original", True)
                else f"{kind}; original language unknown")
    name = transcript.get("name") or transcript.get("language") or ""
    translations = translations_of(transcript)

    def translated(language):
        return f'{esc((translations.get(language) or {}).get("name") or language)} (auto-translated)'

    versions = [f"bilingual with {translated(language)}" if version == "bilingual" else version
                for version, language in spec]
    return f"{esc(name)} ({features}); {plural(len(versions), 'version')}: {', '.join(versions)}"


def about_comments(info: dict) -> str:
    status = comment_status(info)
    if status == "not-downloaded":
        return "left out"
    if status == "turned-off":
        return "turned off or unavailable"
    comments = info["comments"]
    if not comments and status == "complete":
        return "no comments"
    roots, _ = build_comment_tree(comments)
    replies = len(comments) - len(roots)
    count_text, _ = youtube_comment_count(info)
    amount = (f"{format_int(len(comments))} of {esc(count_text)}" if count_text
              else f"{format_int(len(comments))} downloaded, YouTube's count unknown")
    state = {"complete": "complete", "interrupted": "interrupted (Ctrl+C)",
             "limited": f"limited to {format_int(info.get(LIMIT_KEY) or 0)}"}[status]
    return (f"{amount}; {plural(len(roots), 'thread')}, "
            f"{replies} {'reply' if replies == 1 else 'replies'}; {state}")


def about_images(info: dict, thumbnail: str | None, avatar_uris: dict[str, str],
                 extra_images: dict[str, str], no_avatars: bool) -> str:
    size = image_size(thumbnail) if thumbnail else None
    parts = [f"thumbnail {size[0]}×{size[1]}" if size else
             "thumbnail" if thumbnail else "no thumbnail"]
    channel = channel_avatar_url(info)
    parts.append("channel avatar" if channel in avatar_uris else
                 "channel avatar shown as a letter" if channel else "no channel avatar")
    wanted = comment_avatar_urls(info)
    if no_avatars:
        parts.append("comment avatars left out")
    elif wanted:
        embedded = sum(1 for u in wanted if u in avatar_uris)
        missing = len(wanted) - embedded
        parts.append(f"comment avatars {embedded} of {len(wanted)}"
                     + (f" ({missing} shown as letters)" if missing else ""))
    else:
        parts.append("no comment avatars")
    others = extra_image_urls(info)
    if others:
        parts.append(f"other images {sum(1 for u in others if u in extra_images)} of {len(others)}")
    else:
        parts.append("no other images")
    return "; ".join(parts)


def about_software() -> str:
    return (f'<a href="{REPOSITORY}">yt2html</a> {__version__}, {COPYRIGHT}, '
            f'<a href="{REPOSITORY}/blob/HEAD/LICENSE">MIT License</a> · '
            f'<a href="https://github.com/yt-dlp/yt-dlp">yt-dlp</a> {yt_dlp.version.__version__}, '
            '<a href="https://unlicense.org">Unlicense</a> (public domain); '
            "these licenses cover the programs, not the content of this copy.")


# --------------------------------------------------------------------------
# --video (experimental): the formats YouTube offers, described in About this copy
# --------------------------------------------------------------------------

VIDEO_CODECS = (("avc1", "h264"), ("av01", "av1"), ("vp09", "vp9"), ("vp9", "vp9"),
                ("hev1", "h265"), ("hvc1", "h265"))
AUDIO_CODECS = (("mp4a", "aac"), ("opus", "opus"))


def codec_name(codec: str | None, names: tuple) -> str | None:
    """'avc1.640028' -> 'h264'; None for 'none' or no codec."""
    codec = (codec or "none").lower()
    if codec == "none":
        return None
    return next((name for prefix, name in names if codec.startswith(prefix)), codec.split(".")[0])


def format_size(info: dict, fmt: dict) -> int | None:
    """Bytes: exact, YouTube's estimate, or from the bitrate and the duration."""
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if not size and fmt.get("tbr") and info.get("duration"):
        size = fmt["tbr"] * 1000 / 8 * info["duration"]
    return int(size) if size else None


def human_size(size: int | None) -> str:
    """Approximate: "~1.2 GB", "~609 MB", "<1 MB" below one megabyte."""
    if not size:
        return "size unknown"
    if size < 1e6:
        return "<1 MB"
    return f"~{size / 1e9:.1f} GB" if size >= 1e9 else f"~{size / 1e6:.0f} MB"


def video_formats(info: dict) -> tuple[list[dict], list[dict]]:
    """(video-only, audio-only) formats; no DRC (quieter audio) variants.
    Storyboards have neither codec, so they drop out. Combined formats (old
    low-quality mp4) count only when nothing else exists. Premium formats
    (signed in with YouTube Premium) stay; the row marks them."""
    usable = [f for f in info.get("formats") or []
              if "drc" not in str(f.get("format_id") or "")]
    has = lambda f, kind: codec_name(f.get(kind), VIDEO_CODECS if kind == "vcodec" else AUDIO_CODECS)
    video = [f for f in usable if has(f, "vcodec") and not has(f, "acodec") and f.get("height")]
    audio = [f for f in usable if has(f, "acodec") and not has(f, "vcodec")]
    if not video:
        video = [f for f in usable if has(f, "vcodec") and f.get("height")]
    return video, audio


UNKNOWN_LANGUAGE = "?"   # the key of a track without a language in the data


def audio_language(fmt: dict) -> str:
    return fmt.get("language") or UNKNOWN_LANGUAGE


def track_text(code: str, codec: str | None = None) -> str:
    """A track for a text: "en aac", or only "aac" when the language is unknown."""
    return " ".join(part for part in (None if code == UNKNOWN_LANGUAGE else esc(code), codec)
                    if part)


def is_hls(fmt: dict) -> bool:
    """HLS: the video cut into short pieces listed in a playlist (iPhone, Safari).
    It gives no file size, only a declared bitrate, which proved far too high
    (12 times the real average in a test), so the row shows no size for it."""
    return str(fmt.get("protocol") or "").startswith("m3u8")


# The kind of an audio track, as YouTube writes it into each format's address
# ("xtags=acont%3Ddubbed-auto%3Alang%3Dpl"): the names the row shows, in the
# order of its rows. A kind not listed here is shown as YouTube names it.
AUDIO_KINDS = {"original": "original", "dubbed": "dubbed", "dubbed-auto": "auto-dubbed",
               "descriptive": "descriptive"}
AUDIO_ROLES = tuple(AUDIO_KINDS.values())
# A download goes up to this height; the planned default is the best quality
# up to it. Codecs a download takes, in this order (not Premium).
DEFAULT_MAX_HEIGHT = 1080
RULE = (("av1", False), ("h264", False))


def audio_kind(fmt: dict) -> str | None:
    """YouTube's kind of the audio track (acont: original, dubbed, dubbed-auto,
    descriptive) from the xtags of the format's address (a query parameter,
    or a path part in HLS addresses); None without it. Undocumented, so the
    row falls back to yt-dlp's language preference when it is missing."""
    for key in ("url", "manifest_url"):
        found = re.search(r"xtags[=/]([^&/?#]+)", str(fmt.get(key) or ""))
        if found:
            tags = dict(part.partition("=")[::2]
                        for part in urllib.parse.unquote(found.group(1)).split(":"))
            return tags.get("acont")
    return None


def is_premium(fmt: dict) -> bool:
    """Formats YouTube gives only to Premium accounts (e.g. 1080p with a higher
    bitrate); yt-dlp says "Premium" in the format note."""
    return "premium" in (fmt.get("format_note") or "").lower()


def video_key(fmt: dict) -> tuple[str | None, bool]:
    """A video format's column: (codec, Premium)."""
    return codec_name(fmt.get("vcodec"), VIDEO_CODECS), is_premium(fmt)


class FormatChoice:
    """What a download can take from the formats in the data, without any
    presentation: the audio tracks (language, role), the main audio (the
    track that goes into the video), the qualities as YouTube names them,
    and the variants a download can choose (up to DEFAULT_MAX_HEIGHT, the
    first codec of RULE there), best first; the first is the planned
    default. Video formats shows it, --video will take from it."""

    def __init__(self, info: dict):
        self.info = info
        video, audio = video_formats(info)
        # Sizes, the main audio and the variants come from formats with a
        # real size (DASH: one file each); HLS formats are only named.
        self.hls = [f for f in video if is_hls(f)]
        self.video = [f for f in video if not is_hls(f)]
        self.audio = [f for f in audio if not is_hls(f)] or audio
        # A video with a single language has nothing to mark its tracks by:
        # that is the original.
        self._single = len({audio_language(f) for f in self.audio}) == 1
        self._has_original = any(audio_kind(f) == "original"
                                 or f.get("language_preference") == 10 for f in self.audio)
        self.original = next((self.track(f) for f in self.audio
                              if self.role_of(f) == "original"), None)
        # The main audio: the original (aac, else any codec); else an ordinary
        # track, an audio description only when there is nothing else; never
        # a Premium format.
        regular = [f for f in self.audio if not is_premium(f)]
        first = self.original or (self.track(self.audio[0]) if self.audio else None)
        spoken = [f for f in regular if self.role_of(f) != "descriptive"] or regular
        self.main_audio = (self.audio_for(first, "aac") or self.audio_for(first)
                           or (self.best(spoken) if spoken else None))
        self._audio_bytes = format_size(info, self.main_audio) if self.main_audio else 0
        # The quality as YouTube names it ("1080p" also for 1920x804 or 1080x1920)
        self._qualities = youtube_qualities(self.video + self.hls)
        self.variants = []
        for height in sorted({self.quality(f) for f in self.video}, reverse=True):
            if height > DEFAULT_MAX_HEIGHT:
                continue
            for fps in sorted({f.get("fps") or 0 for f in self.video
                               if self.quality(f) == height}, reverse=True):
                for key in RULE:
                    pool = [f for f in self.video if self.quality(f) == height
                            and video_key(f) == key and (f.get("fps") or 0) == fps]
                    if pool:
                        fmt = self.best(pool)
                        self.variants.append({
                            "height": height, "fps": fps, "format": fmt, "codec": key[0],
                            "quality": quality_label(height, fps),
                            "parameter": quality_parameter(height, fps),
                            "size": self.with_audio(fmt)})
                        break

    def role_of(self, fmt: dict) -> str | None:
        """YouTube's kind of track; without it, yt-dlp's language preference:
        10 the original, -10 an audio description, the rest dubbed when an
        original is known."""
        kind = audio_kind(fmt)
        if kind:
            return AUDIO_KINDS.get(kind, kind)
        if fmt.get("language_preference") == 10:
            return "original"
        if fmt.get("language_preference") == -10:
            return "descriptive"
        if self._single:
            return "original"
        return "dubbed" if self._has_original else None

    def track(self, fmt: dict) -> tuple[str, str | None]:
        """An audio format's track: (language, role)."""
        return audio_language(fmt), self.role_of(fmt)

    def best(self, formats: list[dict]) -> dict:
        """The best of alike formats: the highest frame rate, then the largest."""
        return max(formats, key=lambda f: (f.get("fps") or 0, format_size(self.info, f) or 0))

    def audio_for(self, key, codec: str | None = None, premium: bool = False) -> dict | None:
        """The best format of an audio track, in a codec if given."""
        pool = [f for f in self.audio if self.track(f) == key and is_premium(f) == premium
                and (codec is None or codec_name(f.get("acodec"), AUDIO_CODECS) == codec)]
        return self.best(pool) if pool else None

    def quality(self, fmt: dict) -> int:
        return self._qualities[id(fmt)]

    def with_audio(self, fmt: dict) -> str:
        """The size of a video format together with the main audio."""
        size = format_size(self.info, fmt)
        return human_size(size + (self._audio_bytes or 0) if size else None)


def video_choice(info: dict) -> FormatChoice | None:
    """What a download can take; None when the data has no video or audio formats."""
    video, audio = video_formats(info)
    return FormatChoice(info) if video or audio else None


def video_overview(info: dict) -> dict | None:
    """The formats YouTube offers for this video, as tables: image (quality,
    i.e. height and frame rate, × codecs; sizes with the main audio) and
    languages (audio tracks with the subtitles in their language); the
    planned default (FormatChoice) marked in them; a short summary; whether
    yt-dlp had Deno (a JavaScript runtime) for the full list and was signed
    in. None when there are no formats."""
    choice = video_choice(info)
    if choice is None:
        return None
    video, hls, audio = choice.video, choice.hls, choice.audio
    track, best, audio_for, quality = choice.track, choice.best, choice.audio_for, choice.quality
    original, main_audio, with_audio = choice.original, choice.main_audio, choice.with_audio
    known = [(codec, premium) for codec in ("h264", "av1", "vp9", "h265")
             for premium in (False, True)]

    def label(key):
        return esc(key[0]) + (" Premium" if key[1] else "")

    def hdr(f):
        return [esc(f["dynamic_range"])] if f.get("dynamic_range") not in (None, "SDR") else []

    def extras(f):  # HLS names: no rows of their own, so the frame rate goes with the name
        return [f"{f['fps']:g} fps"] * ((f.get("fps") or 0) > 30) + hdr(f)

    def ordered(keys):
        return [k for k in known if k in keys] + [k for k in keys if k not in known]

    columns = ordered(list(dict.fromkeys(video_key(f) for f in video)))
    rows, row_of = [], {}   # row_of: (quality, fps) -> row index, DASH rows
    for height in sorted({quality(f) for f in video + hls}, reverse=True):
        # one row per frame rate at this quality (most often just one)
        rates = sorted({f.get("fps") or 0 for f in video if quality(f) == height},
                       reverse=True) or [None]
        for i, fps in enumerate(rates):
            cells, formats = [], {}
            for key in columns:
                pool = [f for f in video if quality(f) == height and video_key(f) == key
                        and (f.get("fps") or 0) == fps]
                if not pool:
                    cells.append(None)
                    continue
                f = formats[key] = best(pool)
                cells.append(", ".join([esc(with_audio(f))] + hdr(f)))   # "<1 MB" in HTML
            if hls:
                named = []
                for key in ordered(list(dict.fromkeys(
                        video_key(f) for f in hls if quality(f) == height))):
                    f = best([g for g in hls if quality(g) == height and video_key(g) == key])
                    named.append(" ".join([label(key)] + extras(f)))
                # HLS once per height, in its first row
                cells.append((", ".join(named) or None) if i == 0 else None)
            # the real size of the image: of the row's single-file formats, else HLS
            sized = [f for f in video if quality(f) == height and (f.get("fps") or 0) == fps
                     ] or [f for f in hls if quality(f) == height]
            resolution = ", ".join(dict.fromkeys(f"{f['width']}×{f['height']}" for f in sized
                                                 if f.get("width"))) or None
            selectable = height <= DEFAULT_MAX_HEIGHT and any(k in formats for k in RULE)
            row_of[height, fps] = len(rows)
            rows.append((quality_label(height, fps, selectable), resolution, cells))
    # The variants with their place in the image table; the first is marked.
    variants = [dict(v, row=row_of[v["height"], v["fps"]], column=columns.index((v["codec"], False)))
                for v in choice.variants]
    picks = [(variants[0]["row"], variants[0]["column"])] if variants and main_audio else []
    image = {"columns": [label(k) for k in columns] + (["HLS (sizes unknown)"] if hls else []),
             "rows": rows, "picks": picks,
             "audio": (track_text(audio_language(main_audio),
                              codec_name(main_audio.get("acodec"), AUDIO_CODECS))
                       if main_audio else None)}

    tracks = set(map(track, audio))
    audio_keys = [(codec, premium) for premium in (False, True) for codec in ("aac", "opus")
                  if any(audio_for(key, codec, premium) for key in tracks)]

    def track_order(key):  # by role (known ones first, in their order), then language
        language, role = key
        rank = (AUDIO_ROLES.index(role) if role in AUDIO_ROLES
                else len(AUDIO_ROLES) + (role is None))
        return rank, role or "", language

    tracks = sorted(tracks, key=track_order)

    # The languages table: one row per audio track, with the subtitles joined
    # to it by language; subtitles without a matching audio track get rows of
    # their own. The author's subtitles belong to the video: the row of the
    # same code, else the only row of the same language (an audio description
    # only when nothing else). Automatic ones are made from one audio track's
    # speech ("-orig"): its row by language, the only track of an unknown
    # language, else a row of their own.
    manual = [lang for lang, fmts in (info.get("subtitles") or {}).items()
              if fmts and lang != "live_chat"]
    auto = [lang for lang, fmts in (info.get("automatic_captions") or {}).items() if fmts]
    origs = [lang.removesuffix("-orig") for lang in auto if lang.endswith("-orig")]
    rows_of = [{"key": key, "author": [], "auto": []} for key in tracks]

    def joined(code, unknown_too=False):
        exact = [r for r in rows_of if r["key"][0].lower() == code.lower()]
        same = exact or [r for r in rows_of if base_language(r["key"][0]) == base_language(code)]
        found = [r for r in same if r["key"][1] != "descriptive"] or same
        if not found and unknown_too and [r["key"][0] for r in rows_of] == [UNKNOWN_LANGUAGE]:
            found = rows_of
        return found[0] if len(found) == 1 else None

    alone = {}   # subtitle code -> its own row
    for kind, codes in (("author", manual), ("auto", origs)):
        for code in codes:
            row = joined(code, unknown_too=kind == "auto") or alone.setdefault(
                code, {"key": (code, None), "author": [], "auto": [], "alone": True})
            row[kind].append(code)
    with_audio_languages = {base_language(r["key"][0]) for r in rows_of}
    original_base = base_language(original[0]) if original else None

    def language_order(row):  # languages with audio first (the original's first), then without
        code, role = row["key"]
        base = base_language(code)
        rank = (len(AUDIO_ROLES) + 2 if row.get("alone") else
                AUDIO_ROLES.index(role) if role in AUDIO_ROLES else len(AUDIO_ROLES) + (role is None))
        return base not in with_audio_languages, base != original_base, base, rank, code

    table_rows = sorted(rows_of + list(alone.values()), key=language_order)
    def codes_cell(row, kind):
        return esc(", ".join(row[kind])) or None

    language_rows = []
    for row in table_rows:
        code, role = row["key"]
        known = code != UNKNOWN_LANGUAGE   # unknown: no code, the name "unknown"
        if row.get("alone"):
            cells = [None] * len(audio_keys)
            names = info.get(TRANSLATIONS_KEY) or {}
            name = names.get(code) or names.get(code.split("-")[0]) or code
        else:
            cells = [esc(human_size(format_size(info, audio_for(row["key"], codec, premium))))
                     if audio_for(row["key"], codec, premium) else None
                     for codec, premium in audio_keys]
            name = (language_name(info, [f for f in audio if track(f) == row["key"]], code)
                    if known else "unknown")
        language_rows.append([esc(code) if known else None, esc(name),
                              esc(role) if role else None, *cells,
                              codes_cell(row, "author"), codes_cell(row, "auto")])
    audio_picks = []
    if main_audio:
        key = (codec_name(main_audio.get("acodec"), AUDIO_CODECS), False)
        if key in audio_keys:
            audio_picks.append((table_rows.index(rows_of[tracks.index(track(main_audio))]),
                                audio_keys.index(key)))
    # Machine translation: YouTube's list of target languages from the watch
    # page (given only when the video has subtitles; else yt-dlp's translation
    # entries), but the source language. Without a number, why: no subtitles,
    # YouTube's empty list (nothing to do about it), or no list read (try later).
    listed = info.get(TRANSLATIONS_KEY)
    targets = len(listed or [lang for lang in auto if not lang.endswith("-orig")])
    translated = max(targets - 1, 0)
    translation = "machine-translated subtitles: " + (
        f"possible in {translated} other languages for each row with subtitles" if translated
        else "none (no subtitles to translate)" if not (manual or auto)
        else "unknown (not read from YouTube; make the page again later)" if listed is None
        else "unknown (YouTube listed no translation languages)")
    languages_table = {"columns": [codec + (" Premium" if premium else "")
                                   for codec, premium in audio_keys],
                       "rows": language_rows, "picks": audio_picks,
                       "translation": translation}

    # The summary, short on purpose: the top quality and the original audio's
    # language (the tables tell the rest).
    summary = []
    if video or hls:
        top = max(map(quality, video + hls))
        fps = max((f.get("fps") or 0 for f in video + hls if quality(f) == top), default=0)
        summary.append(f"up to {top}p" + (f", {fps:g} fps" if fps else ""))
    if original and original[0] != UNKNOWN_LANGUAGE:
        summary.append(f"original audio: {esc(original[0])}")

    download = info.get(DOWNLOAD_KEY) or {}
    deno = download.get("deno")
    # The track in the video (the main audio) and the ones a download can add
    # (aac, m4a), by role, for the console list.
    in_video = track(main_audio) if main_audio else None
    extra = [key for key in tracks if key != in_video and audio_for(key, "aac")]
    known = sorted(filter(None, (format_size(info, audio_for(key, "aac")) for key in extra)))
    extra_sizes = list(dict.fromkeys(map(human_size, known)))   # smallest to largest
    return {"image": image, "languages": languages_table,
            "variants": variants, "in_video": in_video,
            "in_video_codec": codec_name(main_audio.get("acodec"), AUDIO_CODECS) if main_audio
            else None,
            "extra_audio": [(key[0], key[1]) for key in extra], "extra_sizes": extra_sizes,
            "summary": " · ".join(summary),
            "deno": ("yes" if deno else
                     "unknown (data saved by an older version)" if deno is None else
                     "no, so YouTube may have offered more formats than listed"),
            "signed_in": bool(download.get("cookies_from_browser"))}


QUALITY_LADDER = (144, 240, 360, 480, 720, 1080, 1440, 2160, 4320)


def quality_from_size(width: int, height: int) -> int:
    """YouTube's name for an image size when no format says it: the shorter
    side, or 9/16 of the longer one when that is more (a frame wider than
    16:9 is named after its width, as YouTube does: 1920x804 is "1080p", and
    so is 1080x1920), to the nearest step of the ladder."""
    size = max(min(width, height), max(width, height) * 9 / 16)
    return min(QUALITY_LADDER, key=lambda step: abs(step - size))


def youtube_qualities(formats: list[dict]) -> dict[int, int]:
    """{id(format): quality}: YouTube's own name from the format note
    ("1080p", "720p60", "1080p Premium"), else the name another format of the
    same size has (HLS formats have no note), else from the size, else the
    height."""
    def noted(f):
        found = re.match(r"(\d{3,4})p", f.get("format_note") or "")
        return int(found.group(1)) if found else None

    by_size = {(f.get("width"), f.get("height")): noted(f) for f in formats if noted(f)}
    result = {}
    for f in formats:
        size = (f.get("width"), f.get("height"))
        result[id(f)] = (noted(f) or by_size.get(size)
                         or (quality_from_size(*size) if all(size) else f["height"]))
    return result


def player_qualities(info: dict) -> dict[str, str]:
    """{"1920x1080": "1080p", ...}: YouTube's names of the image sizes in the
    data, for the player tile (the script reads the size of the video file)."""
    video, _ = video_formats(info)
    qualities = youtube_qualities(video)
    return {f"{f['width']}x{f['height']}": f"{qualities[id(f)]}p"
            for f in video if f.get("width")}   # video formats always have a height


def quality_parameter(height: int, fps: float | None) -> str:
    """The parameter that will choose a quality: "1080p30" ("720p" without
    a frame rate in the data)."""
    return f"{height}p" + (f"{fps:g}" if fps else "")


def quality_label(height: int, fps: float | None, selectable: bool = False) -> str:
    """The quality of a row of the image table ("1080p, 30 fps"), with the
    parameter that will choose it when a download can ("1080p, 30 fps
    (1080p30)"): up to 1080p, with AV1 or H.264. A height offered only as
    HLS (fps None, never selectable) shows alone."""
    label = f"{height}p" + (f", {fps:g} fps" if fps else "")
    return label + (f" ({quality_parameter(height, fps)})" if selectable else "")


QUALITY_WORDS = {"ultralow", "low", "medium", "high"}


def language_name(info: dict, formats: list[dict], code: str) -> str:
    """The English name of an audio track's language: from yt-dlp's format
    note of a single-file format ("English (US) original (default), medium"
    -> "English (US)"; HLS notes are in the track's own language, so not
    used), else from YouTube's list of translation languages (full code,
    then the language alone), else the code."""
    for f in formats:
        if is_hls(f):
            continue
        name = re.sub(r"\s+(original|descriptive)\b.*$|\s+\(default\)$", "",
                      (f.get("format_note") or "").split(",")[0]).strip()
        if name and name != code and name.lower() not in QUALITY_WORDS:
            return name
    names = info.get(TRANSLATIONS_KEY) or {}
    return names.get(code) or names.get(code.split("-")[0]) or code


def formats_table(title: str, head: list[str], rows: list[list], sizes: range = range(0),
                  picks: set = frozenset()) -> str:
    """A small table of the Video formats section; an empty cell is a dash.
    Columns in `sizes` hold sizes: right-aligned, never wrapped; the rest
    wraps. Cells in `picks` ((row, column) of the data rows) are the planned
    default."""
    def cell(tag, r, i, value):
        classes = " ".join(["size"] * (i in sizes) + ["planned"] * ((r, i) in picks))
        attribute = f' class="{classes}"' if classes else ""
        return f"<{tag}{attribute}>{value if value is not None else '–'}</{tag}>"
    return (f'<div class="formats"><table><caption>{title}</caption>'
            "<tr>" + "".join(cell("th", -1, i, h) for i, h in enumerate(head)) + "</tr>"
            + "".join("<tr>" + "".join(cell("td", r, i, v) for i, v in enumerate(row)) + "</tr>"
                      for r, row in enumerate(rows))
            + "</table></div>")




# Without video and audio formats (only the storyboard images for the seek
# bar's preview, or none at all): YouTube sometimes sends none for a while,
# e.g. after several requests for the same video.
NO_FORMATS = "no video or audio formats in the data"
NO_FORMATS_WHY = "YouTube sometimes sends none for a while"


def render_video_formats(info: dict) -> str:
    """The Video formats section above "About this copy": folded to one grey
    line with the summary; open, the tables. Without formats, one line that
    says so (it looks like a fault of the page otherwise)."""
    overview = video_overview(info)
    if overview is None:
        return ('<details class="formats-info"><summary>Video formats<span class="rest"> · '
                f'none in the data</span></summary>\n<div class="none">{NO_FORMATS}: '
                f'{NO_FORMATS_WHY}; make the page again later</div></details>')
    image, languages = overview["image"], overview["languages"]
    parts = []
    if image["rows"]:
        title = "image" + (f" (sizes include the audio: {image['audio']})" if image["audio"] else "")
        sized = len(image["columns"]) - image["columns"][-1:].count("HLS (sizes unknown)")
        parts.append(formats_table(title, ["quality", "resolution"] + image["columns"],
                                   [[quality, resolution, *cells]
                                    for quality, resolution, cells in image["rows"]],
                                   range(2, 2 + sized),
                                   {(r, 2 + c) for r, c in image["picks"]}))
    if languages["rows"]:
        parts.append(formats_table("languages",
                                   ["code", "language", "audio"] + languages["columns"]
                                   + ["subtitles by the author", "auto-generated subtitles"],
                                   languages["rows"], range(3, 3 + len(languages["columns"])),
                                   {(r, 3 + c) for r, c in languages["picks"]}))
    parts.append(f'<div class="translation">{languages["translation"]}</div>')
    rest = f'<span class="rest"> · {overview["summary"]}</span>' if overview["summary"] else ""
    return (f'<details class="formats-info"><summary>Video formats{rest}</summary>\n'
            + "".join(parts) + "</details>")


def about_video_file(info: dict, video_files: list[str] | None = None,
                     video_size: int | None = None) -> str:
    """The "Video file" row: a video file found next to the page when it was
    built (the first one, with its size), else "not downloaded"; then how the
    list of formats came about (Deno, signed in). The planned default shows
    in the tables (marked) and in --video-formats, not here."""
    overview = video_overview(info)
    if video_files:
        found = (f"a video file next to the page: {esc(video_files[0])}"
                 + (f", {esc(human_size(video_size).lstrip('~'))}" if video_size else ""))
    else:
        found = "not downloaded"
    if overview is None:
        return f"{found}; {NO_FORMATS}"
    text = f"{found}; Deno at download: {overview['deno']}"
    if overview["signed_in"]:
        text += "; signed in, so YouTube offered no HLS formats"
    return text


def named_code(code: str) -> str:
    return "unknown language" if code == UNKNOWN_LANGUAGE else code


def format_list(info: dict, saved: bool = False) -> list[str]:
    """The lines --video-formats prints for one video: the qualities a
    download can choose (one per line: quality, parameter, codec, size with
    the original audio, the default marked), then the audio: the track in
    the video and those a download can add (m4a, aac), by role. `saved`: the
    data comes from a saved file."""
    head = f"{info.get('title') or info.get('id')} [{info.get('id')}]"
    if saved and info.get(ARCHIVED_KEY):
        head += f" · saved data of {dt.date.fromisoformat(info[ARCHIVED_KEY]).strftime(DATE_FORMAT)}"
    lines = [head]
    overview = video_overview(info)
    if overview is None:
        return lines + [f"{NO_FORMATS}: " + ("YouTube sent none when the data was saved; make "
                                             "the page again with --overwrite" if saved
                                             else f"{NO_FORMATS_WHY}; try again later")]
    variants = overview["variants"]
    if variants:
        audio = overview["image"]["audio"]
        lines.append("image (mp4, AV1 else H.264" + (f"; sizes include the audio: {audio}" if audio
                                                     else "") + "):")
        widths = [max(len(v[key]) for v in variants) for key in ("quality", "parameter",
                                                                  "codec", "size")]
        for i, v in enumerate(variants):
            default = "  (default)" if i == 0 and overview["image"]["picks"] else ""
            lines.append(f"  {v['quality']:<{widths[0]}}  {'(' + v['parameter'] + ')':<{widths[1] + 2}}"
                         f"  {v['codec']:<{widths[2]}}  {v['size']:>{widths[3]}}{default}")
    else:
        lines.append("image: nothing a download can take (no av1 or h264 up to 1080p)")
    in_video, extra, sizes = overview["in_video"], overview["extra_audio"], overview["extra_sizes"]
    if not in_video:  # no main audio: then no ordinary track to add either
        return lines + ["audio: none"]
    each = None
    if sizes:  # one size, or a range: "~60–70 MB", "~900 MB–1.1 GB"
        low, high = sizes[0], sizes[-1]
        same_unit = low.split()[-1] == high.split()[-1]
        each = low if low == high else (
            f"{low.split()[0] if same_unit else low}–{high.lstrip('~')}")
    # Like the image: container and codec of what is in the video, then after
    # a semicolon the extra tracks a download can add (m4a, aac), if any. A
    # track a download can add is an ordinary one, so there is a main audio.
    header = f"mp4, {overview['in_video_codec']}"
    if extra:
        header += "; extra tracks m4a, aac" + (f", {each} each" if each else "")
    lines.append(f"audio ({header}):")
    lines.append(f"  in the video: {named_code(in_video[0])}"
                 + (f" ({in_video[1]})" if in_video[1] else ""))
    for role in dict.fromkeys(role for _, role in extra):
        lines.append(f"  {role or 'tracks'}: "
                     + ", ".join(named_code(code) for code, r in extra if r == role))
    return lines


def list_formats(args: argparse.Namespace) -> int:
    """--video-formats: for each source, the formats a download can take,
    printed; no page is made. Playlists and channels are not listed."""
    failed = 0
    for n, source in enumerate(args.sources):
        if n:
            print()
        try:
            if args.from_json:
                info = json.loads(saved_data_path(source, Path(args.output_dir))
                                  .read_text(encoding="utf-8"))
            elif is_channel_url(source) or playlist_id_from_url(source):
                raise ValueError("--video-formats lists single videos, not playlists or "
                                 "channels")
            else:
                info = fetch_formats(source, args)
            if info.get("_type") in ("playlist", "multi_video"):
                raise ValueError("--video-formats lists single videos, not playlists")
        except yt_dlp.utils.DownloadError:
            log(f"FAILED: {source}")  # yt-dlp has already printed the reason
            failed += 1
            continue
        except Exception as error:
            log(f"FAILED: {source}: {error}")
            failed += 1
            continue
        print("\n".join(format_list(info, saved=args.from_json)), flush=True)
        if not args.from_json and not info[DOWNLOAD_KEY]["deno"]:
            log("Note: no JavaScript runtime (Deno) found, so YouTube may have offered "
                "more formats than listed.")
    return 1 if failed else 0


def render_about(info: dict, archived: dt.date, thumbnail: str | None,
                 avatar_uris: dict[str, str], extra_images: dict[str, str], spec: list,
                 theme: str, show_snapshot: bool, no_avatars: bool, from_json: bool,
                 built: dt.datetime, video_files: list[str] | None = None,
                 video_size: int | None = None) -> str:
    """"About this copy": how this copy was made, what worked and what did not.
    Every row is always there, in a fixed order (see docs/DEVELOPMENT.md)."""
    rows = [
        ("Downloaded", about_downloaded(info, archived)),
        ("Page built", about_page_built(from_json, built)),
        ("Playlist", about_playlist(info)),
        ("Command", about_command(info, spec, theme, show_snapshot, no_avatars)),
        ("Video page", about_video_page(info)),
        ("Markers", about_markers(info)),
        ("Info cards", about_cards(info)),
        ("Transcript", about_transcript(info, spec)),
        ("Comments", about_comments(info)),
        ("Images", about_images(info, thumbnail, avatar_uris, extra_images, no_avatars)),
        ("Video file", about_video_file(info, video_files, video_size)),
        ("Software", about_software()),
        ("Disclaimer", esc(DISCLAIMER)),
    ]
    items = "\n".join(f"<dt>{name}</dt><dd>{value}</dd>" for name, value in rows)
    return ('<details class="copy-info"><summary>About this copy<span class="rest"> · '
            f'Snapshot {archived.strftime(DATE_FORMAT)} · made with yt2html</span></summary>\n'
            f'<dl>\n{items}\n</dl></details>')


def channel_avatar_url(info: dict) -> str | None:
    """The channel's avatar: from the watch page, else from a comment of the
    uploader (pages made from older JSON files, or an unreadable watch page)."""
    return info.get(CHANNEL_AVATAR_KEY) or next(
        (c.get("author_thumbnail") for c in info.get("comments") or []
         if c.get("author_is_uploader") and c.get("author_thumbnail")), None)


def comment_avatar_urls(info: dict) -> set[str]:
    """The avatars of the comment authors (the channel's own is apart: it stays
    with --no-avatars). The same set is downloaded and counted in "About this copy"."""
    return {c.get("author_thumbnail") for c in info.get("comments") or []} - {None, ""}


def avatar_urls(info: dict, comments: bool = True) -> set[str]:
    """The avatars to download: the channel's, and the comment authors' unless
    left out (--no-avatars)."""
    urls = comment_avatar_urls(info) if comments else set()
    return {url for url in urls | {channel_avatar_url(info)} if url}


def render_page(info: dict, thumbnail: str | None, avatar_uris: dict[str, str],
                archived: dt.date, show_snapshot: bool = True,
                transcript_spec: list | None = None,
                extra_images: dict[str, str] | None = None,
                theme: str = "auto", no_avatars: bool = False,
                from_json: bool = False, built: dt.datetime | None = None,
                video_files: list[str] | None = None, video_size: int | None = None) -> str:
    """The whole HTML page. Images come as data URIs: `thumbnail`,
    `avatar_uris` and `extra_images` ({image url: data URI}). `no_avatars`,
    `from_json` and `built` (the build time, default now) are only described
    in "About this copy"."""
    video_id = info.get("id") or ""
    title = info.get("title") or video_id
    duration = info.get("duration")
    url = info.get("webpage_url") or f"{YOUTUBE}/watch?v={video_id}"
    uploader = info.get("uploader") or info.get("channel") or "(unknown channel)"
    channel_url = info.get("uploader_url") or info.get("channel_url")
    comments = info.get("comments") or []
    avatars = AvatarStyles(avatar_uris)

    channel_avatar = channel_avatar_url(info)

    body = ['<main class="page">']
    if thumbnail:
        body.append(f'<img class="thumbnail" src="{thumbnail}" alt="">')
    else:
        body.append('<div class="thumbnail missing">Thumbnail not available</div>')

    body.append(f'<h1 class="title">{link(url, title)}</h1>')

    channel_name = link(channel_url, uploader) if channel_url else esc(uploader)
    verified = ' <span class="badge" title="Verified">✓</span>' if info.get("channel_is_verified") else ""
    subs = info.get("channel_follower_count")
    subs_text = f"{format_compact(subs)} subscribers" if subs is not None else ""
    body.append('<div class="channel">' + avatars.html(channel_avatar, uploader)
                + f'<div><div class="name">{channel_name}{verified}</div>'
                + f'<div class="subs">{esc(subs_text)}</div></div></div>')

    body.append('<div class="facts">' + PLAYER_TILE
                + fact("Duration", format_duration(duration))
                + fact("Published", published_date(info))
                + fact("Views", format_int(info.get("view_count")))
                + fact("Likes", format_int(info.get("like_count")))
                + fact("Comments", youtube_comment_count(info)[0] or "—")
                + (fact("Snapshot", archived.strftime(DATE_FORMAT), extra=True)
                   if show_snapshot else "")
                + "</div>")

    description = info.get("description") or ""
    if description.strip():
        body.append(f'<section class="description">{linkify(description, video_id, duration)}</section>')
    else:
        body.append('<section class="description empty">No description.</section>')

    # YouTube's order: summary, chapters, people mentioned, podcast, transcript.
    extras = info.get(EXTRAS_KEY) or []
    before = [e for e in extras if e.get("type") == "video_summary"]
    after = [e for e in extras if e.get("type") != "video_summary"]
    if before:
        body.append(render_description_extras(before, video_id, duration, extra_images))

    markers = marker_lists_of(info)
    if markers:
        body.append(render_marker_lists(markers, video_id))

    cards = info.get(CARDS_KEY)
    if cards:
        body.append(render_cards(cards, video_id, extra_images or {}))

    if after:
        body.append(render_description_extras(after, video_id, duration, extra_images))

    transcript = info.get(TRANSCRIPT_KEY)
    spec = [("plain", None)] if transcript_spec is None else transcript_spec
    if transcript and spec:  # "none" (an empty spec) also hides a stored one
        chapters = next((m.get("items") or [] for m in markers if m.get("id") == "chapters"), [])
        body.extend(render_transcripts(transcript, chapters, video_id, spec))

    license_info = license_of(info)
    if license_info:
        body.append(render_license(license_info))

    if "comments" in info:
        roots, children = build_comment_tree(comments)
        replies = len(comments) - len(roots)
        body.append('<section class="comments">')
        count_text, expected = youtube_comment_count(info)
        shown = (f" (YouTube shows {count_text})"
                 if expected is not None and expected != len(comments) else "")
        # A download cut short by --max-comments must not claim "No comments."
        limit = info.get(LIMIT_KEY)
        limited = (limit is not None and len(comments) >= limit
                   and (expected is None or expected > len(comments)))
        body.append("<h2>Comments</h2>")
        body.append(f'<div class="note">{format_int(len(comments))} downloaded{shown}: '
                    f'{format_int(len(roots))} threads, {format_int(replies)} replies · '
                    f'oldest first · comment dates are relative to '
                    f'{archived.strftime(DATE_FORMAT)}</div>')
        if info.get("comments") is None:
            body.append('<div class="note">Comments are turned off or unavailable.</div>')
        elif not comments and not limited:
            body.append('<div class="note">No comments.</div>')
        for c in roots:
            body.append(render_comment(c, children, avatars, video_id, duration, uploader))
        body.append("</section>")

    body.append(render_video_formats(info))
    body.append(render_about(info, archived, thumbnail, avatar_uris, extra_images or {},
                             spec, theme, show_snapshot, no_avatars, from_json,
                             built or dt.datetime.now(), video_files, video_size))
    body.append("</main>")
    overview = video_overview(info)
    in_video = overview["in_video"] if overview else None
    body.insert(0, render_player(video_names(info), video_files, player_qualities(info),
                                 in_video[0] if in_video and in_video[0] != UNKNOWN_LANGUAGE
                                 else None))
    body.append(f"<script>{PLAYER_JS}</script>")

    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta name="generator" content="yt2html {__version__} (yt-video-archiver)">',
        f'<meta name="source-url" content="{esc(url)}">',
        *(f'<meta name="{name}" content="{esc(value)}">'
          for name, value in page_metadata(info, archived).items()),
        f"<title>{esc(title)}</title>",
        f'<meta name="color-scheme" content="{COLOR_SCHEMES.get(theme, "dark")}">',
        # The body is already rendered here, so every avatar class is known.
        f"<style>{CSS}\n{theme_css(theme)}\n{avatars.css()}\n</style>",
        "</head>",
        "<body>",
        *body,
        "</body>",
        "</html>",
        "",
    ])


# --------------------------------------------------------------------------
# Index of a playlist or a list file (--index, --index-only)
# --------------------------------------------------------------------------
#
# The index is always built from the list (playlist entries or the lines of
# the list file) and the pages in the output folder, never from a log: pages
# are found by "[VIDEO_ID].html", so renamed pages keep their links, and the
# original titles come from the pages' metadata. The list itself is kept in
# the index (data-* attributes), so --index-only can rebuild it offline.

class Run:
    """What this run learned: playlists read, videos tried, failures."""

    def __init__(self):
        self.playlists: dict[str, dict] = {}  # playlist id -> section
        self.attempted: set[str] = set()      # video ids tried in this run
        self.failures: dict[str, str] = {}    # video id -> short reason


UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]"}


def playlist_entry(entry: dict) -> dict | None:
    """One flat playlist entry as an index entry; None if it is no video."""
    video_id = entry_video_id(entry)
    if not video_id:
        return None
    title = entry.get("title") or ""
    return {"id": video_id, "url": f"{YOUTUBE}/watch?v={video_id}", "title": title,
            "unavailable": title in UNAVAILABLE_TITLES or entry.get("availability") == "private",
            "status": "", "reason": ""}


def failure_reason(error: Exception) -> str:
    """A short reason for the index, e.g. "Private video. Sign in ..."."""
    text = str(error).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", text)  # "[youtube] VIDEO_ID: "
    return (text.splitlines() or ["unknown error"])[0][:300]


class IndexReader(html.parser.HTMLParser):
    """The list kept in an index page: sections with their entries."""

    def __init__(self):
        super().__init__()
        self.sections = []

    def handle_starttag(self, tag, attrs):
        data = {name: value or "" for name, value in attrs}
        if tag == "section" and "data-section" in data:
            self.sections.append({
                "playlist_id": data.get("data-playlist-id", ""),
                "title": data.get("data-title", ""), "url": data.get("data-url", ""),
                "fetched": data.get("data-fetched", ""),
                "unread": data.get("data-unread") == "1", "entries": []})
        elif tag == "tr" and "data-url" in data and self.sections:
            status = data.get("data-status", "")
            self.sections[-1]["entries"].append({
                "id": data.get("data-id", ""), "url": data["data-url"],
                "title": data.get("data-title", ""), "unavailable": status == "unavailable",
                "status": status, "reason": data.get("data-reason", "")})


def read_index(path: Path | None) -> list[dict]:
    """Sections of an earlier index ([] if there is none or it is unreadable)."""
    if not path or not path.is_file():
        return []
    reader = IndexReader()
    try:
        reader.feed(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []
    return reader.sections


def entry_status(entry: dict, page: Path | None, run: Run) -> tuple[str, str]:
    """(status, reason) of one index entry: archived, unavailable, failed,
    not-a-video or pending. A reason from an earlier run is kept as long as
    the video has no page and was not tried again."""
    video_id = entry.get("id")
    if page:
        return "archived", ""
    if not video_id:
        return "not-a-video", entry.get("reason") or ""
    reason = run.failures.get(video_id, "")
    if entry.get("unavailable"):
        return "unavailable", reason or entry.get("reason") or ""
    if video_id in run.failures:
        return "failed", reason
    if video_id not in run.attempted and entry.get("status") in ("failed", "unavailable"):
        return entry["status"], entry.get("reason") or ""
    return "pending", ""


STATUS_TEXT = {"unavailable": "Unavailable", "failed": "Failed",
               "not-a-video": "Not a video", "pending": "Not archived yet"}


def comments_summary(meta: dict[str, str]) -> str:
    """The small line under a title: how many comments, and whether all."""
    status = meta.get("yt2html:comments-status")
    if status == "not-downloaded":
        return "Comments not downloaded"
    if status == "turned-off":
        return "Comments turned off"
    try:
        count = int(meta.get("yt2html:comments-downloaded") or 0)
    except ValueError:
        return ""
    on_youtube = meta.get("yt2html:comments-youtube") or ""
    noun = "comment" if count == 1 else "comments"
    if status == "complete":
        shown = f" (YouTube: {on_youtube})" if on_youtube and on_youtube != format_int(count) else ""
        return f"{format_int(count)} {noun} · complete{shown}"
    if status in ("limited", "interrupted"):
        of = f" of {on_youtube}" if on_youtube else ""
        return f"{format_int(count)}{of} comments · {status}"
    return ""


def render_index_rows(entries: list[dict], output_dir: Path, run: Run,
                      first: int = 1) -> tuple[list[str], int, int]:
    """Table rows of one section, numbered from `first`, the number of
    entries with a page and the number of videos (entries that are videos)."""
    rows, archived, videos = [], 0, 0
    for position, entry in enumerate(entries, first):
        videos += bool(entry.get("id"))
        page = existing_page(output_dir, entry.get("id"))
        meta = read_page_metadata(page) if page else {}
        status, reason = entry_status(entry, page, run)
        title = meta.get("yt2html:title") or entry.get("title") or ""
        data = {"data-id": entry.get("id") or "", "data-url": entry["url"],
                "data-title": title, "data-status": status, "data-reason": reason}
        attributes = " ".join(f'{name}="{esc(value)}"' for name, value in data.items() if value
                              or name == "data-url")
        shown_title = esc(title or entry["url"])
        if page:
            archived += 1
            name = (f'<a href="{esc(urllib.parse.quote(page.name))}">{shown_title}</a>'
                    + (f'<div class="sub">{esc(comments_summary(meta))}</div>'
                       if comments_summary(meta) else ""))
            try:
                when = dt.date.fromisoformat(meta.get("yt2html:archived") or "").strftime(DATE_FORMAT)
            except ValueError:
                when = ""
            date = f'<td class="date">{when}</td>'
            row_class = "item"
        else:
            name = shown_title
            hint = f' title="{esc(reason)}"' if reason else ""
            date = f'<td class="date reason"{hint}>{STATUS_TEXT[status]}</td>'
            row_class = "item missing"
        host = urllib.parse.urlsplit(entry["url"]).netloc
        youtube = (link(entry["url"], "YouTube" if "youtu" in host else "Link")
                   if entry["url"].startswith("http") else "")
        rows.append(f'<tr class="{row_class}" {attributes}><td class="pos">{position}</td>'
                    f'<td class="name">{name}</td>{date}<td class="yt">{youtube}</td></tr>')
    return rows, archived, videos


def render_index(title: str, url: str | None, sections: list[dict], output_dir: Path,
                 run: Run, kind: str, theme: str = "auto",
                 today: dt.date | None = None) -> tuple[str, int, int]:
    """The index page and its counts (entries with a page, all entries).

    A playlist index has one section and no section heading; a list file's
    index has a section per playlist in it (with a heading) and one for each
    run of plain video URLs between them. Playlist rows are numbered by
    their position in the playlist, the other rows continuously."""
    today = today or dt.date.today()
    body, archived, total, plain = [], 0, 0, 1
    for section in sections:
        in_playlist = bool(section.get("playlist_id"))
        rows, done, videos = render_index_rows(section["entries"], output_dir, run,
                                               1 if in_playlist else plain)
        if not in_playlist:
            plain += len(rows)
        archived, total = archived + done, total + videos
        data = {"data-section": "1", "data-playlist-id": section.get("playlist_id") or "",
                "data-title": section.get("title") or "", "data-url": section.get("url") or "",
                "data-fetched": section.get("fetched") or "",
                "data-unread": "1" if section.get("unread") else ""}
        attributes = " ".join(f'{name}="{esc(value)}"' for name, value in data.items()
                              if value or name == "data-section")
        part = [f'<section class="index-section" {attributes}>']
        if kind == "list" and section.get("playlist_id"):
            heading = link(section["url"], section.get("title") or section["url"])
            note = ("not read yet" if section.get("unread")
                    else f"playlist · {plural(len(rows), 'video')}")
            part.append(f'<h2>{heading}{note_html(note)}</h2>')
        if rows:
            part.append('<table class="index-table"><tbody>')
            part.extend(rows)
            part.append("</tbody></table>")
        part.append("</section>")
        body.extend(part)
    heading = link(url, title) if url else esc(title)
    updated = today.strftime(DATE_FORMAT)
    page = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta name="generator" content="yt2html {__version__} (yt-video-archiver)">',
        f'<meta name="yt2html:index" content="{esc(kind)}">',
        f"<title>{esc(title)}</title>",
        f'<meta name="color-scheme" content="{COLOR_SCHEMES.get(theme, "dark")}">',
        f"<style>{CSS}\n{theme_css(theme)}\n</style>",
        "</head>",
        "<body>",
        '<main class="page index">',
        f'<h1 class="title">{heading}</h1>',
        f'<div class="index-meta">{archived} of {plural(total, "video")} archived · '
        f'Updated {updated}</div>',
        *body,
        f'<footer class="footer">Index made with yt2html on {updated}.</footer>',
        "</main>",
        "</body>",
        "</html>",
        "",
    ])
    return page, archived, total


def with_earlier_status(section: dict, earlier: list[dict]) -> dict:
    """The section with the status and reason each video had in the earlier
    index, so a reason survives runs in which the video is not tried again."""
    old = {e["id"]: e for s in earlier for e in s["entries"] if e.get("id")}
    entries = []
    for entry in section["entries"]:
        previous = old.get(entry["id"], {})
        entries.append({**entry, "status": entry.get("status") or previous.get("status", ""),
                        "reason": entry.get("reason") or previous.get("reason", "")})
    return {**section, "entries": entries}


def playlist_section(playlist_id: str, args: argparse.Namespace, run: Run,
                     earlier: list[dict]) -> dict:
    """A playlist's section: read in this run, else from an earlier index;
    with --index-only and no earlier index, the list is read from YouTube
    (only the list, no video). Unread playlists get an empty section."""
    if playlist_id in run.playlists:
        return with_earlier_status(run.playlists[playlist_id], earlier)
    for section in earlier:
        if section.get("playlist_id") == playlist_id and not section.get("unread"):
            return section
    url = f"{YOUTUBE}/playlist?list={playlist_id}"
    if args.index_only:
        try:
            read_playlist(url, args, run)
            return with_earlier_status(run.playlists[playlist_id], earlier)
        except Exception as error:
            log(f"FAILED: {url}: {failure_reason(error)}")
    return {"playlist_id": playlist_id, "title": "", "url": url, "fetched": "",
            "unread": True, "entries": []}


def list_file_sections(lines: list[str], args: argparse.Namespace, run: Run,
                       earlier: list[dict]) -> list[dict]:
    """Sections of a list file's index, in the order of its lines: one per
    playlist, one per run of other lines. A video listed twice (also in
    another URL form) appears once."""
    known = {entry["id"] or entry["url"]: entry
             for section in earlier if not section.get("playlist_id")
             for entry in section["entries"]}
    sections, current, seen = [], None, set()
    for line in lines:
        playlist_id = playlist_id_from_url(line)
        if playlist_id:
            if playlist_id not in seen:
                seen.add(playlist_id)
                sections.append(playlist_section(playlist_id, args, run, earlier))
            current = None
            continue
        video_id = video_id_from_url(line)
        key = video_id or line
        if key in seen:
            continue
        seen.add(key)
        if current is None:
            current = {"playlist_id": "", "title": "", "url": "", "fetched": "", "entries": []}
            sections.append(current)
        previous = known.get(key, {})
        reason = "" if video_id else ("a channel URL" if is_channel_url(line)
                                      else "not a YouTube video URL")
        current["entries"].append({
            "id": video_id or "", "url": f"{YOUTUBE}/watch?v={video_id}" if video_id else line,
            "title": previous.get("title", ""), "unavailable": False,
            "status": previous.get("status", ""), "reason": previous.get("reason") or reason})
    return sections


def write_index(path: Path, title: str, url: str | None, sections: list[dict],
                kind: str, args: argparse.Namespace, run: Run) -> None:
    page, archived, total = render_index(title, url, sections, Path(args.output_dir), run,
                                         kind, args.theme)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, page)
    log(f"Saved index: {path} ({archived} of {plural(total, 'video')} archived)")


def write_indexes(args: argparse.Namespace, run: Run) -> int:
    """Write the index of each playlist given in the command and of each
    list file; returns the number of indexes that could not be written."""
    output_dir = Path(args.output_dir)
    failed = 0
    for source in args.command_sources:
        playlist_id = playlist_id_from_url(source)
        if not playlist_id:
            continue
        try:
            found = existing_page(output_dir, playlist_id)
            section = playlist_section(playlist_id, args, run, read_index(found))
            if section.get("unread"):
                log(f"No index for {source}: the playlist could not be read.")
                continue
            title = section.get("title") or playlist_id
            path = found or output_path({"title": title, "id": playlist_id}, output_dir)
            write_index(path, title, section["url"], [section], "playlist", args, run)
        except Exception as error:
            failed += 1
            log(f"FAILED: index for {source}: {error}")
    for list_file, lines in args.batch_lines.items():
        try:
            path = output_dir / (Path(list_file).stem + ".html")
            sections = list_file_sections(lines, args, run, read_index(path))
            write_index(path, Path(list_file).name, None, sections, "list", args, run)
        except Exception as error:
            failed += 1
            log(f"FAILED: index for {list_file}: {error}")
    return failed


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

_NAMING_YDL = None  # one quiet instance, only for building file names


def output_path(info: dict, output_dir: Path) -> Path:
    """File name built by yt-dlp's own sanitizer, so it matches the video file."""
    global _NAMING_YDL
    if _NAMING_YDL is None:
        _NAMING_YDL = yt_dlp.YoutubeDL({"quiet": True})
    name = _NAMING_YDL.prepare_filename({**info, "ext": "html"}, outtmpl=OUTPUT_TEMPLATE)
    return output_dir / Path(name).name


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def video_id_from_url(url: str) -> str | None:
    """The 11-character video id of a YouTube video URL, without any request.

    Works for watch?v=, youtu.be/, shorts/ and bare ids; None if the URL is
    not a single video (or not recognised), e.g. a playlist.
    """
    from yt_dlp.extractor.youtube import YoutubeIE
    try:
        if YoutubeIE.suitable(url):
            return YoutubeIE.extract_id(url)
    except Exception:
        pass
    # yt-dlp leaves watch URLs with &list= to its playlist extractor; the
    # video id is still the v= parameter.
    parts = urllib.parse.urlsplit(url)
    if "youtube" in parts.netloc:
        video = urllib.parse.parse_qs(parts.query).get("v", [""])[0]
        if VIDEO_ID.fullmatch(video):
            return video
    return None


def playlist_id_from_url(url: str) -> str | None:
    """The id of a YouTube playlist URL (/playlist?list=...) or of a bare
    playlist id, without any request. None for anything else; a watch URL
    with &list= is a video, as in yt-dlp."""
    from yt_dlp.extractor.youtube import YoutubePlaylistIE
    if video_id_from_url(url):
        return None
    try:
        if "/" not in url and YoutubePlaylistIE.suitable(url):
            return url
    except Exception:
        return None
    parts = urllib.parse.urlsplit(url)
    if "youtube" in parts.netloc and parts.path.rstrip("/") == "/playlist":
        return urllib.parse.parse_qs(parts.query).get("list", [None])[0]
    return None


BRACKETED = re.compile(r"\[([A-Za-z0-9_-]+)\]")


def id_in_name(text: str, playlists: bool = True) -> str | None:
    """The video id (or, with `playlists`, the playlist id) a file name carries
    in square brackets, anywhere in the name, whatever its extension: the last
    bracketed part that is a valid id ("Song [Remix] [ID] (copy).mp4" -> ID).
    Only the file name counts, not its folders."""
    name = re.split(r"[\\/]", text.strip())[-1]
    for found in reversed(BRACKETED.findall(name)):
        if VIDEO_ID.fullmatch(found) or (playlists and playlist_id_from_url(found)):
            return found
    return None


def source_from_page_name(text: str) -> str:
    """A file name with a video or playlist id in square brackets (a page, an
    index, a video, saved data: "Title [VIDEO_ID].html", "Title [ID].mp4",
    "Title [PLAYLIST_ID]") as the URL of that video or playlist; anything else
    unchanged. A bare id is already understood by yt-dlp."""
    found = None if "://" in text else id_in_name(text)
    if not found:
        return text
    if VIDEO_ID.fullmatch(found):
        return f"{YOUTUBE}/watch?v={found}"
    return f"{YOUTUBE}/playlist?list={found}"


def is_channel_url(url: str) -> bool:
    """True for a YouTube channel URL (@handle, /channel/..., /c/... and
    their tabs), recognised without any request."""
    from yt_dlp.extractor.youtube import YoutubeTabIE
    if video_id_from_url(url) or playlist_id_from_url(url):
        return False
    try:
        return YoutubeTabIE.suitable(url)
    except Exception:
        return False


def entry_video_id(entry: dict) -> str | None:
    """The video id of one entry of a flat playlist, None if it is not a video
    (e.g. a nested playlist or a channel)."""
    video_id = entry.get("id") or ""
    if VIDEO_ID.fullmatch(video_id):
        return video_id
    return video_id_from_url(entry.get("url") or "")


def fetch_playlist(url: str, args: argparse.Namespace) -> tuple[str, list[dict]]:
    """Title and flat entries of a playlist: only the list of its videos, in
    playlist order, none of them extracted (one request per ~100 videos)."""
    opts = {"skip_download": True, "extract_flat": "in_playlist", "noprogress": True}
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies_from_browser)
    with yt_dlp.YoutubeDL(opts) as ydl:
        # process=False: the entries are read as they are, without yt-dlp's
        # "Downloading item N of M" line for each of them.
        info = ydl.extract_info(url, download=False, process=False)
        for _ in range(3):  # follow a redirect to the real playlist
            if info.get("_type") not in ("url", "url_transparent"):
                break
            info = ydl.extract_info(info["url"], download=False, process=False,
                                    ie_key=info.get("ie_key"))
        if info.get("_type") != "playlist":
            raise ValueError("this URL does not lead to a playlist")
        entries = list(info.get("entries") or [])
    return info.get("title") or info.get("id") or url, entries


def read_playlist(source: str, args: argparse.Namespace, run: Run) -> dict:
    """Read a playlist's list (no video) and keep it in `run` for the index."""
    log(f"Fetching playlist {source}")
    title, entries = fetch_playlist(source, args)
    playlist_id = playlist_id_from_url(source) or source
    section = {"playlist_id": playlist_id, "title": title,
               "url": f"{YOUTUBE}/playlist?list={playlist_id}",
               "fetched": dt.date.today().isoformat(), "unread": False,
               "entries": [e for e in map(playlist_entry, entries) if e]}
    run.playlists[playlist_id] = section
    return section


def playlist_videos(source: str, args: argparse.Namespace, seen: set[str],
                    run: Run) -> list[str]:
    """Watch URLs of the videos of a playlist, without those already in `seen`
    (videos given earlier in this run or twice in the playlist); adds the
    new ones to `seen`."""
    section = read_playlist(source, args, run)
    urls = []
    for entry in section["entries"]:
        if entry["id"] not in seen:
            seen.add(entry["id"])
            urls.append(entry["url"])
    listed = len(section["entries"])
    duplicates = listed - len(urls)
    note = f" ({plural(duplicates, 'duplicate')} ignored)" if duplicates else ""
    log(f'Playlist "{section["title"]}": {plural(listed, "video")}{note}.')
    return urls


def playlist_context(run: Run, source: str, url: str) -> dict | None:
    """The playlist a video comes from, for "About this copy": its id, title,
    the video's position (first occurrence) and the number of videos."""
    section = run.playlists.get(playlist_id_from_url(source) or source)
    video_id = video_id_from_url(url)
    if not section:
        return None
    entries = section["entries"]
    position = next((i for i, e in enumerate(entries, 1) if e["id"] == video_id), None)
    return {"id": section["playlist_id"], "title": section["title"],
            "position": position, "count": len(entries)}


VIDEO_FILE_EXTENSIONS = tuple(f".{ext}" for ext in VIDEO_EXTENSIONS)
_SEVERAL_NOTED: set[tuple] = set()


def files_with_id(folder: Path, ident: str | None, extensions: tuple[str, ...]) -> list[Path]:
    """Files in `folder` (not its subfolders) whose name contains "[ident]"
    anywhere and ends with one of `extensions` (any case); the rest of the name
    does not matter. Sorted by name."""
    if not ident or not folder.is_dir():
        return []
    mark = f"[{ident}]"
    return [p for p in sorted(folder.iterdir())
            if p.is_file() and mark in p.name and p.name.lower().endswith(extensions)]


def file_with_id(folder: Path, ident: str | None, extensions: tuple[str, ...],
                 what: str) -> Path | None:
    """The first of files_with_id(); several of them get a note (once)."""
    found = files_with_id(folder, ident, extensions)
    key = (str(folder), ident, extensions)
    if len(found) > 1 and key not in _SEVERAL_NOTED:
        _SEVERAL_NOTED.add(key)
        log(f"Note: several {what} for [{ident}] in {folder}: "
            + ", ".join(p.name for p in found) + f"; using {found[0].name}")
    return found[0] if found else None


def existing_page(output_dir: Path, video_id: str | None) -> Path | None:
    """A page (or a playlist's index) for this id in the output folder, not its
    subfolders: any ".html" file with "[id]" in its name, so pages may be
    renamed freely as long as that part stays."""
    return file_with_id(output_dir, video_id, (".html",), "pages")


def write_atomically(path: Path, text: str) -> None:
    """Write to a temporary file and rename it, so an interrupted run never
    leaves a cut-off page that a later run would take as finished."""
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


class CommentsInterrupted(KeyboardInterrupt):
    """Ctrl+C while yt-dlp downloaded the comments: yt-dlp stops only the
    comments, the page is made from those fetched so far, then the run stops."""


def comments_interrupted(info: dict) -> bool:
    """yt-dlp keeps the comments fetched before Ctrl+C but sets
    comment_count to None (it is also None when comments are disabled, but
    then there is no comment list)."""
    return isinstance(info.get("comments"), list) and info.get("comment_count") is None


def narrow_saved_comments(info: dict, args: argparse.Namespace) -> None:
    """--no-comments and --max-comments with --from-json: they can only narrow
    the saved comments, as if the download had used them. The first N are
    kept, in the saved (yt-dlp's) order, replies counted: the comments a
    download with that limit gets. The page then shows the result only."""
    if args.no_comments:
        info.pop("comments", None)
        return
    comments = info.get("comments")
    limit = args.max_comments
    if limit is None or not isinstance(comments, list):
        return
    if len(comments) > limit:
        info["comments"] = comments[:limit]
        info[LIMIT_KEY] = limit
        info["comment_count"] = limit  # cut by the limit, not by Ctrl+C any more
    elif comment_status(info) in ("limited", "interrupted"):
        log(f"  The saved data has only {plural(len(comments), 'comment')}; download "
            f"again (without --from-json) to get more.")


PLAYLIST_ERROR = "this URL leads to a playlist or channel, not a single video"
CHANNEL_ERROR = ("this is a channel URL; only videos and playlists are supported "
                 "(open the channel's playlist and use its URL)")


def archive(source: str, args: argparse.Namespace, playlist: dict | None = None) -> Path | None:
    """Make the page for one URL (or one .info.json file).

    Returns the page's path, or None if the video already has a page in the
    output folder and --overwrite was not given (with --from-json the page is
    always made again). Everything that can fail for a reason the user should
    know (playlist URL, missing translation) is checked before anything is
    written.
    """
    output_dir = Path(args.output_dir)
    skip_existing = not (args.overwrite or args.from_json)

    if skip_existing:
        # Checked before fetching, from the id in the URL: no request needed.
        found = existing_page(output_dir, video_id_from_url(source))
        if found:
            log(f"Skipped (page exists): {found}")
            return None

    if not args.from_json and is_channel_url(source):
        raise ValueError(CHANNEL_ERROR)
    if args.from_json:
        path = saved_data_path(source, output_dir)
        log(f"Reading {path}")
        info = json.loads(path.read_text(encoding="utf-8"))
    else:
        log(f"Fetching {source}")
        info = fetch_info(source, args)
        info[PLAYLIST_KEY] = playlist

    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError(PLAYLIST_ERROR)
    if args.from_json:
        narrow_saved_comments(info, args)

    available = translations_of(info.get(TRANSCRIPT_KEY) or {})
    for language in bilingual_languages(args.transcript):
        if language not in available:
            raise TranscriptError(f"no transcript translation into '{language}' "
                                  f"in the data; nothing was saved.")

    # An existing page (possibly renamed by the user) keeps its name when it
    # is made again; otherwise the name follows yt-dlp's video file name.
    found = existing_page(output_dir, info.get("id"))
    if found and skip_existing:
        # The URL did not show the id (unusual form), the fetched data does.
        log(f"Skipped (page exists): {found}")
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    target = found or output_path(info, output_dir)

    if args.save_json and not args.from_json:
        json_path = target.with_suffix(".info.json")
        json_path.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"Saved metadata: {json_path}")

    log("Downloading thumbnail...")
    thumbnail = fetch_thumbnail(info)
    if not thumbnail:
        log("  Thumbnail could not be downloaded.")
    avatar_uris = fetch_images(avatar_urls(info, comments=not args.no_avatars), "avatars",
                               resize=resize_avatar_url)
    if args.no_cards:
        info.pop(CARDS_KEY, None)  # also cards kept in a --from-json file
    extra_images = fetch_images(extra_image_urls(info), "images (people, podcast, cards)")

    try:
        archived = dt.date.fromisoformat(info.get(ARCHIVED_KEY) or "")
    except ValueError:
        archived = dt.date.today()  # older JSON files without the date
    video_files = video_files_for(info, output_dir)
    video_size = (output_dir / video_files[0]).stat().st_size if video_files else None
    page = render_page(info, thumbnail, avatar_uris, archived,
                       show_snapshot=not args.no_snapshot,
                       theme=args.theme,
                       transcript_spec=args.transcript,
                       extra_images=extra_images,
                       no_avatars=args.no_avatars, from_json=args.from_json,
                       video_files=video_files, video_size=video_size)
    write_atomically(target, page)
    size_kb = target.stat().st_size / 1024
    log(f"{'Replaced' if found else 'Saved'} page: {target} ({size_kb:,.0f} KB, "
        f"{len(info.get('comments') or [])} comments)")
    if not args.from_json and comments_interrupted(info):
        log("  Comments were interrupted (Ctrl+C): the page has those fetched so far "
            "and counts as finished; use --overwrite for this video to get all of them.")
        raise CommentsInterrupted
    return target


LANGUAGE_CODE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")


def standard_code(code: str) -> str:
    """A language code in the usual notation, as YouTube writes it: "PT-br"
    -> "pt-BR", "zh-hans" -> "zh-Hans" (language lower case, region upper
    case, script capitalised). Option values ignore the case of letters."""
    language, *rest = code.split("-")
    return "-".join([language.lower()] + [part.upper() if len(part) == 2 else
                                          part.capitalize() for part in rest])
TRANSCRIPT_VERSIONS = ("plain", "timestamps", "bilingual")


def transcript_spec(value: str) -> list[tuple[str, str | None]]:
    """Parse --transcript: "plain,timestamps,bilingual:pl" or "none".

    Returns the versions in the given order as (version, language) pairs;
    [] means no transcript. Any case of letters ("Plain", "bilingual:PT-br"). Raises ArgumentTypeError for unknown versions,
    a bilingual version without a valid language code, a language on any
    other version, repeated versions and "none" combined with anything.
    """
    items = [item.strip().lower() for item in value.split(",")]
    if any(not item for item in items):
        raise argparse.ArgumentTypeError(f"empty item in '{value}' (check the commas)")
    if "none" in items:
        if len(items) > 1:
            raise argparse.ArgumentTypeError("'none' cannot be combined with other versions")
        return []
    spec = []
    for item in items:
        version, _, language = item.partition(":")
        if version not in TRANSCRIPT_VERSIONS:
            raise argparse.ArgumentTypeError(
                f"unknown version '{version}' (use plain, timestamps, bilingual:LANG or none)")
        if version == "bilingual":
            if not language:
                raise argparse.ArgumentTypeError(
                    "'bilingual' needs a language code, e.g. bilingual:pl")
            if not LANGUAGE_CODE.fullmatch(language):
                raise argparse.ArgumentTypeError(
                    f"'{language}' is not a language code (e.g. pl, en, pt-BR)")
        elif language or ":" in item:
            raise argparse.ArgumentTypeError(f"'{version}' takes no language ('{item}')")
        entry = (version, standard_code(language) if language else None)
        if entry in spec:
            raise argparse.ArgumentTypeError(f"'{item}' is given more than once")
        spec.append(entry)
    return spec


def plural(n: int, word: str) -> str:
    """'1 video', '2 videos'."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def read_batch_file(path: str) -> list[str]:
    """The sources listed in a --batch-file, one per line.

    Spaces around a line, blank lines and lines starting with "#" (comments)
    are ignored; a UTF-8 byte order mark, as some Windows editors write it,
    is accepted. Raises ValueError if the file cannot be read or lists
    nothing. The file is only read, never changed.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"batch file '{path}' is not UTF-8 text") from None
    except OSError as error:
        raise ValueError(f"cannot read batch file '{path}': {error.strerror or error}") from None
    lines = [line.strip() for line in text.splitlines()]
    sources = [line for line in lines if line and not line.startswith("#")]
    if not sources:
        raise ValueError(f"batch file '{path}' contains no URLs")
    return sources


def has_wildcards(text: str) -> bool:
    return "*" in text or "?" in text


def expand_pattern(source: str) -> list[str]:
    """--from-json paths with * or ?, expanded by the program (Windows shells
    pass them on as they are), sorted. Square brackets stay literal, since
    every page name ends with "[VIDEO_ID]". A pattern without matches is kept;
    saved_data_path() reports it."""
    if not has_wildcards(source):
        return [source]
    pattern = re.sub(r"[][]", lambda m: f"[{m.group(0)}]", source)
    return sorted(glob.glob(pattern)) or [source]


def saved_data_path(source: str, output_dir: Path) -> Path:
    """The .info.json file for a --from-json source: an existing file; else a
    video id, or any file name with "[ID]" (a page, a video, saved data),
    looked up in the output folder as a ".info.json" file with "[ID]" in its
    name. Raises ValueError with what was looked for."""
    path = Path(source)
    if path.is_file():
        return path
    if has_wildcards(source):
        raise ValueError(f"no file matches {source}")
    video_id = source if VIDEO_ID.fullmatch(source) else id_in_name(source, playlists=False)
    if not video_id:
        raise ValueError(f"no such file: {source}")
    found = file_with_id(output_dir, video_id, (".info.json",), "saved data files")
    if not found:
        raise ValueError(f"no saved data for {video_id} in {output_dir} "
                         f"(looked for *[{video_id}]*.info.json)")
    return found


def collect_sources(args: argparse.Namespace) -> list[str]:
    """Sources from the command line, then from each --batch-file in order,
    each video only once.

    Two URLs are the same video if they have the same video id (e.g.
    youtu.be/ID and watch?v=ID); anything else (playlist URLs, .info.json
    paths) must match exactly. Raises ValueError if there is nothing to do
    or a batch file cannot be used.
    """
    what = "file" if args.from_json else "URL"
    seen = set()
    if args.from_json:
        args.sources = [path for source in args.sources for path in expand_pattern(source)]
    else:
        args.sources = [source_from_page_name(source) for source in args.sources]

    def new(source: str) -> bool:
        key = source if args.from_json else (video_id_from_url(source) or source)
        if key in seen:
            return False
        seen.add(key)
        return True

    args.command_sources = list(args.sources)
    args.batch_lines = {}
    sources = [source for source in args.sources if new(source)]
    if len(sources) < len(args.sources):
        log(f"Ignored {plural(len(args.sources) - len(sources), 'duplicate ' + what)} "
            f"given on the command line.")
    for path in args.batch_file:
        listed = read_batch_file(path)
        if args.from_json:
            listed = [found for source in listed for found in expand_pattern(source)]
        else:
            listed = [source_from_page_name(source) for source in listed]
        args.batch_lines[path] = listed
        added = [source for source in listed if new(source)]
        duplicates = len(listed) - len(added)
        note = f" ({plural(duplicates, 'duplicate')} ignored)" if duplicates else ""
        log(f"Read {plural(len(listed), what)} from {path}{note}.")
        sources += added
    if not sources:
        raise ValueError("give at least one URL or a batch file (-a FILE)")
    return sources


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt2html",
        description="Save a YouTube video page (metadata, description, chapters, "
                    "transcript, all comments) as a single offline HTML file.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__} (yt-dlp {yt_dlp.version.__version__})")
    parser.add_argument("sources", nargs="*", metavar="URL",
                        help="video or playlist URL(s) or id(s), or the name of a page or "
                             "index made by yt2html ('Title [ID].html'); a playlist archives "
                             "all its videos. With --from-json: .info.json file(s), also as patterns "
                             "with * or ?, or video id(s) or page names, looked up in the "
                             "output folder (-o)")
    parser.add_argument("-a", "--batch-file", action="append", default=[], metavar="FILE",
                        help="also read URLs (with --from-json: .info.json paths) from a "
                             "UTF-8 text file, one per line; blank lines and lines starting "
                             "with '#' are ignored. Can be given several times. A video "
                             "listed more than once is archived once")
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="folder for the generated pages (default: the current folder)")
    parser.add_argument("--no-comments", action="store_true",
                        help="do not download comments (with --from-json: leave out the "
                             "saved ones)")
    parser.add_argument("--max-comments", type=int, metavar="N",
                        help="limit the number of downloaded comments (replies included; "
                             "with --from-json: keep only the first N saved ones)")
    parser.add_argument("--no-cards", action="store_true",
                        help="leave out the author's info cards (nothing is read or "
                             "downloaded for them)")
    parser.add_argument("--no-avatars", action="store_true",
                        help="do not embed the avatars of comment authors (letter "
                             "placeholders instead); the channel's avatar stays")
    parser.add_argument(
        "--transcript", type=transcript_spec, default=[("plain", None)], metavar="VERSIONS",
        help="transcript versions, comma-separated, shown in this order: "
             "plain (continuous text, the default), timestamps (one line per caption), "
             "bilingual:LANG (timestamps with YouTube's translation into LANG in a "
             "second column, e.g. bilingual:pl; stops with an error if not available), "
             "or none. Example: --transcript plain,bilingual:pl")
    parser.add_argument("--theme", choices=THEMES, default="auto", type=str.lower,
                        help="colour theme of the page: auto (default; follows the light/dark "
                             "setting of the device it is opened on, like YouTube's "
                             "'Use device theme'), dark or light")
    parser.add_argument("--no-snapshot", action="store_true",
                        help="leave out the grey 'Snapshot' date tile "
                             "(the date stays in 'About this copy' at the bottom)")
    # --cookies: the short name; the long one stays (yt-dlp's name, and the
    # Command row of existing pages uses it)
    parser.add_argument("--cookies-from-browser", "--cookies", dest="cookies_from_browser",
                        metavar="BROWSER", type=cookies_argument,
                        help="use cookies from a browser (firefox, chrome, edge, ...) "
                             "when YouTube asks to sign in; a profile after a colon: "
                             "\"firefox:PROFILE_FOLDER\" (its folder name or path)")
    parser.add_argument("--save-json", action="store_true",
                        help="also save the raw yt-dlp metadata next to the page")
    parser.add_argument("--overwrite", action="store_true",
                        help="make the page again even if the video already has one in the "
                             "output folder (recognised by '[VIDEO_ID].html' at the end of the "
                             "file name; such videos are skipped by default)")
    parser.add_argument("--index", action="store_true",
                        help="also make an index page for each playlist URL and each list "
                             "file (-a): every video with a link to its page (found by "
                             "'[VIDEO_ID].html', so renamed pages keep working), its date and "
                             "comments, and the reason for videos without a page")
    parser.add_argument("--index-only", action="store_true",
                        help="only rebuild the index pages from the pages in the output "
                             "folder, without downloading videos (the list comes from the "
                             "earlier index; without one, only the playlist's list is read)")
    parser.add_argument("--video-formats", action="store_true",
                        help="only list, for each video, the qualities a download can take "
                             "(with the parameter that chooses each, the codec and the size) "
                             "and the audio tracks it can add; no page is made")
    parser.add_argument("--from-json", action="store_true",
                        help="build pages from saved .info.json files instead of URLs "
                             "(existing pages are always replaced)")
    args = parser.parse_args(argv)
    if args.video_formats:
        used = [name for name, on in (
            ("--overwrite", args.overwrite), ("--index", args.index),
            ("--index-only", args.index_only), ("--save-json", args.save_json),
            ("--no-comments", args.no_comments), ("--max-comments", args.max_comments is not None),
            ("--no-cards", args.no_cards), ("--no-avatars", args.no_avatars),
            ("--transcript", args.transcript != [("plain", None)]),
            ("--theme", args.theme != "auto"), ("--no-snapshot", args.no_snapshot)) if on]
        if used:
            parser.error(f"--video-formats makes no page, so it cannot be used with "
                         f"{', '.join(used)}")
    if args.from_json and (args.index or args.index_only):
        parser.error("--index and --index-only cannot be used with --from-json")
    if args.from_json and args.cookies_from_browser:
        log("Note: --cookies-from-browser has no effect with --from-json (nothing is downloaded).")
    try:
        args.sources = collect_sources(args)
    except ValueError as error:
        parser.error(str(error))
    if args.index or args.index_only:
        if not args.batch_lines and not any(map(playlist_id_from_url, args.command_sources)):
            parser.error("--index and --index-only need a playlist (URL, id or the name of its "
                         "index page) or a list file (-a FILE)")
        if any(not playlist_id_from_url(s) for s in args.command_sources):
            log("Note: only playlists and list files get an index, not single URLs "
                "given in the command.")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    counts = {"saved": 0, "skipped": 0, "failed": 0}

    run = Run()

    def attempt(source: str, action, video_id: str | None = None):
        """Run one step; a failure is reported and counted, the run goes on."""
        try:
            return action()
        except yt_dlp.utils.DownloadError as error:
            log(f"FAILED: {source}")  # yt-dlp has already printed the reason
            reason = failure_reason(error)
        except Exception as error:
            log(f"FAILED: {source}: {error}")
            reason = failure_reason(error)
        counts["failed"] += 1
        if video_id:
            run.failures[video_id] = reason
        return None

    def archive_video(source: str, playlist: dict | None = None):
        video_id = video_id_from_url(source)
        if video_id:
            run.attempted.add(video_id)
        try:
            result = attempt(source, lambda: archive(source, args, playlist) or "skipped",
                             video_id)
        except CommentsInterrupted:
            counts["saved"] += 1  # the page was made, then the run stops
            raise
        if result:
            counts["skipped" if result == "skipped" else "saved"] += 1

    if args.video_formats:
        return list_formats(args)
    seen = set()  # video ids handled in this run
    playlists = 0
    interrupted = False
    if args.index_only:
        return 1 if write_indexes(args, run) else 0
    try:
        for source in args.sources:
            if not args.from_json and playlist_id_from_url(source):
                playlists += 1
                for url in attempt(source,
                                   lambda: playlist_videos(source, args, seen, run)) or []:
                    archive_video(url, playlist_context(run, source, url))
                continue
            video_id = None if args.from_json else video_id_from_url(source)
            if video_id in seen:
                log(f"Ignored duplicate: {source} (already in a playlist above)")
                continue
            if video_id:
                seen.add(video_id)
            archive_video(source)
    except KeyboardInterrupt:
        interrupted = True
        log("Interrupted (Ctrl+C). Run the same command again to continue: "
            "videos with a page are skipped.")
    index_failures = write_indexes(args, run) if args.index else 0
    if sum(counts.values()) > 1 or playlists or interrupted:
        log(f"Done: {counts['saved']} saved, {counts['skipped']} skipped (page exists), "
            f"{counts['failed']} failed.")
    if interrupted:
        return 130  # the usual exit code after Ctrl+C
    return 1 if counts["failed"] or index_failures else 0


if __name__ == "__main__":
    sys.exit(main())
