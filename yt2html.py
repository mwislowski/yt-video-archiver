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
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

# The program version; pyproject.toml reads it from here (single source).
__version__ = "0.4.0"

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

# Keys filled from the watch page; all None when the page cannot be read.
PAGE_KEYS = (MARKERS_KEY, EXTRAS_KEY, YT_COUNT_KEY, LICENSE_KEY, CAPTION_KEY, TRANSLATIONS_KEY)


# --------------------------------------------------------------------------
# Fetching data
# --------------------------------------------------------------------------

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
        opts["cookiesfrombrowser"] = (args.cookies_from_browser,)
    started = time.monotonic()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
        info[ARCHIVED_KEY] = dt.date.today().isoformat()
        if not args.no_comments:
            info[LIMIT_KEY] = args.max_comments
        if info.get("_type") not in ("playlist", "multi_video"):
            add_watch_page_data(ydl, info)
            if args.transcript:
                info[TRANSCRIPT_KEY] = fetch_transcript(
                    ydl, info, bilingual_languages(args.transcript),
                    not_before=started + TRANSLATION_WAIT)
        return info


# --------------------------------------------------------------------------
# Reading the watch page (what yt-dlp does not return)
# --------------------------------------------------------------------------

def add_watch_page_data(ydl: yt_dlp.YoutubeDL, info: dict) -> None:
    """Fill PAGE_KEYS from the watch page, fetched once more.

    yt-dlp keeps only one timestamp list, as `chapters`, without its name and
    origin, replaces YouTube's comment count with the downloaded number, and
    returns nothing of the description extras, the license link or the
    default caption track. The page is fetched with yt-dlp's cookies and
    consent; ytInitialData and ytInitialPlayerResponse are read from it.
    If it cannot be read, all PAGE_KEYS are None and the page falls back to
    yt-dlp's data where it can.
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
        return
    try:
        player_response = embedded_json(webpage, "ytInitialPlayerResponse")
        info[CAPTION_KEY] = default_caption_track(player_response)
        info[TRANSLATIONS_KEY] = translation_languages(player_response)
    except Exception:
        info[CAPTION_KEY] = info[TRANSLATIONS_KEY] = None
    info[MARKERS_KEY] = parse_marker_lists(data, info.get("description") or "",
                                           info.get("duration"))
    info[EXTRAS_KEY] = parse_description_extras(data)
    info[YT_COUNT_KEY] = parse_comment_count(data)
    info[LICENSE_KEY] = parse_license(data)


def unwrap_redirect(url: str) -> str:
    """YouTube's /redirect?q=<target> links -> the target address."""
    if "youtube.com/redirect" in url or url.startswith("/redirect"):
        target = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q")
        if target:
            return target[0]
    return urllib.parse.urljoin(YOUTUBE, url)


def parse_license(data: dict) -> dict | None:
    """The "License" row (e.g. Creative Commons) from the video's metadata rows.

    It is in videoSecondaryInfoRenderer.metadataRowContainer, not in the
    expanded description's "Video details", and only on videos whose author
    chose a license other than the standard YouTube one.
    """
    contents = ((((data.get("contents") or {}).get("twoColumnWatchNextResults") or {})
                 .get("results") or {}).get("results") or {}).get("contents") or []
    for content in contents:
        secondary = content.get("videoSecondaryInfoRenderer")
        if not secondary:
            continue
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
    page_read = LICENSE_KEY in info and info.get(EXTRAS_KEY) is not None
    if page_read:
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


def extra_image_urls(info: dict) -> set[str]:
    """Images used by the description extras (people photos, podcast covers)."""
    urls = set()
    for extra in info.get(EXTRAS_KEY) or []:
        for entry in (extra.get("people") or []) + (extra.get("items") or []):
            if entry.get("image"):
                urls.add(entry["image"])
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
    origs = [lang for lang in auto if lang.endswith("-orig")]
    language = (default.get("language") or original_audio_language(info)
                or (origs[0].removesuffix("-orig") if len(origs) == 1 else None))
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
        # page is simply made without a transcript.
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
    transcript = {"language": lang, "name": name, "kind": kind, "cues": cues}
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
    """A timestamp linking to the video at that second (plain text without an id)."""
    seconds = int(start)
    text = text or format_duration(seconds)
    if not video_id:
        return esc(text)
    return link(f"{YOUTUBE}/watch?v={video_id}&t={seconds}s", text)


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
                out.append(link(f"{YOUTUBE}/watch?v={video_id}&t={seconds}s", token))
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
.footer{color:var(--muted);font-size:12px;margin-top:32px;padding-top:12px;
  border-top:1px solid var(--line)}
/* Phones */
@media (max-width:600px){
  .page{padding:12px}
  .replies{margin-left:20px}
  .replies .replies{margin-left:12px}
  .transcript .bi-row{grid-template-columns:48px 1fr}
  .transcript .bi-row .text + .text{grid-column:2;border-left:0;padding-left:0;color:var(--muted)}
}
"""


# Light colours, as on YouTube's light theme; the dark ones are in CSS above.
LIGHT_VARIABLES = """
  --bg:#ffffff; --text:#0f0f0f; --muted:#606060; --panel:#f2f2f2;
  --line:#e5e5e5; --link:#065fd4; --chip:#e5e5e5; --media-bg:#e5e5e5;
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
            parts.append(f'<div class="bi-row"><span class="time">{time_link(video_id, cue["start"])}</span>'
                         f'<span class="text">{esc(cue["text"])}</span>'
                         f'<span class="text">{esc(translated[id(cue)])}</span></div>')
    # The languages are named in the column headings, not again in the heading.
    return (f'<section class="transcript bilingual"><h2>Transcript</h2>'
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
                rows.append(f'<div class="cue"><span class="time">{time_link(video_id, cue["start"])}</span>'
                            f'<span class="text">{esc(cue["text"])}</span></div>')
            parts.append(f'<div class="cues">{"".join(rows)}</div>')
        else:
            parts.append(f"<p>{esc(' '.join(c['text'] for c in group['cues']))}</p>")

    note = transcript_note(transcript)
    if note_suffix:
        note = f"{note}, {note_suffix}" if note else note_suffix
    css = "transcript with-timestamps" if timestamps else "transcript"
    return (f'<section class="{css}"><h2>Transcript{note_html(note)}</h2>'
            f'{"".join(parts)}</section>')


def render_page(info: dict, thumbnail: str | None, avatar_uris: dict[str, str],
                archived: dt.date, show_snapshot: bool = True,
                transcript_spec: list | None = None,
                extra_images: dict[str, str] | None = None,
                theme: str = "auto") -> str:
    """The whole HTML page. Images come as data URIs: `thumbnail`,
    `avatar_uris` and `extra_images` ({image url: data URI})."""
    video_id = info.get("id") or ""
    title = info.get("title") or video_id
    duration = info.get("duration")
    url = info.get("webpage_url") or f"{YOUTUBE}/watch?v={video_id}"
    uploader = info.get("uploader") or info.get("channel") or "(unknown channel)"
    channel_url = info.get("uploader_url") or info.get("channel_url")
    comments = info.get("comments") or []
    avatars = AvatarStyles(avatar_uris)

    # Channel avatar: not part of video metadata, but available when the
    # uploader has commented under the video.
    channel_avatar = next((c.get("author_thumbnail") for c in comments
                           if c.get("author_is_uploader") and c.get("author_thumbnail")), None)

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

    body.append('<div class="facts">'
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

    body.append(f'<footer class="footer">Archived from {link(url, url)} on '
                f'{archived.strftime(DATE_FORMAT)} with yt2html.</footer>')
    body.append("</main>")

    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="generator" content="yt2html (yt-video-archiver)">',
        f'<meta name="source-url" content="{esc(url)}">',
        f'<meta name="archived" content="{archived.isoformat()}">',
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
# Command line
# --------------------------------------------------------------------------

def output_path(info: dict, output_dir: Path) -> Path:
    """File name built by yt-dlp's own sanitizer, so it matches the video file."""
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        name = ydl.prepare_filename({**info, "ext": "html"}, outtmpl=OUTPUT_TEMPLATE)
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
        opts["cookiesfrombrowser"] = (args.cookies_from_browser,)
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


def playlist_videos(source: str, args: argparse.Namespace, seen: set[str]) -> list[str]:
    """Watch URLs of the videos of a playlist, without those already in `seen`
    (videos given earlier in this run or twice in the playlist); adds the
    new ones to `seen`."""
    log(f"Fetching playlist {source}")
    title, entries = fetch_playlist(source, args)
    urls, listed = [], 0
    for entry in entries:
        video_id = entry_video_id(entry)
        if not video_id:
            continue
        listed += 1
        if video_id not in seen:
            seen.add(video_id)
            urls.append(f"{YOUTUBE}/watch?v={video_id}")
    duplicates = listed - len(urls)
    note = f" ({plural(duplicates, 'duplicate')} ignored)" if duplicates else ""
    log(f'Playlist "{title}": {plural(listed, "video")}{note}.')
    return urls


def existing_page(output_dir: Path, video_id: str | None) -> Path | None:
    """A page for this video in the output folder (not its subfolders).

    A page is recognised by "[<video id>].html" at the end of its name, so it
    may be renamed freely as long as that part stays.
    """
    if not video_id or not output_dir.is_dir():
        return None
    suffix = f"[{video_id}].html"
    return next((p for p in sorted(output_dir.iterdir())
                 if p.is_file() and p.name.endswith(suffix)), None)


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


PLAYLIST_ERROR = "this URL leads to a playlist or channel, not a single video"
CHANNEL_ERROR = ("this is a channel URL; only videos and playlists are supported "
                 "(open the channel's playlist and use its URL)")


def archive(source: str, args: argparse.Namespace) -> Path | None:
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
        log(f"Reading {source}")
        info = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        log(f"Fetching {source}")
        info = fetch_info(source, args)

    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError(PLAYLIST_ERROR)

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
    avatar_uris = {}
    if not args.no_avatars:
        avatar_uris = fetch_images({c.get("author_thumbnail") for c in info.get("comments") or []},
                                   "avatars", resize=resize_avatar_url)
    extra_images = fetch_images(extra_image_urls(info), "images (people, podcast)")

    try:
        archived = dt.date.fromisoformat(info.get(ARCHIVED_KEY) or "")
    except ValueError:
        archived = dt.date.today()  # older JSON files without the date
    page = render_page(info, thumbnail, avatar_uris, archived,
                       show_snapshot=not args.no_snapshot,
                       theme=args.theme,
                       transcript_spec=args.transcript,
                       extra_images=extra_images)
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
TRANSCRIPT_VERSIONS = ("plain", "timestamps", "bilingual")


def transcript_spec(value: str) -> list[tuple[str, str | None]]:
    """Parse --transcript: "plain,timestamps,bilingual:pl" or "none".

    Returns the versions in the given order as (version, language) pairs;
    [] means no transcript. Raises ArgumentTypeError for unknown versions,
    a bilingual version without a valid language code, a language on any
    other version, repeated versions and "none" combined with anything.
    """
    items = [item.strip() for item in value.split(",")]
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
        entry = (version, language or None)
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

    def new(source: str) -> bool:
        key = source if args.from_json else (video_id_from_url(source) or source)
        if key in seen:
            return False
        seen.add(key)
        return True

    sources = [source for source in args.sources if new(source)]
    if len(sources) < len(args.sources):
        log(f"Ignored {plural(len(args.sources) - len(sources), 'duplicate ' + what)} "
            f"given on the command line.")
    for path in args.batch_file:
        listed = read_batch_file(path)
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
                        help="video or playlist URL(s) (a playlist archives all its videos); "
                             "with --from-json: path(s) to .info.json files")
    parser.add_argument("-a", "--batch-file", action="append", default=[], metavar="FILE",
                        help="also read URLs (with --from-json: .info.json paths) from a "
                             "UTF-8 text file, one per line; blank lines and lines starting "
                             "with '#' are ignored. Can be given several times. A video "
                             "listed more than once is archived once")
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="folder for the generated pages (default: the current folder)")
    parser.add_argument("--no-comments", action="store_true",
                        help="do not download comments")
    parser.add_argument("--max-comments", type=int, metavar="N",
                        help="limit the number of downloaded comments (replies included)")
    parser.add_argument("--no-avatars", action="store_true",
                        help="do not embed avatars (letter placeholders instead)")
    parser.add_argument(
        "--transcript", type=transcript_spec, default=[("plain", None)], metavar="VERSIONS",
        help="transcript versions, comma-separated, shown in this order: "
             "plain (continuous text, the default), timestamps (one line per caption), "
             "bilingual:LANG (timestamps with YouTube's translation into LANG in a "
             "second column, e.g. bilingual:pl; stops with an error if not available), "
             "or none. Example: --transcript plain,bilingual:pl")
    parser.add_argument("--theme", choices=THEMES, default="auto",
                        help="colour theme of the page: auto (default; follows the light/dark "
                             "setting of the device it is opened on, like YouTube's "
                             "'Use device theme'), dark or light")
    parser.add_argument("--no-snapshot", action="store_true",
                        help="leave out the grey 'Snapshot' date tile "
                             "(the date stays in the footer)")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help="use cookies from a browser (firefox, chrome, edge, ...) "
                             "when YouTube asks to sign in")
    parser.add_argument("--save-json", action="store_true",
                        help="also save the raw yt-dlp metadata next to the page")
    parser.add_argument("--overwrite", action="store_true",
                        help="make the page again even if the video already has one in the "
                             "output folder (recognised by '[VIDEO_ID].html' at the end of the "
                             "file name; such videos are skipped by default)")
    parser.add_argument("--from-json", action="store_true",
                        help="build pages from saved .info.json files instead of URLs "
                             "(existing pages are always replaced)")
    args = parser.parse_args(argv)
    try:
        args.sources = collect_sources(args)
    except ValueError as error:
        parser.error(str(error))
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    counts = {"saved": 0, "skipped": 0, "failed": 0}

    def attempt(source: str, action):
        """Run one step; a failure is reported and counted, the run goes on."""
        try:
            return action()
        except yt_dlp.utils.DownloadError:
            log(f"FAILED: {source}")  # yt-dlp has already printed the reason
        except Exception as error:
            log(f"FAILED: {source}: {error}")
        counts["failed"] += 1
        return None

    def archive_video(source: str):
        try:
            result = attempt(source, lambda: archive(source, args) or "skipped")
        except CommentsInterrupted:
            counts["saved"] += 1  # the page was made, then the run stops
            raise
        if result:
            counts["skipped" if result == "skipped" else "saved"] += 1

    seen = set()  # video ids handled in this run
    playlists = 0
    interrupted = False
    try:
        for source in args.sources:
            if not args.from_json and playlist_id_from_url(source):
                playlists += 1
                for url in attempt(source, lambda: playlist_videos(source, args, seen)) or []:
                    archive_video(url)
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
    if sum(counts.values()) > 1 or playlists or interrupted:
        log(f"Done: {counts['saved']} saved, {counts['skipped']} skipped (page exists), "
            f"{counts['failed']} failed.")
    if interrupted:
        return 130  # the usual exit code after Ctrl+C
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
