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
import hashlib
import html
import json
import os
import random
import re
import shutil
import socket
import sys
import time
import urllib.parse
import urllib.request
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp
from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS
from yt_dlp.utils import ISO639Utils

# The program version; pyproject.toml reads it from here (single source).
__version__ = "0.13.0"

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
# requested soon after the video data was fetched (about a minute in early
# November 2026; since mid-November often for good, for yt-dlp alone too).
# Original captions are not affected. So a translation is requested once,
# a random 70-100 s after fetching started (a fixed delay looks automated),
# and not retried: refused, it is left out with a note (subtitle files: the
# run reports a failure, and the next run with --video tries again).
TRANSLATION_WAIT = (70, 100)
TRANSLATION_HINT = (" YouTube refuses auto-translated captions to downloads at times "
                    "(HTTP 429); try again later.")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

YOUTUBE = "https://www.youtube.com"

# --------------------------------------------------------------------------
# Keys of yt2html's own data in the info dict (and in --json write files),
# next to yt-dlp's fields
# --------------------------------------------------------------------------

# Date of fetching, stored so that --json load keeps the original snapshot date.
ARCHIVED_KEY = "yt2html_archived"

# Key under which the timestamp lists are stored in the info dict (and in a
# --json write file). Not a yt-dlp field.
MARKERS_KEY = "yt2html_marker_lists"

# Key for extra description elements (e.g. the AI-generated video summary).
EXTRAS_KEY = "yt2html_description_extras"

# Comment count as YouTube shows it ("116", "1.2K"). yt-dlp replaces its own
# `comment_count` with the number of downloaded comments when it fetches them.
YT_COUNT_KEY = "yt2html_youtube_comment_count"

# YouTube's message in place of the comments ("Comments are turned off."),
# None when there is none.
COMMENTS_MESSAGE_KEY = "yt2html_comments_message"

# The comment limit used when fetching (--page comments:N, by default
# DEFAULT_COMMENTS; None = all).
LIMIT_KEY = "yt2html_max_comments"

# The "License" row of the video details: {title, runs: [{text, url}]}.
LICENSE_KEY = "yt2html_license"

# The caption track YouTube's transcript panel opens with: {language, kind}.
CAPTION_KEY = "yt2html_default_caption_track"

# Languages YouTube can translate captions into: {code: name}.
TRANSLATIONS_KEY = "yt2html_translation_languages"

# The language of the audio track the watch page marks as the original
# (page_original_language()): "pl"; None when none is so marked (a video in
# one language, or a page without its audio tracks). Kept apart from yt-dlp's
# formats, which can come without any audio track (YOUTUBE.local.md 15.5).
ORIGINAL_AUDIO_KEY = "yt2html_original_audio_language"

# The original language set with --olang ("en"): it wins over everything the
# program finds (transcript_language(), the video's main audio, the
# Languages table); absent when not set.
OLANG_KEY = "yt2html_original_language_set"

# The transcript: {language, name, kind, cues: [{start, text}], translations:
# {lang: {language, name, cues, own?}}} (own: {file, date} for a translation of
# one's own that --json merge took from an SRT file, the others are YouTube's);
# None if the video has none (the reason in NOT_SAVED_KEY), absent without
# --transcript. All versions on the page are built from the same cues.
TRANSCRIPT_KEY = "yt2html_transcript"

# The author's info cards: [{type, title, url, image, details, message, start}];
# absent with --page cards:no, None when the watch page cannot be read.
CARDS_KEY = "yt2html_cards"

# The channel's avatar (image URL) from the watch page's owner row; yt-dlp
# gives it only for channel pages, not for videos.
CHANNEL_AVATAR_KEY = "yt2html_channel_avatar"

# How the data was downloaded, for "About this copy":
# {yt2html: version, cookies_from_browser: browser name or None}.
DOWNLOAD_KEY = "yt2html_download"

# The files --video took or found next to the page, in the same run as the
# data: {date, parameters, video: {file, size, date?, quality, codec,
# video_format, audio, audio_codec, audio_format}, extra_audio: [{audio,
# role, file, size, date?, audio_format?}], subtitles: [{code, kind, file,
# size, date?, source?}]}. An entry with a date was downloaded that day (now,
# or in an earlier run whose saved data describe the same file); one without
# was only found next to the page. The Video file row and the yellow marks
# come from it.
VIDEO_KEY = "yt2html_video"

# The playlist the video was archived from: {id, title, position, count}
# (position and count on the download day); None if not from a playlist.
PLAYLIST_KEY = "yt2html_playlist"

# What the copy lacks although a command asked for it (the user, 25.11.2026:
# one map for all of it): {key: {reason, date, again}}, the key in the words
# of the options: "transcript" (none at all), "transcript pl" (a translation),
# "transcript pl.author" (subtitles of their own). `again`: the same command
# asks for it again (a refusal, a failed download), else not (YouTube does not
# have it). The state now, without history (the runs are in UPDATES_KEY): an
# entry goes when what it names comes. The page and --json show read it.
NOT_SAVED_KEY = "yt2html_not_saved"

# Data saved by 0.12.3 and before: why the transcript is missing, and the
# transcript's own "missing"; read only by upgrade_saved() (transitional code:
# notes 5.4, to be removed when no saved data have them).
TRANSCRIPT_MISSING_KEY = "yt2html_transcript_missing"

# What each run of --json write and --json refresh did to the saved data:
# [{date, time, yt2html, added: [text] ("translation pl", "subtitles pl
# (translated)"), refreshed: true for --json refresh, comments: {new, gone,
# back} when the comments were refreshed, unavailable: true for a
# --json refresh that found the video no longer on YouTube}]; the copy's date
# stays. The page names only the runs (Downloaded row); the rest is for the data.
UPDATES_KEY = "yt2html_updates"

# --json refresh found the video no longer on YouTube (removed or private):
# {status, reason} as YouTube gives them (the reason as yt-dlp shows it). The
# state now, without history (the runs are in UPDATES_KEY); removed when a
# later --json refresh finds the video again.
UNAVAILABLE_KEY = "yt2html_unavailable"

# In a saved comment (--json refresh): the day it was first not on YouTube,
# the times it was missing and came back ([{from, until}]), its earlier
# texts ([{text, until}]) and, for one a refresh added, the day it came.
MISSING_SINCE_KEY = "yt2html_missing_since"
MISSING_HISTORY_KEY = "yt2html_missing"
EDITS_KEY = "yt2html_edits"
FIRST_SEEN_KEY = "yt2html_first_seen"

# --json refresh: the times the comments were turned off on YouTube (or no
# longer shown) after they had been saved: [{from, until?, turned_off}]; the
# last entry without "until" means they are off now (a note on the page).
COMMENTS_CLOSED_KEY = "yt2html_comments_closed"

# --json refresh: the earlier title and description ([{until, title} or
# {until, description}]); the page shows the current ones, without a note.
PREVIOUS_KEY = "yt2html_previous"

# What --json refresh takes anew besides the comments: the counts.
REFRESHED_KEYS = ("view_count", "like_count", "channel_follower_count", YT_COUNT_KEY)

# The watch page: "read", "partial" (without the player data) or "failed".
WATCH_PAGE_KEY = "yt2html_watch_page"

# Keys filled from the watch page; all None when the page cannot be read.
PAGE_KEYS = (MARKERS_KEY, EXTRAS_KEY, YT_COUNT_KEY, COMMENTS_MESSAGE_KEY, LICENSE_KEY, CAPTION_KEY,
             TRANSLATIONS_KEY, CHANNEL_AVATAR_KEY, ORIGINAL_AUDIO_KEY)


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
    if args.cookies:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    if getattr(args, "olang", None):
        info[OLANG_KEY] = args.olang
    info[DOWNLOAD_KEY] = {"yt2html": __version__,
                          "cookies_from_browser": browser_name(args.cookies),
                          "deno": shutil.which("deno") is not None}
    return info


def fetch_info(url: str, args: argparse.Namespace) -> dict:
    """Extract metadata and comments with yt-dlp (no video download)."""
    max_comments = "all" if args.comment_limit is None else str(args.comment_limit)
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
    if args.cookies:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies)
    started = time.monotonic()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
        info[ARCHIVED_KEY] = dt.date.today().isoformat()
        if getattr(args, "olang", None):
            info[OLANG_KEY] = args.olang
        info[DOWNLOAD_KEY] = {"yt2html": __version__,
                              "cookies_from_browser": browser_name(args.cookies),
                              # yt-dlp needs a JavaScript runtime (by default Deno) for
                              # YouTube's full list of formats (Video formats section)
                              "deno": shutil.which("deno") is not None}
        if not args.no_comments:
            info[LIMIT_KEY] = args.comment_limit
        if info.get("_type") not in ("playlist", "multi_video"):
            add_watch_page_data(ydl, info, cards=not args.no_cards)
            if args.transcript:
                gaps = {}
                info[TRANSCRIPT_KEY] = fetch_transcript(
                    ydl, info, gaps, source_keys(args.transcript, info, None),
                    not_before=started + translation_wait())
                set_gaps(info, gaps)
        return info


# --------------------------------------------------------------------------
# Reading the watch page (what yt-dlp does not return)
# --------------------------------------------------------------------------

class VideoUnavailable(Exception):
    """YouTube refused the video: the watch page has no video, only a
    playability status other than OK. `status` and `reason` as YouTube gives
    them; str() is the reason."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason

    @property
    def gone(self) -> bool:
        """Removed (YouTube's status ERROR, except "try again later") or
        private: --json refresh records it. Any other refusal ("not a bot",
        sign in to confirm the age, try again later) may not last."""
        if self.status == "ERROR":
            return "try again later" not in self.reason.lower()
        return self.status == "LOGIN_REQUIRED" and self.reason.startswith("Private video")


def playability_refusal(player_response: dict) -> VideoUnavailable | None:
    """The refusal in the watch page's player data, or None. yt-dlp, told to
    ignore missing formats, only warns and returns an empty shell
    ("youtube video #ID"), so the program checks it itself. The text joins
    YouTube's reason and the error screen's description as yt-dlp does
    ("Video unavailable. This video is no longer available because ...")."""
    status = player_response.get("playabilityStatus") or {}
    if status.get("status") in (None, "OK") or player_response.get("videoDetails"):
        return None
    screen = (((status.get("errorScreen") or {}).get("playerInterstitialRenderer") or {})
              .get("content") or {}).get("interstitialViewModel") or {}
    reason = status.get("reason") or (screen.get("title") or {}).get("content") or status["status"]
    detail = (screen.get("description") or {}).get("content")
    if detail and detail != reason:
        reason = f"{reason}. {detail}"
    return VideoUnavailable(status["status"], reason)


def add_watch_page_data(ydl: yt_dlp.YoutubeDL, info: dict, cards: bool = True) -> None:
    """Fill PAGE_KEYS from the watch page, fetched once more.

    yt-dlp keeps only one timestamp list, as `chapters`, without its name and
    origin, replaces YouTube's comment count with the downloaded number, and
    returns nothing of the description extras, the license link or the
    default caption track. The page is fetched with yt-dlp's cookies and
    consent; ytInitialData and ytInitialPlayerResponse are read from it.
    If it cannot be read, all PAGE_KEYS are None and the page falls back to
    yt-dlp's data where it can. Info cards are read only if `cards` is true
    (--page cards:no leaves CARDS_KEY out and does nothing for them). Raises
    VideoUnavailable when YouTube refuses the video (playability_refusal()).
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
    refusal = None
    try:
        player_response = embedded_json(webpage, "ytInitialPlayerResponse")
        refusal = playability_refusal(player_response)
        info[CAPTION_KEY] = default_caption_track(player_response)
        info[ORIGINAL_AUDIO_KEY] = page_original_language(player_response)
        info[TRANSLATIONS_KEY] = translation_languages(player_response)
        if info.get("like_count") is None:
            info["like_count"] = like_count(player_response)
        info[WATCH_PAGE_KEY] = "read"
    except Exception:
        info[CAPTION_KEY] = info[TRANSLATIONS_KEY] = info[ORIGINAL_AUDIO_KEY] = None
        info[WATCH_PAGE_KEY] = "partial"
    if refusal:
        raise refusal
    info[MARKERS_KEY] = parse_marker_lists(data, info.get("description") or "",
                                           info.get("duration"))
    info[EXTRAS_KEY] = parse_description_extras(data)
    info[YT_COUNT_KEY] = parse_comment_count(data)
    info[COMMENTS_MESSAGE_KEY] = parse_comments_message(data)
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


def parse_comments_message(data: dict) -> str | None:
    """YouTube's message in place of the comments, from the comment section
    of the watch page (itemSectionRenderer "comment-item-section" with a
    messageRenderer): "Comments are turned off." without the "Learn more"
    link. None when the section holds comments, or there is no section."""
    results = (((data.get("contents") or {}).get("twoColumnWatchNextResults") or {})
               .get("results") or {}).get("results") or {}
    for item in results.get("contents") or []:
        section = item.get("itemSectionRenderer") or {}
        if section.get("sectionIdentifier") != "comment-item-section":
            continue
        for part in section.get("contents") or []:
            runs = ((part.get("messageRenderer") or {}).get("text") or {}).get("runs") or []
            text = "".join(run.get("text", "") for run in runs
                           if "navigationEndpoint" not in run).strip()
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


def like_count(player_response: dict) -> int | None:
    """The like count in the player data, for when yt-dlp has none.

    yt-dlp reads the count only from the like button's spoken label ("like
    this video along with 1,234 other people"), which has no number when
    nobody liked the video yet, so it gives None for zero likes. The player
    data's microformat has `likeCount` "0" then; when the author hides the
    count the field is not there (and `allowRatings` is false), so None
    stays and the Likes tile shows "—" as before.
    """
    renderer = (player_response.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    count = renderer.get("likeCount")
    return int(count) if isinstance(count, str) and count.isdecimal() else None


def page_original_language(player_response: dict) -> str | None:
    """The language of the audio track the watch page marks as the original,
    by yt-dlp's rule for its language preference 10 (get_language_code_and_
    preference): "original" in the track's name ("Polish original"; the page
    is asked for in English), the part of its id before the dot ("pl.4"); an
    audio description is not the original. None without such a track."""
    for fmt in (player_response.get("streamingData") or {}).get("adaptiveFormats") or []:
        name = ((fmt.get("audioTrack") or {}).get("displayName") or "").lower()
        if "original" in name and "descriptive" not in name:
            return (fmt["audioTrack"].get("id") or "").split(".")[0] or None
    return None


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
    language = (page_original_language(player_response)
                or (audio.get("audioTrackId") or "").split(".")[0])
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
                      [{title, url, byline, count, image}]}; also from
                      YouTube's newer podcast card ("Podcast", no byline)
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
        podcast = parse_podcast_card(item)
        if podcast:
            extras.append(podcast)
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


def parse_podcast_card(item: dict) -> dict | None:
    """YouTube's newer form of the podcast section (seen 23.11.2026 on
    Z8lf5oWKfj0, in place of "Explore the podcast"): an itemSectionRenderer
    holding a cardViewModel marked "podcast_hero_card", with the podcast's
    title and episode count as metadata rows, its square cover as the
    background and the playlist as the tap target. As a "playlists" element
    headed "Podcast" (the card's badge; it has no heading) and without a
    byline (it names no author); parts of a row joined with " · ". None for
    anything else."""
    for content in (item.get("itemSectionRenderer") or {}).get("contents") or []:
        card = content.get("cardViewModel") or {}
        if card.get("accessibilityId") != "podcast_hero_card":
            continue
        body = (card.get("bodyContent") or {}).get("heroCardBodyViewModel") or {}
        layout = (body.get("bodyContent") or {}).get("linearLayoutViewModel") or {}
        rows = []   # the podcast's title, then "8 episodes"
        for entry in layout.get("items") or []:
            for row in (entry.get("contentMetadataViewModel") or {}).get("metadataRows") or []:
                rows.append(" · ".join(text_of(part.get("text"))
                                     for part in row.get("metadataParts") or []))
        if not rows or not rows[0]:
            continue
        tap = (((card.get("rendererContext") or {}).get("commandContext") or {})
               .get("onTap") or {}).get("innertubeCommand") or {}
        browse_id = (tap.get("browseEndpoint") or {}).get("browseId") or ""
        playlist_id = browse_id[2:] if browse_id.startswith("VL") else browse_id
        image = (((card.get("background") or {}).get("heroCardBackgroundViewModel") or {})
                 .get("image") or {}).get("sources")
        return {"type": "playlists", "title": "Podcast", "items": [{
            "title": rows[0],
            "url": f"{YOUTUBE}/playlist?list={playlist_id}" if playlist_id else None,
            "byline": None, "count": rows[1] if len(rows) > 1 else None,
            "image": best_image_url(image)}]}
    return None


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
        url = channel_address((navigation.get("browseEndpoint") or {}).get("browseId"))
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


# What the watch page tells about the transcript's track and languages; a
# fresh list of formats (yt-dlp alone) has none of it.
PAGE_CHOICE_KEYS = (CAPTION_KEY, ORIGINAL_AUDIO_KEY, TRANSLATIONS_KEY, OLANG_KEY)


def with_page_choices(fresh: dict, page: dict | None) -> dict:
    """Fresh yt-dlp data with the watch page's PAGE_CHOICE_KEYS from `page`
    (saved data, or the page's data made now), for choosing the transcript's
    track and language as the page did."""
    data = {**fresh, **{key: (page or {}).get(key) for key in PAGE_CHOICE_KEYS}}
    data[OLANG_KEY] = fresh.get(OLANG_KEY) or (page or {}).get(OLANG_KEY)   # --olang now wins
    if not data[OLANG_KEY]:
        del data[OLANG_KEY]
    return data


def marked_original_language(info: dict) -> str | None:
    """The language of the audio marked as the original: in yt-dlp's formats
    (language preference 10, or YouTube's kind in the format's address,
    audio_kind()), else on the watch page (ORIGINAL_AUDIO_KEY: yt-dlp can get
    no audio tracks at all, e.g. when YouTube asks to confirm one is not a
    bot, imRB4Y2ShoM 23.11.2026). A video with one audio language has no
    such mark."""
    for fmt in info.get("formats") or []:
        if fmt.get("language") and (fmt.get("language_preference") == 10
                                    or audio_kind(fmt) == "original"):
            return fmt["language"]
    return info.get(ORIGINAL_AUDIO_KEY)


def original_uncertain(info: dict) -> bool:
    """Whether the original language is a guess that may be a dub's: no audio
    is marked as the original (marked_original_language()), and YouTube
    recognised speech in more than one language (several "-orig" automatic
    captions: the video has dubs). Then the transcript's language is that of
    the captions the watch page opens with, which may be a dub's."""
    if info.get(OLANG_KEY):   # set with --olang: no guess
        return False
    origs = [lang for lang, fmts in (info.get("automatic_captions") or {}).items()
             if fmts and lang.endswith("-orig")]
    return len(origs) > 1 and not marked_original_language(info)


def original_audio_language(info: dict) -> str | None:
    """Original audio language (the audio marked "original" in the formats or
    on the watch page, else yt-dlp's language of the video)."""
    return marked_original_language(info) or info.get("language")


def transcript_language(info: dict) -> str | None:
    """The video's original language for the transcript: the watch page's
    default track, else yt-dlp's original audio, else a single "-orig" track.
    The page's default follows the audio it starts with, which for a viewer
    in another language can be a dub (8etkFuBLGZ0, 20.11.2026: Polish
    original, an English auto-dub, the English one's captions as default for
    yt-dlp's English request); so an audio marked as the original in another
    language wins (marked in yt-dlp's formats, else on the watch page)."""
    if info.get(OLANG_KEY):   # --olang: the user knows best
        return info[OLANG_KEY]
    auto = [lang for lang, fmts in (info.get("automatic_captions") or {}).items() if fmts]
    origs = [lang for lang in auto if lang.endswith("-orig")]
    default = (info.get(CAPTION_KEY) or {}).get("language")
    marked = marked_original_language(info)
    if marked and base_language(default) != base_language(marked):
        return marked
    return (default or original_audio_language(info)
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
        # the page's kind of track only for the language it names (not when
        # the original wins over a dub's default, transcript_language())
        if (default.get("kind") == "asr"
                and base_language(default.get("language")) == base_language(language)):
            order = (find_auto, find_manual)
        else:
            order = (find_manual, find_auto)
        for find in order:
            found = find(language)
            if found:
                return found
        if info.get(OLANG_KEY):   # --olang: that language or none
            return None
    # Last resort (original language unknown): any author subtitles.
    if manual:
        lang = next(iter(manual))
        return "manual", lang, manual[lang]
    return None


def parse_json3_captions(data: dict) -> list[dict]:
    """Caption cues from YouTube's json3 format: [{start (s), end (s), text}].

    Line breaks inside a cue become spaces; events that hold only a line
    break (used by automatic captions) are skipped. The end (start and
    duration) is for the SRT files (--transcript srt); data saved before it
    have none (cues_srt() takes the next line's start).
    """
    cues = []
    for event in data.get("events") or []:
        segs = event.get("segs")
        if not segs:
            continue
        text = " ".join("".join(seg.get("utf8", "") for seg in segs).split())
        if text:
            start = event.get("tStartMs") or 0
            cue = {"start": round(start / 1000, 3), "text": text}
            if event.get("dDurationMs") is not None:   # (0 too: as YouTube's SRT)
                cue["end"] = round((start + event["dDurationMs"]) / 1000, 3)
            cues.append(cue)
    return cues


class TranscriptError(Exception):
    """The requested transcript could not be downloaded (a
    failure that may pass); nothing is saved, so the next run tries again."""


class TranslationUnavailable(Exception):
    """YouTube has no such translation of the transcript (it will not come
    by trying again): the page is made without it, with a note."""


class NoConnection(Exception):
    """The computer cannot reach YouTube (the name does not resolve, the
    network is unreachable; the user's run of 25.11.2026): the run stops
    before anything of the video is written, so its page and data stay as
    they were and the next run does the rest; `partly`: files of --video
    were downloaded first, and the page and data describe them (review A4)."""

    def __init__(self, reason: str = "", partly: bool = False):
        super().__init__(reason)
        self.partly = partly


# What a lost connection looks like in an error, unlike a refusal (HTTP 429)
# or a passing failure: the name does not resolve or there is no network.
NO_CONNECTION_RE = re.compile(
    r"getaddrinfo failed|Failed to resolve|Name or service not known|nodename nor servname"
    r"|Temporary failure in name resolution|No address associated with hostname"
    r"|Network is unreachable|No route to host|\[Errno 1100[1-4]\]|\[WinError 100(?:51|65)\]",
    re.IGNORECASE)


def no_connection(error: BaseException | None) -> bool:
    """Whether an error (or one it came from) is a lost connection."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if NO_CONNECTION_RE.search(str(error)):
            return True
        info = getattr(error, "exc_info", None)   # yt-dlp's DownloadError
        inner = info[1] if isinstance(info, tuple) and len(info) > 1 else None
        error = error.__cause__ or error.__context__ or inner
    return False


def connected() -> bool:
    """Whether YouTube's name resolves now: checked before a page made with
    YouTube's data replaces the one there, so a connection lost on the way
    does not leave it with placeholders for its images."""
    try:
        socket.getaddrinfo("www.youtube.com", 443)
        return True
    except OSError:
        return False


class TranslationRefused(TranslationUnavailable):
    """YouTube did not send the translation (HTTP 429 or another failure): the
    page is made without it, with a note; --page ovr tries again later."""


# Each caption track is downloaded once per run, as json3 (YouTube's own
# format): the transcript and the subtitle files (made from it as SRT) share
# it, so a translation is asked for once, and a refusal is not asked again.
caption_cache: dict = {}


def caption_key(url: str) -> tuple | str:
    """What tells a caption track apart, whatever the address's signature
    and format: (video, language, kind, name, translation language)."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    if not query.get("lang"):
        return with_query(url, fmt="json3")
    return tuple(query.get(key, "") for key in ("v", "lang", "kind", "name", "tlang"))


def translation_wait() -> float:
    """Seconds after fetching the video data before asking for a translation."""
    return random.uniform(*TRANSLATION_WAIT)


def caption_data(url: str, fetch, not_before: float = 0.0) -> dict:
    """A caption track's json3 data, downloaded with `fetch(url) -> bytes`
    once per run ({} when YouTube sends nothing). The first download waits
    until `not_before` (a time.monotonic() value); a failure is kept and
    raised again for the same track."""
    key = caption_key(url)
    if key not in caption_cache:
        wait(not_before - time.monotonic(), "YouTube refuses translations requested too soon")
        try:
            raw = fetch(with_query(url, fmt="json3")).decode("utf-8", errors="replace")
            caption_cache[key] = json.loads(raw) if raw.strip() else {}
        except Exception as error:
            caption_cache[key] = error
    found = caption_cache[key]
    if isinstance(found, Exception):
        raise found
    return found


def read_url(ydl: yt_dlp.YoutubeDL, url: str) -> bytes:
    with ydl.urlopen(url) as response:
        return response.read()


def download_cues(ydl: yt_dlp.YoutubeDL, url: str, not_before: float = 0.0) -> list[dict]:
    return parse_json3_captions(caption_data(url, lambda u: read_url(ydl, u), not_before))


def srt_time(ms: int) -> str:
    hours, rest = divmod(int(ms), 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


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


def refusal_reason(error: Exception) -> str:
    """Why a translation did not come, for "About this copy"."""
    if is_too_many_requests(error):
        return "YouTube refused to send it (HTTP 429 Too Many Requests)"
    return f"the download failed ({failure_reason(error)})"


def fetch_translation(ydl: yt_dlp.YoutubeDL, info: dict, transcript: dict,
                      url: str, target: str, not_before: float = 0.0) -> dict:
    """YouTube's auto-translation of the transcript track into `target`.

    It is the same track with `tlang=<target>`, so the translated captions
    keep the original timing. YouTube refuses such requests (HTTP 429) for
    a while after the video data was fetched, so this waits until
    `not_before` (a time.monotonic() value) and asks once.
    Raises TranslationUnavailable when YouTube has no such translation,
    TranslationRefused when it does not send it.
    """
    languages = info.get(TRANSLATIONS_KEY)
    if base_language(target) == base_language(transcript["language"]):
        raise TranslationUnavailable(f"'{target}' is the language of the transcript itself "
                                     f"({transcript['name']})")
    if languages is not None and target not in languages:
        available = ", ".join(sorted(languages)) or "none"
        raise TranslationUnavailable(f"YouTube offers no translation of this transcript into "
                                     f"'{target}' (available: {available})")
    name = (languages or {}).get(target, target)
    log(f"Downloading transcript translation ({name}, auto-translated)...")
    try:
        cues = download_cues(ydl, with_query(url, tlang=target), not_before)
    except Exception as error:
        if no_connection(error):
            raise NoConnection(failure_reason(error)) from error
        raise TranslationRefused(refusal_reason(error))
    if not cues:
        raise TranslationUnavailable(f"YouTube returned no translation into '{target}'")
    return {"language": target, "name": name, "cues": cues}


# The kinds a version in another language can name ("plain:pl.author"): the
# author's subtitles, YouTube's automatic ones (made from an audio track's
# speech: a dub has its own), YouTube's translation of the transcript, one's
# own (--json merge). Without a kind (the user, 25.11.2026), plain and timed
# take one's own translation, else YouTube's, else the author's subtitles,
# else a dub's (the author's and a dub's are rare and have other lines and
# times; they are fetched when YouTube refuses its translation); dual needs
# the same lines as the original, so only a translation: one's own, else
# YouTube's.
TRACK_KINDS = ("author", "auto-generated")
SOURCE_KINDS = TRACK_KINDS + ("translated", "own")


def split_code(token: str | None) -> tuple[str | None, str | None]:
    """"pl.author" -> ("pl", "author"); "pl" -> ("pl", None)."""
    if not token:
        return None, None
    code, _, kind = token.partition(".")
    return code, kind or None


def version_languages(spec: list[tuple[str, str | None]]) -> list[str]:
    """The languages a --transcript spec names, each once, in its order (as
    written, with a kind if one is given)."""
    return list(dict.fromkeys(language for version, language in spec
                              if language and version not in ("ovr", "srt")))


def subtitles_in(info: dict, code: str, kind: str) -> tuple[str, list[dict]] | None:
    """YouTube's subtitles of a kind in a language: (their code, formats) of
    the author's ("subtitles") or the automatic ones made from speech
    ("automatic_captions", "-orig"); the exact code, else the same language."""
    if kind == "author":
        tracks = {lang: fmts for lang, fmts in (info.get("subtitles") or {}).items()
                  if lang != "live_chat" and fmts}
    else:
        tracks = {lang.removesuffix("-orig"): fmts
                  for lang, fmts in (info.get("automatic_captions") or {}).items()
                  if lang.endswith("-orig") and fmts}
    match = ([lang for lang in tracks if lang.lower() == code.lower()]
             or [lang for lang in tracks if base_language(lang) == base_language(code)])
    return (match[0], tracks[match[0]]) if match else None


def source_keys(spec: list, info: dict, original: str | None,
                own: set | frozenset = frozenset()) -> list[str]:
    """What to fetch for the versions in other languages, each once: "pl" for
    YouTube's translation, "pl.author" or "pl.auto-generated" for subtitles
    of their own, and "pl|pl.author" for plain or timed without a kind: the
    translation, and when YouTube does not give it the first subtitles of
    their own it has (the author's, else a dub's; fallback_key()). A
    language of one's own translation (`own`) needs nothing without a kind
    (it comes first); one in the original's language (`original`, if known)
    is the original itself."""
    keys = []
    for version, token in spec:
        code, kind = split_code(token)
        if not code or version in ("ovr", "srt") or kind == "own":
            continue
        if original and base_language(code) == base_language(original):
            continue
        if kind in TRACK_KINDS:
            key = f"{code}.{kind}"
        elif kind is None and code in own:
            key = None
        elif version == "dual" or kind == "translated":
            key = code
        else:
            found = next((k for k in TRACK_KINDS if subtitles_in(info, code, k)), None)
            key = f"{code}|{code}.{found}" if found else code
        if key and key not in keys:
            keys.append(key)
    return keys


def key_code(key: str) -> str:
    """The language of a source key: "pl", "pl.author", "pl|pl.author" -> "pl"."""
    return key.split("|")[0].split(".")[0]


def split_keys(keys: list[str]) -> tuple[list[str], list[str], dict[str, str]]:
    """source_keys() as (YouTube's translations, subtitles of their own,
    {translation: the subtitles taken when it does not come})."""
    translations, tracks, fallbacks = [], [], {}
    for key in keys:
        first, _, fallback = key.partition("|")
        if "." in first:
            tracks.append(first)
            continue
        if first not in translations:
            translations.append(first)
        if fallback:
            fallbacks[first] = fallback
    return translations, tracks, fallbacks


def version_text(version: str, language: str | None) -> str:
    return version if language is None else f"{version}:{language}"


def version_source(transcript: dict, version: str, token: str) -> str:
    """Where a version in another language comes from in the data: "pl.own"
    (one's own translation), "pl" (YouTube's translation), "pl.author" or
    "pl.auto-generated" (subtitles of their own); without a kind the first
    the data have, in the order of the kinds (dual: a translation only); the
    token as written when the data have none of it (available() then says
    no)."""
    code, kind = split_code(token)
    if kind is not None:
        return code if kind == "translated" and code in translations_of(transcript) else token
    order = [f"{code}.own", code] + ([] if version == "dual" else
                                     [f"{code}.{k}" for k in TRACK_KINDS])
    return next((source for source in order if available(transcript, source)), token)


def available(transcript: dict, source: str | None) -> bool:
    """Whether the data have a version's source (version_source())."""
    if source is None:
        return True
    code, kind = split_code(source)
    if kind == "own":
        return code in own_of(transcript)
    if kind:
        return source in (transcript.get("tracks") or {})
    return source in translations_of(transcript)


def translation_entry(transcript: dict, source: str) -> dict | None:
    """The translation a source names: "pl.own" one's own, "pl" YouTube's."""
    code, kind = split_code(source)
    return (own_of(transcript) if kind == "own" else translations_of(transcript)).get(code)


def resolve_versions(spec: list, transcript: dict | None
                     ) -> tuple[list[tuple[str, str | None]], list[str]]:
    """The versions this video's page shows, and what changed on the way:
    a code in the original's language (any region) is the original (None);
    dual into it has nothing beside it, so it is timed; another language is
    its source in the data (version_source()); the same version twice, as
    it turns out now, is shown once. Returns (versions, notes)."""
    transcript = transcript or {}
    original = transcript.get("language")
    name = transcript.get("name") or original
    versions, notes = [], []
    for version, language in spec:
        if version in ("ovr", "srt"):
            continue
        code, _ = split_code(language)
        own = code is None or (original and base_language(code) == base_language(original))
        shown = "timed" if version == "dual" and own else version
        if shown != version:
            notes.append(f"{version_text(version, language)}: the original is {name}; "
                         f"shown as timed")
        entry = (shown, None if own else version_source(transcript, version, language))
        if entry in versions:
            notes.append(f"{version_text(version, language)}: the same as "
                         f"{version_text(*entry) if entry[1] else entry[0]}; shown once")
            continue
        versions.append(entry)
    return versions, notes


def translations_of(transcript: dict) -> dict[str, dict]:
    """{language: YouTube's translation} of a transcript (empty if it has
    none); one's own are apart (own_of())."""
    return {language: entry for language, entry in (transcript.get("translations") or {}).items()
            if not entry.get("own")}


def own_of(transcript: dict) -> dict[str, dict]:
    """{language: one's own translation} (--json merge): in "own" since the
    user's decision of 25.11.2026 (beside YouTube's in the same language);
    data saved before keep them among the translations, marked "own"."""
    own = dict(transcript.get("own") or {})
    for language, entry in (transcript.get("translations") or {}).items():
        if entry.get("own"):
            own.setdefault(language, entry)
    return own


def set_own(transcript: dict, language: str, entry: dict) -> None:
    """One's own translation into the data, in "own" (one saved before among
    the translations is moved there)."""
    translations = transcript.get("translations") or {}
    if (translations.get(language) or {}).get("own"):
        del translations[language]
        if not translations:
            transcript.pop("translations", None)
    transcript.setdefault("own", {})[language] = entry


def own_shown(transcript: dict | None, spec: list | None) -> list[tuple[str, str]]:
    """[(language, day added)] of the translations of one's own (--json merge)
    that the page shows, in any version: only those the data have (a ".own"
    version without one is "not available" on the page, not an error)."""
    transcript = transcript or {}
    own = own_of(transcript)
    shown = [source for _, source in resolve_versions(spec or [], transcript)[0] if source]
    return [(code, own[code]["own"].get("date") or "")
            for code, kind in map(split_code, dict.fromkeys(shown))
            if kind == "own" and code in own]


def gap_key(source: str | None = None) -> str:
    """The key in NOT_SAVED_KEY for the transcript itself (None) or one of its
    sources ("pl", "pl.author")."""
    return f"transcript {source}" if source else "transcript"


def note_gap(gaps: dict, key: str, reason: str, again: bool) -> None:
    """What did not come, into `gaps` (a NOT_SAVED_KEY map): why, the day,
    and whether the same command asks for it again."""
    gaps[key] = {"reason": reason, "date": dt.date.today().isoformat(), "again": again}


def transcript_gaps(info: dict) -> dict:
    """The sources of the transcript that the data lack: {"pl": entry, …}."""
    return {key.partition(" ")[2]: entry for key, entry in (info.get(NOT_SAVED_KEY) or {}).items()
            if key.startswith("transcript ")}


def without_transcript_gaps(gaps: dict) -> dict:
    """`gaps` without those of the transcript (it is downloaded again)."""
    return {key: entry for key, entry in gaps.items() if key.partition(" ")[0] != "transcript"}


def set_gaps(info: dict, gaps: dict) -> None:
    """NOT_SAVED_KEY as `gaps`; none when there are none."""
    if gaps:
        info[NOT_SAVED_KEY] = gaps
    else:
        info.pop(NOT_SAVED_KEY, None)


def upgrade_saved(info: dict) -> dict:
    """Saved data of 0.12.3 and before in today's form: the transcript's
    "missing" ({KEY: {reason, refused, date}}) and TRANSCRIPT_MISSING_KEY go
    into NOT_SAVED_KEY (refused: asked for again). Transitional code (notes
    5.4): removed once no saved data have them. Returns `info`."""
    transcript = info.get(TRANSCRIPT_KEY)
    old = transcript.pop("missing", None) if isinstance(transcript, dict) else None
    gaps = {}
    for key, entry in (old or {}).items():
        gaps[gap_key(key)] = {"reason": entry.get("reason") or "unknown",
                              **({"date": entry["date"]} if entry.get("date") else {}),
                              "again": bool(entry.get("refused"))}
    reason = info.pop(TRANSCRIPT_MISSING_KEY, None)
    if reason and TRANSCRIPT_KEY in info and transcript is None:
        gaps[gap_key()] = {"reason": reason, "again": False}
    if gaps:
        info[NOT_SAVED_KEY] = {**gaps, **(info.get(NOT_SAVED_KEY) or {})}
    return info


def read_saved(path: Path) -> dict:
    """Saved data from their file, in today's form (upgrade_saved())."""
    return upgrade_saved(json.loads(path.read_text(encoding="utf-8")))


def fetch_transcript(ydl: yt_dlp.YoutubeDL, info: dict, gaps: dict,
                     translate_to: list[str] | None = None,
                     not_before: float = 0.0) -> dict | None:
    """Download the transcript as caption cues (and its translations if asked).

    YouTube's own transcript panel (get_transcript API) requires a token that
    only a real browser can produce, so the same text is taken from the
    caption track the panel shows, through the URLs yt-dlp has extracted.
    What YouTube does not have or send (no captions, a translation it does
    not offer or refuses) leaves the page without it, with the reason in
    `gaps` (NOT_SAVED_KEY) for "About this copy"; a failed download of the transcript itself raises
    TranscriptError, so that nothing is saved and the next run tries again
    (a page counts as finished once it is there).
    """
    def unavailable(reason: str) -> None:
        note_gap(gaps, gap_key(), reason, again=False)
        log(f"No transcript: {reason}.")

    track = choose_caption_track(info)
    if not track and info.get(OLANG_KEY):
        return unavailable(f"no captions in '{info[OLANG_KEY]}', the original language set "
                           f"with --olang")
    if not track:
        return unavailable("the video has no captions")
    kind, lang, formats = track
    url = json3_url(formats)
    name = re.sub(r"\s*\((Original|auto-generated)\)$", "", (formats[0].get("name") or lang))
    if not url:
        return unavailable(f"no usable captions for {name}")
    label = f"{name}, auto-generated" if kind == "auto" else name
    log(f"Downloading transcript ({label})...")
    try:
        cues = download_cues(ydl, url)
    except Exception as error:   # (a lost connection: main() stops the run on it)
        raise TranscriptError(f"could not download the transcript ({error}); nothing was saved.")
    if not cues:
        return unavailable("YouTube returned it empty")
    original = transcript_language(info)
    transcript = {"language": lang, "name": name, "kind": kind, "cues": cues,
                  "original": (bool(original) and base_language(original) == base_language(lang)
                               and not original_uncertain(info))}
    translate_to = [target for target in translate_to or []   # the original itself: not
                    if base_language(key_code(target)) != base_language(lang)]
    add_sources(ydl, info, transcript, url, translate_to, gaps, not_before)
    return transcript


def add_sources(ydl: yt_dlp.YoutubeDL, info: dict, transcript: dict, url: str | None,
                keys: list[str], gaps: dict, not_before: float = 0.0) -> None:
    """The versions in other languages (source_keys()): translations of the
    transcript's track at `url`, and subtitles of their own; for plain and
    timed without a kind, the subtitles of their own when the translation
    does not come (refused, or not there), which costs no request when it
    does (the user, 25.11.2026)."""
    translations, tracks, fallbacks = split_keys(keys)
    if translations and url:
        add_translations(ydl, info, transcript, url, translations, gaps, not_before)
    for translation, fallback in fallbacks.items():
        if translation not in translations_of(transcript) and fallback not in tracks \
                and fallback not in (transcript.get("tracks") or {}):
            what = ("subtitles by the author" if fallback.endswith(".author")
                    else "automatic subtitles")
            log(f"  Instead: {what} in '{key_code(fallback)}'.")
            tracks.append(fallback)
    add_tracks(ydl, info, transcript, tracks, gaps)


def add_tracks(ydl: yt_dlp.YoutubeDL, info: dict, transcript: dict, keys: list[str],
               gaps: dict) -> None:
    """Subtitles of their own in other languages ("pl.author", "pl.auto-generated"),
    added to the transcript's "tracks"; what does not come is noted in `gaps`
    with the reason (YouTube has none: not asked again; a failed download:
    asked again by --json write), what comes leaves them."""
    for key in keys:
        code, kind = split_code(key)
        what = ("subtitles by the author" if kind == "author" else "automatic subtitles") \
            + f" in '{code}'"
        found = subtitles_in(info, code, kind)
        address = json3_url(found[1]) if found else None
        reason, again = None, False
        if not address:
            reason = f"YouTube has no {what}"
        else:
            name = re.sub(r"\s*\((Original|auto-generated)\)$", "",
                          found[1][0].get("name") or found[0])
            log(f"Downloading subtitles ({name}, "
                + ("by the author" if kind == "author" else "auto-generated") + ")...")
            try:
                cues = download_cues(ydl, address)
            except Exception as error:
                if no_connection(error):
                    raise NoConnection(failure_reason(error)) from error
                reason, again = refusal_reason(error), True
            else:
                if cues:
                    transcript.setdefault("tracks", {})[key] = {
                        "language": found[0], "name": name, "kind": kind, "cues": cues}
                    gaps.pop(gap_key(key), None)
                    continue
                reason = f"YouTube returned {what} empty"
        log(f"No {what}: {reason}.")
        note_gap(gaps, gap_key(key), reason, again)


def json3_url(formats: list[dict]) -> str | None:
    """The json3 address among a caption track's formats."""
    return next((f["url"] for f in formats if f.get("ext") == "json3" and f.get("url")), None)


def add_translations(ydl: yt_dlp.YoutubeDL, info: dict, transcript: dict, url: str,
                     targets: list[str], gaps: dict, not_before: float = 0.0) -> None:
    """Translations of the transcript (its track at `url`) into `targets`,
    added to it; what does not come is noted in `gaps` with the reason and
    whether YouTube refused it (asked for again by --json write)."""
    translations = transcript.setdefault("translations", {})
    for target in targets:
        try:
            translation = fetch_translation(ydl, info, transcript, url, target, not_before)
            if (translations.get(target) or {}).get("own"):   # one's own, saved before
                set_own(transcript, target, translations[target])   # 25.11.2026: kept
                translations = transcript.setdefault("translations", {})
            translations[target] = translation
            gaps.pop(gap_key(target), None)
        except TranslationUnavailable as reason:
            refused = isinstance(reason, TranslationRefused)
            log(f"No translation into '{target}': {reason}.")
            if refused:
                log("  The page is made without it and counts as finished; to add the "
                    "translation later: --json write (keeps the comments) or --page ovr."
                    + (TRANSLATION_HINT if "HTTP 429" in str(reason) else " Try again later."))
            note_gap(gaps, gap_key(target), str(reason), again=refused)


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


PAGE_THUMBNAIL_RE = re.compile(r'<img class="thumbnail" src="(data:image/[^"]+)"')
# an avatar's CSS class with its key (AvatarStyles), another image with its key
PAGE_AVATAR_RE = re.compile(r'\{background-image:url\("(data:image/[^"]+)"\)\}/\*([0-9a-f]+)\*/')
PAGE_IMAGE_RE = re.compile(r'<img class="[^"]*" src="(data:image/[^"]+)" data-key="([0-9a-f]+)"')


def image_key(url: str) -> str:
    """The key of an image in the page (the first 12 hex digits of the
    SHA-256 of its address): the page made again takes the image with the
    same address from it (images_of_page())."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def on_youtube(url: str) -> bool:
    """Whether an image comes from YouTube's own image servers (i.ytimg.com,
    yt3.ggpht.com, …googleusercontent.com): taken from the old page first;
    others are asked for first (the user, 25.11.2026)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    return host.endswith((".ytimg.com", ".ggpht.com", ".googleusercontent.com"))


def cover_url(info: dict) -> str:
    """YouTube's small ready-made cover (mqdefault, 320×180), for the index."""
    return f"https://i.ytimg.com/vi/{info.get('id')}/mqdefault.jpg"


def images_of_page(path: Path | None) -> dict:
    """The images of an existing page, for the page made again (review A5:
    not downloaded again, nor lost when they cannot be): {"thumbnail": data
    URI, "cover": data URI, "keyed": {image_key(): data URI}} (the avatars
    and the other images, by the key the page keeps beside each; a page made
    by 0.12.3 or before has none), only what the page has."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore") if path else ""
    except OSError:
        return {}
    found = PAGE_THUMBNAIL_RE.search(text)
    images = {"cover": read_page_metadata(path).get("yt2html:cover")} if text else {}
    if found:
        images["thumbnail"] = found.group(1)
    images["keyed"] = {key: uri for regex in (PAGE_AVATAR_RE, PAGE_IMAGE_RE)
                       for uri, key in regex.findall(text)}
    return {kind: uri for kind, uri in images.items() if uri}


def fetch_images(urls: set, what: str, resize=None, kept: dict | None = None,
                 reuse: bool = True) -> dict[str, str]:
    """The images at `urls`: {original url: data URI}.

    `kept` holds those of the existing page ({image_key(): data URI},
    images_of_page()): with `reuse` an image of YouTube's servers (on_youtube())
    is taken from it without a request; the others are downloaded in parallel,
    and one that cannot be is taken from it too. `resize` may turn a URL into
    one for a smaller image (avatars). Images neither downloaded nor kept are
    left out; the page shows a placeholder.
    """
    kept = kept or {}
    urls = sorted(u for u in urls if u)
    images = {u: kept[image_key(u)] for u in urls
              if reuse and on_youtube(u) and image_key(u) in kept}
    wanted = [u for u in urls if u not in images]
    if images:
        log(f"{len(images)} {what} from the existing page.")
    if not wanted:
        return images
    log(f"Downloading {len(wanted)} {what}...")
    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
        results = pool.map(lambda u: fetch_image(resize(u) if resize else u), wanted)
        got = {u: d for u, d in zip(wanted, results) if d}
    spare = {u: kept[image_key(u)] for u in wanted if u not in got and image_key(u) in kept}
    images.update(got)
    images.update(spare)
    if spare:
        log(f"  {len(spare)} could not be downloaded; kept from the existing page.")
    if len(got) + len(spare) < len(wanted):
        log(f"  {len(wanted) - len(got) - len(spare)} could not be downloaded (placeholders used).")
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


def published_day(info: dict) -> dt.date | None:
    """Publication day as YouTube shows it (premiere/stream start if any), in
    the computer's local time zone when yt-dlp gives the moment."""
    for key in ("release_timestamp", "timestamp"):
        if info.get(key) is not None:
            return dt.datetime.fromtimestamp(info[key]).date()
    for key in ("release_date", "upload_date"):
        try:
            return dt.datetime.strptime(info.get(key) or "", "%Y%m%d").date()
        except ValueError:
            continue
    return None


def published_date(info: dict) -> str:
    """The Published tile: DD.MM.YYYY, or a dash."""
    day = published_day(info)
    return day.strftime(DATE_FORMAT) if day else "—"


def avatar_hue(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) % 360


def initial_of(name: str) -> str:
    """The letter on a placeholder: the first letter or digit of the name
    (not "@", "(" or an emoji); none for a name without one, and for an
    unknown author or channel (the caller passes "")."""
    return next((ch.upper() for ch in name or "" if ch.isalnum()), "")


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
    moves items whose coarse time clearly disagrees (e.g. a pinned comment,
    which build_comment_tree() then puts first).
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
    """Return (top-level comments oldest first, the pinned one first, as on
    YouTube; {parent_id: replies oldest first}). A pinned comment YouTube no
    longer sends (MISSING_SINCE_KEY, after --json refresh) stays in its place
    in time, its mark a trace of the past (the user, 20.11.2026)."""
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
    roots.sort(key=lambda c: not (c.get("is_pinned") and not c.get(MISSING_SINCE_KEY)))
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
  --tag-bg:#b4b4b4; --tag-fg:#2b2b2b; --warn-bg:#3f2a2a; --warn-fg:#c09a9a;
  --accent:#0e8f85; --on-accent:#fff; --accent-text:#3cc8b4;
  --accent-soft:rgba(14,143,133,.28); --hover:brightness(1.15);
  --planned:#9cc3e8; --chosen:#ffd54a; --audio:#6d5bd0;
  --badge-bg:rgba(0,0,0,.8); --badge-fg:#fff;
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
/* The video is no longer on YouTube: the Refreshed tile, red emerging from grey */
.fact.warn{background:var(--warn-bg)}
.fact.warn .value{color:var(--warn-fg)}
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
/* the cover as wide as the info cards' images, as tall as its shape makes
   it: a podcast's square 160x160, a course's 16:9 160x90 (YouTube uses
   both); none downloaded: 16:9, the most common shape */
.playlist .cover{display:flex;width:160px;height:auto;flex:none;
  border-radius:8px;background:var(--chip)}
.playlist .cover.img-placeholder{height:90px}
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
.transcript .note{color:var(--muted);font-size:12px;margin:-4px 0 12px}
.comments .note.closed{margin-bottom:0}
.comment{display:flex;gap:16px;margin-top:16px}
.comment .body{min-width:0;flex:1}
.comment .head{font-size:13px;line-height:18px}
.comment .author{font-weight:600}
.comment .author a{color:var(--text)}
.comment .author.owner{background:var(--chip);border-radius:12px;padding:1px 6px}
.comment .time{color:var(--muted);margin-left:4px}
.comment .gone{background:var(--tag-bg);color:var(--tag-fg);font-size:12px;margin-left:8px;
  padding:1px 7px;border-radius:9px;line-height:16px;white-space:nowrap}
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
.formats-info .formats .chosen,.formats-info .translation .chosen{color:var(--chosen)}
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
.player .speed button{width:48px;padding:4px 0;font-size:12px}
.player .speed button[aria-pressed=true]{background:var(--accent);color:var(--on-accent)}
/* Follow row: as high as the speeds, both buttons as wide as "Stop following" */
.player .buttons.follow button{width:116px;padding:4px 0;font-size:12px}
.player .buttons.follow .langs{display:contents}
.player .buttons.follow button.lang{width:40px}
.player .buttons.follow button.lang sub{font-size:.7em;line-height:0}
.player button:disabled{opacity:.45;cursor:default;filter:none}
.player .buttons.follow button[aria-pressed=true]{background:var(--accent);color:var(--on-accent)}
.player .buttons.audio button{width:52px;padding:4px 0;font-size:12px}
.player .buttons.audio button.original{width:72px}
.player .buttons.audio button[aria-pressed=true]{background:var(--audio);color:var(--on-accent)}
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
  --tag-bg:#5f5f5f; --tag-fg:#ececec; --warn-bg:#edd9d9; --warn-fg:#7d4c4c;
  --accent:#0f766e; --accent-text:#0f766e; --accent-soft:rgba(15,118,110,.16);
  --hover:brightness(.92); --planned:#2f6a9e; --chosen:#9a6700; --audio:#5b47c4;
  --badge-bg:rgba(0,0,0,.8); --badge-fg:#fff;
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
            f'.{cls}{{background-image:url("{self.data_uris[url]}")}}/*{image_key(url)}*/'
            for url, cls in self.classes.items()
        )


def channel_address(channel_id: str | None) -> str | None:
    """The lasting address of a channel, by its id ("UC…"). Not by its handle
    ("@name"): the owner can change a handle, YouTube keeps the old one for 14
    days and then someone else may take it, so in an old copy a handle link
    could lead to another channel."""
    return f"{YOUTUBE}/channel/{channel_id}" if channel_id else None


def render_comment(c: dict, children: dict, avatars: AvatarStyles,
                   video_id: str, duration, uploader: str) -> str:
    """One comment and, below it, its replies (recursively)."""
    author = c.get("author") or "(unknown)"
    author_html = esc(author)
    author_url = channel_address(c.get("author_id")) or c.get("author_url")   # (a handle)
    if author_url:
        author_html = link(author_url, author)
    owner = " owner" if c.get("author_is_uploader") else ""
    verified = ' <span class="badge" title="Verified">✓</span>' if c.get("author_is_verified") else ""

    time_text = c.get("_time_text") or ""
    approx = format_timestamp(c.get("timestamp"))
    time_title = f' title="≈ {approx}"' if approx else ""

    parts = [f'<article class="comment" id="comment-{esc(c.get("id", ""))}">',
             avatars.html(c.get("author_thumbnail"), c.get("author") or ""),
             '<div class="body">']
    if c.get("is_pinned"):
        parts.append(f'<div class="pinned">📌 Pinned by {esc(uploader)}</div>')
    gone = c.get(MISSING_SINCE_KEY)
    gone_html = (f'<span class="gone">not on YouTube on {esc(format_date(gone))}</span>'
                 if gone else "")
    parts.append(f'<div class="head"><span class="author{owner}">{author_html}</span>'
                 f'{verified}<span class="time"{time_title}>{esc(time_text)}</span>'
                 f'{gone_html}</div>')
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


def fact(label: str, value: str, extra: bool = False, warn: bool = False) -> str:
    """One fact tile; extra=True marks information added by this program
    (grey), warn=True a warning (the video no longer on YouTube)."""
    cls = "fact" + (" extra" if extra else "") + (" warn" if warn else "")
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
        return f'<img class="{cls}" src="{data_uri}" data-key="{image_key(url)}" alt="">'
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
    """One transcript section per version, in the order of the spec as this
    video resolves it (resolve_versions()); a version in a language the data
    have no translation into is left out (the Transcript row says why).

    The original's sections are marked "original" (the lines under the video
    come from them); a page without one gets the original as a hidden
    section for that, so they are always the original's. A translation's
    sections carry its language (the buttons beside Subtitles); subtitles of
    their own in another language (other lines, other times) carry neither.
    When more than one plain or timed version is shown, the timed ones get
    "with timestamps" in their grey note.
    """
    versions, _ = resolve_versions(spec, transcript)
    tracks = transcript.get("tracks") or {}
    versions = [(v, source) for v, source in versions if available(transcript, source)]
    several = sum(version != "dual" for version, _ in versions) > 1
    sections = []
    for version, language in versions:
        if version == "dual":
            sections.append(render_bilingual_transcript(
                transcript, translation_entry(transcript, language), chapters, video_id))
            continue
        timed = version == "timed"
        suffix = "with timestamps" if timed and several else ""
        if language is None:
            sections.append(render_transcript(transcript, chapters, video_id, timed,
                                              note_suffix=suffix))
        elif language in tracks:
            sections.append(render_transcript(track_transcript(tracks[language]), chapters,
                                              video_id, timed, note_suffix=suffix))
        else:
            sections.append(render_transcript(
                translated_transcript(transcript, translation_entry(transcript, language)),
                chapters, video_id, timed, note_suffix=suffix))
    if versions and all(language is not None for _, language in versions):
        sections.append(render_transcript(transcript, chapters, video_id, True, hidden=True))
    return sections


def track_transcript(track: dict) -> dict:
    """Subtitles of their own in another language as a transcript (plain:pl
    from the author's Polish subtitles): their lines and times."""
    return {"language": track.get("language"), "track": True, "cues": track.get("cues") or [],
            "name": f'{track.get("name") or track.get("language")}, '
                    + ("by the author" if track.get("kind") == "author" else "auto-generated")}


def translated_transcript(transcript: dict, translation: dict) -> dict:
    """A translation as a transcript of its own (plain:CODE, timed:CODE): the
    original's lines and times with the translated text (translated_texts())."""
    cues = transcript.get("cues") or []
    return {"language": translation.get("language"), "translation": True,
            "own": bool(translation.get("own")),
            "name": f'{translation.get("name") or translation.get("language")}, '
                    + ("own translation" if translation.get("own") else "auto-translated"),
            "cues": [{"start": cue["start"], "text": text}
                     for cue, text in zip(cues, translated_texts(cues, translation))]}


def translated_texts(cues: list[dict], translation: dict) -> list[str]:
    """The translation's text for each of the original's lines (matched by
    their start, align_translation()), with the original's change of speaker
    (">>") where the translation lost it: one rule for every version that
    shows a translation."""
    texts = align_translation(cues, translation.get("cues") or [])
    return [f">> {text}" if ">>" in (cue.get("text") or "") and ">>" not in text else text
            for cue, text in zip(cues, texts)]


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
    if transcript.get("fixed"):
        note = f"{note}, with own corrections"
    return note


def fixed_view(transcript: dict) -> dict:
    """The transcript as the page shows it: with the text of one's own
    corrections (--json merge of "Title [ID].en.fixed.srt", the user,
    25.11.2026) where the data have them, at YouTube's times; the data keep
    YouTube's text (the SRT file of --transcript srt is made from it)."""
    fixed = (transcript.get("fixed") or {}).get("cues")
    cues = transcript.get("cues") or []
    if not fixed or len(fixed) != len(cues):
        return transcript
    return {**transcript, "cues": [{**cue, "text": line["text"]} for cue, line in zip(cues, fixed)]}


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
    translated = dict(zip(map(id, cues), translated_texts(cues, translation)))
    original_label = transcript_note(transcript)
    target_label = (f'{translation.get("name") or translation.get("language")}, '
                    + ("own translation" if translation.get("own") else "auto-translated"))
    parts = [f'<div class="bi-row bi-head"><span class="time"></span>'
             f'<span class="text">{esc(original_label)}</span>'
             f'<span class="text">{esc(target_label)}</span></div>']
    for group in group_by_chapters(cues, chapters):
        if group["title"] is not None:
            parts.append(f'<h3>{esc(group["title"])}</h3>')
        for cue in group["cues"]:
            original, _ = speaker_html(cue["text"])
            translated_html, _ = speaker_html(translated[id(cue)])
            parts.append(f'<div class="bi-row" data-c="{cue_start(cue)}"><span class="time">'
                         f'{time_link(video_id, cue["start"])}</span>'
                         f'<span class="text">{original}</span>'
                         f'<span class="text">{translated_html}</span></div>')
    # The languages are named in the column headings, not again in the heading.
    language = translation.get("language") or ""
    own = " data-own" if translation.get("own") else ""   # (a variant of its own: the buttons)
    return (f'<section class="transcript bilingual" data-lang="{esc(language)}"{own}>'
            f'<h2>Transcript</h2>{"".join(parts)}</section>')


def render_transcript(transcript: dict, chapters: list[dict], video_id: str,
                      timestamps: bool, note_suffix: str = "", hidden: bool = False) -> str:
    """Transcript, split under chapter headings when the video has chapters.

    Without timestamps each part is one continuous text (cues joined with
    spaces, no added paragraphs); with timestamps every cue is its own line.
    The original's section is marked "original" (`hidden`: only there for
    the lines under the video); a translation's (translated_transcript())
    carries its language.
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
    if transcript.get("translation"):
        attributes = (f' data-lang="{esc(transcript.get("language") or "")}"'
                      + (" data-own" if transcript.get("own") else ""))
    elif transcript.get("track"):   # other lines than the original's: no language button
        attributes = f' data-track="{esc(transcript.get("language") or "")}"'
    else:
        css += " original"
        attributes = " hidden" if hidden else ""
    return (f'<section class="{css}"{attributes}><h2>Transcript{note_html(note)}</h2>'
            f'{"".join(parts)}</section>')


def uncertain_note(info: dict) -> str:
    """The grey note at the top of the transcript when original_uncertain():
    in which language it is, and what may help (no option names on pages)."""
    transcript = info.get(TRANSCRIPT_KEY) or {}
    name = transcript.get("name") or transcript.get("language") or "an unknown language"
    return (f'<div class="note uncertain">Original language uncertain: YouTube did not send '
            f'the audio tracks that tell it from the dubs, so this transcript is in '
            f'{esc(name)}, the captions YouTube opens with. Make the page again later; if '
            f'YouTube asked to confirm you are not a bot, with your browser&#x27;s cookies.</div>')


def uncertain_warning(info: dict) -> str:
    """The console's warning when original_uncertain(); the advice does not
    promise that cookies help."""
    language = transcript_language(info) or "unknown"
    what = ("the transcript and the page's language are" if info.get(TRANSCRIPT_KEY)
            else "the page's language is")
    return (f"  Warning: the original language is uncertain: YouTube sent no audio tracks to "
            f"tell it from the dubs, so {what} taken as '{language}' (the captions YouTube "
            f"opens with). If you know the original language, give it: --olang CODE; or make "
            f"the page again later; if YouTube asked you to confirm you're not a bot, try with "
            f"--cookies.")


def comment_status(info: dict) -> str:
    """How the comment download ended, for the page's metadata and the index:
    complete, limited (the comment limit reached), interrupted (Ctrl+C),
    not-downloaded (--page comments:no), turned-off (YouTube's message in place
    of the comments) or unavailable (no comments, reason unknown)."""
    if "comments" not in info:
        return "not-downloaded"
    if info.get("comments") is None:
        return "turned-off" if info.get(COMMENTS_MESSAGE_KEY) else "unavailable"
    if comments_interrupted(info):
        return "interrupted"
    downloaded = len(info["comments"])
    expected = youtube_comment_count(info)[1]
    limit = info.get(LIMIT_KEY)
    if limit is not None and downloaded >= limit and (expected is None or expected > downloaded):
        return "limited"
    return "complete"


# The set of yt2html:... tags a page has (DEVELOPMENT, *Page metadata*). A
# page with this version has every tag whose value is known; an absent tag
# means the value is unknown (refreshed, unavailable: it did not happen).
# Pages without it were made by older versions: make them again.
METADATA_VERSION = "2"


def run_moment(update: dict) -> str:
    """When a run of --json write / --json refresh was: YYYY-MM-DDTHH:MM."""
    return f"{update['date']}T{update['time']}"


def page_metadata(info: dict, archived: dt.date, cover: str | None = None,
                  data: str | None = None) -> dict[str, str]:
    """Data for the index, kept invisibly in the page's <head> as
    <meta name="yt2html:..."> (read back by read_page_metadata()). Only the
    values that are known: an empty one is left out, never written empty.
    Times are local, of the computer that downloaded or refreshed."""
    epoch = info.get("epoch")
    refreshes = [u for u in info.get(UPDATES_KEY) or [] if u.get("refreshed")]
    published = published_day(info)
    duration = info.get("duration")
    playlist = info.get(PLAYLIST_KEY) or {}
    tags = {
        "yt2html:metadata": METADATA_VERSION,
        "yt2html:video-id": info.get("id"),
        "yt2html:title": info.get("title"),
        # the moment of the download (as the Downloaded row), else the day
        "yt2html:archived": (dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M")
                             if epoch else archived.isoformat()),
        "yt2html:refreshed": run_moment(refreshes[-1]) if refreshes else None,
        "yt2html:unavailable": (run_moment(unavailable_checks(info)[-1])
                                if info.get(UNAVAILABLE_KEY) else None),
        "yt2html:published": published.isoformat() if published else None,
        "yt2html:duration": str(round(duration)) if duration is not None else None,
        "yt2html:language": transcript_language(info),
        "yt2html:comments-status": comment_status(info),
        "yt2html:comments-downloaded": str(len(info.get("comments") or [])),
        "yt2html:comments-youtube": youtube_comment_count(info)[0],  # as on the Comments tile
        # the playlist the video was downloaded from and its place there: the index
        # keeps the video when the playlist no longer has it
        "yt2html:playlist": playlist.get("id"),
        "yt2html:playlist-position": (str(playlist["position"])
                                      if playlist.get("id") and playlist.get("position")
                                      else None),
        "yt2html:data": data,   # the saved data it is made from (data_mark())
        "yt2html:cover": cover,   # last: by far the longest
    }
    return {name: value for name, value in tags.items() if value}


META_RE = re.compile(r'<meta name="(yt2html:[a-z-]+)" content="([^"]*)">')
META_READ_BYTES = 65536  # read in pieces until <title>: the metadata is above it


def data_mark(content: bytes) -> str:
    """The mark of saved data (the bytes of their file) that a page made from
    them keeps as yt2html:data: the first 12 hex digits of their SHA-256."""
    return hashlib.sha256(content).hexdigest()[:12]


# --json write or --json show, when a page's yt2html:data is not that of its data
PAGE_BEHIND_NOTE = ("  The page was not made from these saved data (after --json merge, an "
                    "interrupted run or an edit of the file); --json load makes it again from "
                    "them.")


def made_from(page: Path | None, mark: str | None) -> bool:
    """Whether the page was made from the saved data with `mark`; a page
    without yt2html:data (made by 0.12.3 or before, or without saved data) counts
    as made from them (the user, 25.11.2026: no surprise rebuilds)."""
    kept = read_page_metadata(page).get("yt2html:data") if page else None
    return not kept or kept == mark


def read_page_metadata(path: Path) -> dict[str, str]:
    """The yt2html:... metadata of a page made by this program ({} if none)."""
    head = b""
    try:
        with open(path, "rb") as file:
            while b"<title>" not in head and (piece := file.read(META_READ_BYTES)):
                head += piece
    except OSError:
        return {}
    head = head.split(b"<title>")[0].decode("utf-8", errors="ignore")
    return {name: html.unescape(value) for name, value in META_RE.findall(head)}


PLAYER_TILE = ('<button class="fact play" type="button"><div class="value">▶ Play</div>'
               '<div class="label">Player</div></button>')

# 0.25×: for language learning; 1.75× and 2×: lectures and podcasts
PLAYER_SPEEDS = ("0.25", "0.5", "0.75", "1", "1.25", "1.5", "1.75", "2")
VIDEO_EXTENSIONS = ("mp4", "webm", "m4v", "mkv")


def render_player(video_names: list[str], video_files: list[str] | None = None,
                  qualities: dict[str, str] | None = None, language: str | None = None,
                  audio_tracks: list[tuple[str, str, str, str, str]] | None = None,
                  original_label: str = "") -> str:
    """The player panel; hidden until the script finds a video file next to
    the page: `video_files` first (files with the video's id found next to the
    page when it was built), then the page's own name and `video_names`, each
    with every video extension (for videos downloaded later). `qualities`
    ({"1920x1080": "1080p"}) and `language` (of the audio in the video) give
    the tile its label once the file's size is known. `audio_tracks`
    ([(code, file, spoken name, file found, suffix)]: the extra tracks in the
    data, each with the file --video saved, the file with the video's id and
    the track's ending found next to the page when it was built, and that
    ending, ".pl.dubbed.m4a") add a row of switches under the others: the
    script tries the file found, the page's own name with the ending, then
    the file saved, so renamed tracks play too. The original first
    (`original_label` its spoken name), then one per track, only one on at a
    time. Buttons that show symbols or codes have an aria-label: the name
    screen readers and voice control use, invisible on the page."""
    speeds = "".join(f'<button type="button" data-speed="{v}" aria-pressed="{str(v == "1").lower()}" '
                     f'aria-label="Speed {v}">{v}×</button>' for v in PLAYER_SPEEDS)
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
        '<button type="button" data-do="start" title="To the start (Ctrl+←)" '
        'aria-label="To the start">|◀</button>'
        '<button type="button" data-do="back" title="10 s back (←)" '
        'aria-label="10 seconds back">−10 s</button>'
        '<button type="button" data-do="repeat" title="Previous sentence (Shift+←); '
        'click again for one more" aria-label="Previous sentence">↺</button>'
        '<button type="button" data-do="toggle" class="main" title="Play / pause (space)" '
        'aria-label="Play">▶</button>'
        '<button type="button" data-do="forward" title="10 s forward (→)" '
        'aria-label="10 seconds forward">+10 s</button>'
        '<button type="button" data-do="end" title="To the end (Ctrl+→)" '
        'aria-label="To the end">▶|</button>'
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
        + (('<div class="buttons audio">'
            f'<button type="button" class="original" data-audio="" aria-pressed="true" '
            f'aria-label="{esc(original_label or "Original audio")}">{esc(language or "orig")}</button>'
            + "".join(f'<button type="button" data-audio="{esc(file)}" data-found="{esc(found)}" '
                      f'data-suffix="{esc(suffix)}" aria-pressed="false" '
                      f'aria-label="{esc(label)}">{esc(code)}</button>'
                      for code, file, label, found, suffix in audio_tracks)
            + '</div>') if audio_tracks else '')
        + '</aside>')


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
    if (!body.classList.contains('has-video')) all('[data-audio]', panel).forEach(findTrack);
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
  var everyList = all('section.transcript').map(function (section) {
    var els = all('[data-c]', section);
    return {section: section, els: els, times: els.map(function (el) { return Number(el.dataset.c); })};
  }).filter(function (list) { return list.els.length; });
  // Follow goes through those on the page (a hidden one only feeds the subtitles).
  var lists = everyList.filter(function (list) { return !list.section.hidden; });
  function timed(list) {
    return list.section.classList.contains('with-timestamps') || list.section.classList.contains('bilingual');
  }
  // Under the video always the original, as the audio: its first transcript
  // with timestamps (dual included), else its continuous one, else the hidden one.
  var originals = everyList.filter(function (l) {
    return l.section.classList.contains('original') || l.section.classList.contains('bilingual');
  });
  var source = originals.filter(timed)[0] || originals[0];
  // Translations by language, in page order (a dual transcript's in its second
  // column), chosen by the language buttons (none at first). A language with
  // more than one (one's own and YouTube's) gets a small index on its button
  // (pl\u2081): a click on the active one goes to the next, after the last
  // off; another language keeps the one shown last (the user, 25.11.2026).
  var bilinguals = [], langAt = {};
  lists.forEach(function (l) {
    var lang = l.section.dataset.lang;
    if (!lang) return;
    if (!(lang in langAt)) {
      langAt[lang] = bilinguals.length;
      bilinguals.push({lang: lang, variants: [], chosen: 0});
    }
    var group = bilinguals[langAt[lang]], kind = l.section.hasAttribute('data-own') ? 'own' : '';
    if (!group.variants.some(function (v) { return v.kind === kind; })) {  // a source once
      l.kind = kind;
      group.variants.push(l);
    }
  });
  function column(list) { return list.section.classList.contains('bilingual') ? 1 : 0; }
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
  function label(button, group) {  // "pl", or "pl" with the variant's index
    button.textContent = group.lang;
    if (group.variants.length > 1) {
      var index = document.createElement('sub');
      index.textContent = String(group.chosen + 1);
      button.appendChild(index);
    }
  }
  var langButtons = bilinguals.map(function (group, i) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'lang';
    button.disabled = true;
    button.setAttribute('aria-pressed', 'false');
    label(button, group);
    button.title = 'Translation: ' + group.lang;
    button.addEventListener('click', function () { setLanguage(i); button.blur(); });
    langs.appendChild(button);
    return button;
  });
  function setLanguage(i) {
    // another language replaces it (with the variant shown last); the active
    // one goes to its next variant, after the last (or the only one) off
    if (i >= 0 && i === language) {
      var group = bilinguals[i];
      if (group.chosen + 1 < group.variants.length) group.chosen += 1;
      else { group.chosen = 0; language = -1; }
    } else {
      language = i;
    }
    langButtons.forEach(function (b, n) {
      b.setAttribute('aria-pressed', String(n === language));
      label(b, bilinguals[n]);
    });
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
      var translation = language >= 0 ? bilinguals[language].variants[bilinguals[language].chosen]
                                       : null;
      var j = translation ? indexAt(translation.times, t) : -1;
      boxPart.translation.textContent = j >= 0 ? textOf(translation.els[j], column(translation)) : '';
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
  // Another audio track (an m4a next to the page, downloaded with --video):
  // the video plays muted and the track follows it. Every 250 ms the gap is
  // measured and averaged over the last second (Firefox reports the time
  // coarsely); a correction (3 % faster or slower) starts above 80 ms and
  // ends below 20 ms, so the speed changes rarely (each change can click);
  // only more than 1 s off sets the track to the video's time. The file is
  // tried under the name found when the page was built, the page's own name
  // with the track's ending, then the name it was saved under (or the one
  // findTrack() found); a track none of them reads is switched off for good.
  var dub = null, gaps = [], nudging = false;
  function trackNames(button) {
    return button.dataset.file ? [button.dataset.file]
      : [button.dataset.found, page + button.dataset.suffix, button.dataset.audio]
        .filter(function (name, i, list) { return name && list.indexOf(name) === i; });
  }
  // Each track's file is looked for as soon as the video is found, under the
  // same names: the first that loads is kept (data-file); a track none of
  // them reads is greyed out at once. The switch stays in the page, so a
  // file put back works the next time the page is opened.
  function findTrack(button) {
    if (!button.dataset.audio) return;
    var names = trackNames(button);
    var test = function (i) {
      if (i >= names.length) { button.disabled = true; return; }
      var probe = new Audio();
      probe.preload = 'metadata';
      probe.onloadedmetadata = function () { probe.onerror = null; button.dataset.file = names[i]; };
      probe.onerror = function () { test(i + 1); };
      probe.src = encodeURIComponent(names[i]);
    };
    test(0);
  }
  function setAudio(button) {
    if (dub) { dub.pause(); dub = null; }
    video.muted = false;
    if (button.dataset.audio) {
      var names = trackNames(button);
      var load = function (i) {
        var track = new Audio(encodeURIComponent(names[i]));
        track.onerror = function () {
          if (dub !== track) return;   // switched to another track meanwhile
          if (i + 1 < names.length) { load(i + 1); return; }
          button.disabled = true;
          setAudio(panel.querySelector('[data-audio=""]'));
        };
        dub = track;
        track.currentTime = video.currentTime;
        track.playbackRate = video.playbackRate;
        if (!video.paused) track.play().catch(function () {});
      };
      video.muted = true;
      load(0);
    }
    all('[data-audio]', panel).forEach(function (other) {
      other.setAttribute('aria-pressed', String(other === button));
    });
  }
  all('[data-audio]', panel).forEach(function (button) {
    button.addEventListener('click', function () { setAudio(button); button.blur(); });
  });
  ['play', 'playing'].forEach(function (event) {
    video.addEventListener(event, function () {
      if (!dub) return;
      dub.currentTime = video.currentTime;
      dub.play().catch(function () {});
    });
  });
  ['pause', 'waiting', 'ended'].forEach(function (event) {
    video.addEventListener(event, function () { if (dub) dub.pause(); });
  });
  video.addEventListener('seeking', function () { if (dub) dub.currentTime = video.currentTime; });
  video.addEventListener('ratechange', function () { if (dub) dub.playbackRate = video.playbackRate; });
  setInterval(function () {
    if (!dub || video.paused) { gaps = []; return; }
    gaps = gaps.concat(dub.currentTime - video.currentTime).slice(-4);
    var off = gaps.reduce(function (a, b) { return a + b; }, 0) / gaps.length;
    if (Math.abs(off) > 1) { dub.currentTime = video.currentTime; gaps = []; return; }
    if (Math.abs(off) > 0.08) nudging = true;
    else if (Math.abs(off) < 0.02) nudging = false;
    // never below 0.25×: Firefox mutes slower sound, so at 0.25× only faster
    var rate = Math.max(0.25, video.playbackRate * (nudging ? (off > 0 ? 0.97 : 1.03) : 1));
    if (dub.playbackRate !== rate) dub.playbackRate = rate;
  }, 250);
  seek.addEventListener('input', function () { jump(Number(seek.value)); });
  video.addEventListener('timeupdate', function () {
    seek.value = video.currentTime;
    panel.querySelector('.now').textContent = clock(video.currentTime);
    update(false);
  });
  video.addEventListener('seeked', function () { update(false); });
  video.addEventListener('play', function () {
    toggle.textContent = '❚❚'; toggle.setAttribute('aria-label', 'Pause');
  });
  video.addEventListener('pause', function () {
    toggle.textContent = '▶'; toggle.setAttribute('aria-label', 'Play');
  });
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


# "Title [id].f399.mp4": one format of an unfinished --video download;
# "Title [id].temp.mp4" (or ".pl.dubbed.temp.m4a"): yt-dlp's file of a
# merge or fix-up that did not finish (prepend_extension(…, "temp"); left
# behind on Ctrl+C or a full disk, cut short). Neither is a finished file.
FORMAT_PART_RE = re.compile(r"\.(f[0-9][^.]*|temp)\.[^.]+$")


def video_files(folder: Path, ident: str | None) -> list[Path]:
    """The finished video files with the video's id in their name, sorted by
    name (files_with_id()): not the formats or the temporary file of an
    unfinished --video download (FORMAT_PART_RE).
    The page and the index take the same files."""
    return [p for p in files_with_id(folder, ident, VIDEO_FILE_EXTENSIONS)
            if not FORMAT_PART_RE.search(p.name)]


def video_files_for(info: dict, folder: Path) -> list[str]:
    """Video files next to the page with the video's id in their name, found
    when the page is built: the page tries them first, so a renamed video is
    found too (the browser itself cannot look into the folder)."""
    files = video_files(folder, info.get("id"))
    if len(files) > 1:
        log(f"Note: several videos for [{info.get('id')}] in {folder}: "
            + ", ".join(p.name for p in files) + f"; the page tries {files[0].name} first")
    return [p.name for p in files]


def audio_suffix(key: tuple) -> str:
    """The ending of an extra track's file: ".pl.dubbed.m4a" (code and role,
    so a language with two versions gives two files; without a role only the
    code)."""
    code, role = key
    return f".{code}{'.' + role if role else ''}.m4a"


def cues_srt(cues: list[dict]) -> bytes:
    """An SRT file of caption cues, one block per line of the transcript (the
    same lines at the same times): to its end, else (data saved before ends
    were kept) to the next line's start, the last one 5 s."""
    blocks = []
    for i, cue in enumerate(cues):
        start = round(float(cue.get("start") or 0) * 1000)
        end = (round(float(cue["end"]) * 1000) if cue.get("end") is not None else
               round(float(cues[i + 1]["start"]) * 1000) if i + 1 < len(cues) else start + 5000)
        blocks.append(f"{len(blocks) + 1}\n{srt_time(start)} --> {srt_time(max(end, start))}\n"
                      f"{cue.get('text') or ''}\n")
    return "\n".join(blocks).encode("utf-8")


def srt_tracks(info: dict, spec: list) -> list[tuple[str, str, list[dict]]]:
    """--transcript srt: the tracks of the versions the page shows, each once,
    as (code, kind, cues) for "Title [ID].CODE.KIND.srt": the original
    (author or auto-generated) and YouTube's translations (translated); a
    translation of one's own is not written (its .own.srt file is the
    source)."""
    transcript = info.get(TRANSCRIPT_KEY)
    if not transcript or not transcript.get("cues"):
        return []
    translations = translations_of(transcript)   # YouTube's (one's own: no file)
    tracks = {}
    kind = "auto-generated" if transcript.get("kind") == "auto" else "author"
    for version, language in resolve_versions(spec, transcript)[0]:
        if language is None or version == "dual":   # dual: the original beside it too
            tracks.setdefault((transcript.get("language") or "und", kind), transcript["cues"])
        own_tracks = transcript.get("tracks") or {}
        if language in own_tracks:   # subtitles of their own: "pl.author"
            track = own_tracks[language]
            tracks.setdefault((track.get("language") or split_code(language)[0],
                               track.get("kind")), track.get("cues") or [])
        elif language in translations:
            tracks.setdefault((language, "translated"), translations[language].get("cues") or [])
    return [(code, kind, cues) for (code, kind), cues in tracks.items()]


def write_srt_files(info: dict, spec: list, page: Path) -> list[str]:
    """The SRT files of srt_tracks() next to the page, as part of it: a file
    with the video's id and the ending (renamed ones too) is written over
    only when its text differs (the transcript changed); else it is left as
    it is, its date too. Returns the files' endings, for the console."""
    written = []
    for code, kind, cues in srt_tracks(info, spec):
        suffix = sub_suffix(code, kind)
        path = file_by_suffix(page.parent, info.get("id"), suffix, "subtitle files") or \
            page.parent / (page.stem + suffix)
        content = cues_srt(cues)
        try:
            same = path.read_bytes() == content
        except OSError:
            same = False
        if not same:
            write_atomically(path, content)
            log(f"Saved subtitles: {path}")
            written.append(suffix)
    return written


def sub_suffix(code: str, kind: str) -> str:
    return f".{code}.{kind}.srt"


def file_by_suffix(folder: Path, ident: str | None, suffix: str, what: str) -> Path | None:
    """The file next to the page with the video's id and this ending, by the
    rule for all files with an id (files_with_id()): renamed files are found
    as long as both parts stay."""
    return file_with_id(folder, ident, (suffix.lower(),), what)


def audio_files_for(info: dict, folder: Path) -> dict:
    """The files of the extra tracks in the data, found next to the page
    when it is built: {(code, role): file name}."""
    found = {}
    for track in (info.get(VIDEO_KEY) or {}).get("extra_audio") or []:
        key = (track["audio"], track.get("role"))
        path = file_by_suffix(folder, info.get("id"), audio_suffix(key), "audio files")
        if path:
            found[key] = path.name
    return found


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
    text = f"{when} from {source}" + (f" with {' and '.join(programs)}" if programs else "")
    updates = info.get(UPDATES_KEY) or []
    unavailable = info.get(UNAVAILABLE_KEY)
    last_check = max((i for i, u in enumerate(updates) if u.get("unavailable")), default=None)
    for i, update in enumerate(updates):   # the later runs; what they did: in the data
        event = ("no longer on YouTube" if update.get("unavailable") else
                 "refreshed" if update.get("refreshed") else "updated")
        text += (f"; {event} {esc(format_date(update['date']))} "
                 f"{esc(update.get('time') or '')}").rstrip()
        if unavailable and i == last_check:   # YouTube's words, once, while it lasts
            text += f" “{esc(unavailable.get('reason') or '')}”"
    return text


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
                    for version, language in spec)


def video_parameters(info: dict) -> str:
    """The --video parameters that shaped the files of the data, as they
    would be given today: defaults, "ovr" (how, not what) and, from data
    saved before --transcript srt, "again" and the sub: items left out."""
    return ",".join(item.strip() for item in
                    ((info.get(VIDEO_KEY) or {}).get("parameters") or "").split(",")
                    if item.strip() and item.strip() not in ("ovr", "again",
                                                             f"{DEFAULT_MAX_HEIGHT}p")
                    and not item.strip().startswith("sub:"))


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
    if VIDEO_KEY in info:   # the files are part of the copy; "ovr" is not (how, not what)
        kept = video_parameters(info)
        parts += ["--video"] + ([kept] if kept else [])
    # --page: what differs from its defaults; ovr is not part of the copy (how, not what)
    page = []
    if "comments" not in info:
        page.append("comments:no")
    elif info.get(LIMIT_KEY) is None:
        page.append("comments:all")
    elif info[LIMIT_KEY] != DEFAULT_COMMENTS:
        page.append(f"comments:{info[LIMIT_KEY]}")
    if not show_snapshot:
        page.append("snap:no")
    if CARDS_KEY not in info:
        page.append("cards:no")
    if no_avatars:
        page.append("avatars:no")
    if theme != "auto":
        page.append(f"theme:{theme}")
    if page:
        parts += ["--page", ",".join(page)]
    # a translation of one's own does not come from YouTube: the versions
    # that show one are left out, and --transcript too when nothing else is
    transcript = info.get(TRANSCRIPT_KEY) or {}
    spec = [(version, language) for version, language in spec
            if not (language and split_code(version_source(transcript, version, language))[1]
                    == "own")]
    if TRANSCRIPT_KEY in info and spec:   # the option alone means plain
        versions = format_transcript_spec(spec)
        parts += ["--transcript"] + ([versions] if versions != "plain" else [])
    if info.get(OLANG_KEY):
        parts += ["--olang", info[OLANG_KEY]]
    browser = (info.get(DOWNLOAD_KEY) or {}).get("cookies_from_browser")
    if browser:
        parts += ["--cookies", browser]
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
        reason = ((info.get(NOT_SAVED_KEY) or {}).get(gap_key()) or {}).get("reason")
        return f"none: {esc(reason)}" if reason else "none"
    kind = "auto-generated" if transcript.get("kind") == "auto" else "by the author"
    # (data saved before the check of original_uncertain() may say original)
    features = (f"original language, {kind}"
                if transcript.get("original", True) and not original_uncertain(info)
                else f"{kind}; original language unknown")
    if info.get(OLANG_KEY):
        features += ", the original set in the command"
    name = transcript.get("name") or transcript.get("language") or ""
    def translated(source):
        translation = translation_entry(transcript, source) or {}
        kind = "own translation" if split_code(source)[1] == "own" else "auto-translated"
        return f'{esc(translation.get("name") or split_code(source)[0])} ({kind})'

    tracks = transcript.get("tracks") or {}

    def own_subtitles(key):
        track = tracks[key]
        kind = "by the author" if track.get("kind") == "author" else "auto-generated"
        return f'{esc(track.get("name") or track.get("language"))} ({kind})'

    resolved, notes = resolve_versions(spec, transcript)
    versions = [version if language is None else
                f"dual with {translated(language)}" if version == "dual" else
                f"{version} in {own_subtitles(language)}" if language in tracks else
                f"{version} in {translated(language)}"
                for version, language in resolved if available(transcript, language)]
    text = f"{esc(name)} ({features}); {plural(len(versions), 'version')}: {', '.join(versions)}"
    missing = transcript_gaps(info)
    absent = [(version, language) for version, language in resolved
              if not available(transcript, language)]

    def why(language):
        entry = missing.get(language) or missing.get(split_code(language)[0])
        return f" ({esc(gap_text(entry))})" if entry else ""

    if absent:   # "not available: dual:fi (YouTube offers no translation …)"
        text += "; not available: " + ", ".join(f"{version}:{esc(language)}{why(language)}"
                                                for version, language in absent)
    if notes:
        text += "; " + "; ".join(esc(note) for note in notes)
    fixed = transcript.get("fixed") or {}
    if fixed.get("cues"):
        text += (f"; the original with own corrections ({esc(fixed.get('file') or '')}, "
                 f"{format_date(fixed['date']) if fixed.get('date') else ''})"
                 .replace(", )", ")"))
    if ("srt", None) in spec:
        files = [f"{code} ({kind})" for code, kind, _ in srt_tracks(info, spec)]
        text += "; SRT files next to the page: " + (esc(", ".join(files)) if files else "none")
    return text


def format_date(iso: str) -> str:
    """"2026-11-20" -> "20.11.2026"."""
    return dt.date.fromisoformat(iso).strftime(DATE_FORMAT)


def gap_text(entry: dict) -> str:
    """Why something is not saved (a NOT_SAVED_KEY entry); one asked for
    again (a refusal) with the day it was refused."""
    if entry.get("again") and entry.get("date"):
        return f"{entry['reason']}, {format_date(entry['date'])}"
    return entry.get("reason") or "unknown"


def about_comments(info: dict) -> str:
    status = comment_status(info)
    if status == "not-downloaded":
        return "left out"
    if status in ("turned-off", "unavailable"):
        return status.replace("-", " ")
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
    gone = gone_comments(comments)
    return (f"{amount}; {plural(len(roots), 'thread')}, "
            f"{replies} {'reply' if replies == 1 else 'replies'}; {state}"
            + (f"; {format_int(gone)} no longer on YouTube" if gone else "")
            + (f"; {'turned off' if closed['turned_off'] else 'no longer shown'} on YouTube "
               f"as of {esc(format_date(closed['from']))}"
               if (closed := comments_closed(info)) else ""))


def comments_closed(info: dict) -> dict | None:
    """The entry of COMMENTS_CLOSED_KEY when the comments are off on YouTube now."""
    closed = info.get(COMMENTS_CLOSED_KEY) or []
    return closed[-1] if closed and "until" not in closed[-1] else None


def closed_note(closed: dict) -> str:
    when = esc(format_date(closed["from"]))
    if closed.get("turned_off"):
        return (f"As of {when}, comments on this video are turned off on YouTube; "
                "the ones below can no longer be seen there.")
    return (f"As of {when}, YouTube no longer shows comments on this video; "
            "the ones below can no longer be seen there.")


def gone_comments(comments: list[dict]) -> int:
    """How many saved comments --json refresh no longer found on YouTube."""
    return sum(1 for c in comments if c.get(MISSING_SINCE_KEY))


def unavailable_checks(info: dict) -> list[dict]:
    """The runs of --json refresh that found the video no longer on YouTube."""
    return [u for u in info.get(UPDATES_KEY) or [] if u.get("unavailable")]


def refreshed_tile(info: dict, archived: dt.date) -> str:
    """The Refreshed tile: the day of the last --json refresh, if not the copy's
    day; for a video no longer on YouTube the day of the last check, in the
    warning colour, always."""
    if info.get(UNAVAILABLE_KEY):
        day = dt.date.fromisoformat(unavailable_checks(info)[-1]["date"])
        return fact("Refreshed", day.strftime(DATE_FORMAT), extra=True, warn=True)
    refreshed = last_refresh(info)
    return fact("Refreshed", refreshed.strftime(DATE_FORMAT), extra=True) \
        if refreshed and refreshed != archived else ""


def last_refresh(info: dict, comments: bool = False) -> dt.date | None:
    """The day of the last --json refresh (that refreshed the comments)."""
    days = [u["date"] for u in info.get(UPDATES_KEY) or []
            if u.get("refreshed") and (not comments or "comments" in u)]
    return dt.date.fromisoformat(days[-1]) if days else None


def comments_date(info: dict, archived: dt.date) -> dt.date:
    """The day the comment times ("2 days ago") count from."""
    return last_refresh(info, comments=True) or archived


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


def track_order(key: tuple) -> tuple:
    """Audio tracks by role (known ones first, in their order), then language."""
    language, role = key
    rank = AUDIO_ROLES.index(role) if role in AUDIO_ROLES else len(AUDIO_ROLES) + (role is None)
    return rank, role or "", language


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
        # No language in the audio formats (a single track): the original
        # language as the transcript knows it (the watch page's default
        # captions for that track), the same guess the Transcript row shows.
        self._inferred = (transcript_language(info)
                          if self.audio and all(not f.get("language") for f in self.audio)
                          else None)
        self._has_original = any(audio_kind(f) == "original"
                                 or f.get("language_preference") == 10 for f in self.audio)
        self.original = next((self.track(f) for f in self.audio
                              if self.role_of(f) == "original"), None)
        if info.get(OLANG_KEY):   # --olang: the track in that language is the original
            self.original = next((self.track(f) for f in self.audio
                                  if base_language(audio_language(f)) == base_language(info[OLANG_KEY])
                                  and self.role_of(f) != "descriptive"), self.original)
        # The main audio: the original (aac, else any codec); else an ordinary
        # track, an audio description only when there is nothing else; never
        # a Premium format.
        regular = [f for f in self.audio if not is_premium(f)]
        first = self.original or (self.track(self.audio[0]) if self.audio else None)
        spoken = [f for f in regular if self.role_of(f) != "descriptive"] or regular
        self.main_audio = (self.audio_for(first, "aac") or self.audio_for(first)
                           or (self.best(spoken) if spoken else None))
        self._audio_bytes = format_size(info, self.main_audio) if self.main_audio else 0
        # Tracks by role (known ones first, in their order), then language;
        # the ones a download can add: an ordinary aac format (m4a), not the
        # track already in the video.
        self.tracks = sorted(set(map(self.track, self.audio)), key=track_order)
        self.in_video = self.track(self.main_audio) if self.main_audio else None
        self.extra = [key for key in self.tracks
                      if key != self.in_video and self.audio_for(key, "aac")]
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
        return fmt.get("language") or self._inferred or UNKNOWN_LANGUAGE, self.role_of(fmt)

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
    downloaded = info.get(VIDEO_KEY) or {}   # what --video took: marked yellow
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
    # What --video downloaded (yellow): its image cell and its audio's cell.
    chosen = [(v["row"], v["column"]) for v in variants
              if v["format"].get("format_id") == (downloaded.get("video") or {}).get("video_format")]
    image = {"columns": [label(k) for k in columns] + (["HLS (sizes unknown)"] if hls else []),
             "rows": rows, "picks": picks, "chosen": chosen,
             "audio": (track_text(track(main_audio)[0],
                              codec_name(main_audio.get("acodec"), AUDIO_CODECS))
                       if main_audio else None)}

    tracks = choice.tracks
    audio_keys = [(codec, premium) for premium in (False, True) for codec in ("aac", "opus")
                  if any(audio_for(key, codec, premium) for key in tracks)]

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
    # subtitles --video downloaded (yellow): (kind of cell, code)
    taken_subs = {("author" if s["kind"] == "author" else "auto", s["code"])
                  for s in downloaded.get("subtitles") or [] if s["kind"] != "translated"}

    def codes_cell(row, kind):
        return ", ".join(f'<span class="chosen">{esc(code)}</span>' if (kind, code) in taken_subs
                         else esc(code) for code in row[kind]) or None

    language_rows = []
    for row in table_rows:
        code, role = row["key"]
        known = code != UNKNOWN_LANGUAGE   # unknown: no code, the name "unknown"
        if row.get("alone"):
            cells = [None] * len(audio_keys)
            name = language_name(info, [], code)
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
    translations = [s for s in downloaded.get("subtitles") or [] if s["kind"] == "translated"]
    if translations:   # "; downloaded: de, pl (from en)": always one source, the transcript's
        sources = [s["source"] for s in translations if s.get("source")]
        translation += ("; downloaded: " + ", ".join(f'<span class="chosen">{esc(s["code"])}</span>'
                                                     for s in translations)
                        + (f" (from {esc(sources[0])})" if sources else ""))
    # the track in the video and the extra tracks: by their format when the
    # data say which, else (a file only found) the track's aac, as --video takes it
    extra = downloaded.get("extra_audio") or []
    taken = ({(downloaded.get("video") or {}).get("audio_format")}
             | {a.get("audio_format") for a in extra})
    found = {(a["audio"], a.get("role")) for a in extra if not a.get("audio_format")}
    audio_chosen = []
    for f in audio:
        key = (codec_name(f.get("acodec"), AUDIO_CODECS), is_premium(f))
        if ((f.get("format_id") in taken or (track(f) in found and key == ("aac", False)))
                and key in audio_keys):
            audio_chosen.append((table_rows.index(rows_of[tracks.index(track(f))]),
                                 audio_keys.index(key)))
    languages_table = {"columns": [codec + (" Premium" if premium else "")
                                   for codec, premium in audio_keys],
                       "rows": language_rows, "picks": audio_picks, "chosen": audio_chosen,
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
    in_video, extra = choice.in_video, choice.extra
    known = sorted(filter(None, (format_size(info, audio_for(key, "aac")) for key in extra)))
    extra_sizes = list(dict.fromkeys(map(human_size, known)))   # smallest to largest
    return {"image": image, "languages": languages_table,
            "variants": variants, "in_video": in_video,
            "in_video_codec": codec_name(main_audio.get("acodec"), AUDIO_CODECS) if main_audio
            else None,
            "extra_audio": [(key[0], key[1]) for key in extra], "extra_sizes": extra_sizes,
            "subtitles": subtitle_tracks(info),
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
    used), else from the name of the author's subtitles in that code
    ("Portuguese (Brazil)"), else from YouTube's list of translation
    languages (full code, then the language alone), else the code."""
    for f in formats:
        if is_hls(f):
            continue
        name = re.sub(r"\s+(original|descriptive)\b.*$|\s+\(default\)$", "",
                      (f.get("format_note") or "").split(",")[0]).strip()
        if name and name != code and name.lower() not in QUALITY_WORDS:
            return name
    for f in (info.get("subtitles") or {}).get(code) or []:
        if f.get("name"):
            return f["name"]
    names = info.get(TRANSLATIONS_KEY) or {}
    return names.get(code) or names.get(code.split("-")[0]) or code


def formats_table(title: str, head: list[str], rows: list[list], sizes: range = range(0),
                  picks: set = frozenset(), chosen: set = frozenset()) -> str:
    """A small table of the Video formats section; an empty cell is a dash.
    Columns in `sizes` hold sizes: right-aligned, never wrapped; the rest
    wraps. Cells in `picks` ((row, column) of the data rows) are the planned
    default (blue), cells in `chosen` what was downloaded (yellow, which wins
    when they are the same cell)."""
    def cell(tag, r, i, value):
        classes = " ".join(["size"] * (i in sizes) + ["chosen"] * ((r, i) in chosen)
                           + ["planned"] * ((r, i) in picks and (r, i) not in chosen))
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


def own_translations_line(own: list[tuple[str, str]]) -> str:
    """"own translations: de, en (added 22.11.2026), fr (added 25.11.2026)":
    the codes yellow (this copy has them), by the day they were added."""
    days = {}
    for language, day in sorted(own, key=lambda item: (item[1], item[0])):
        days.setdefault(day, []).append(f'<span class="chosen">{esc(language)}</span>')
    return "own translations: " + ", ".join(
        f'{", ".join(codes)} (added {esc(format_date(day))})' for day, codes in days.items())


def render_video_formats(info: dict, own: list[tuple[str, str]] = ()) -> str:
    """The Video formats section above "About this copy": folded to one grey
    line with the summary; open, the tables, and under them the translations
    of one's own the page shows (`own`: [(language, day added)]). Without
    formats, one line that says so (it looks like a fault of the page
    otherwise)."""
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
                                   {(r, 2 + c) for r, c in image["picks"]},
                                   {(r, 2 + c) for r, c in image["chosen"]}))
    if languages["rows"]:
        parts.append(formats_table("languages",
                                   ["code", "language", "audio"] + languages["columns"]
                                   + ["subtitles by the author", "auto-generated subtitles"],
                                   languages["rows"], range(3, 3 + len(languages["columns"])),
                                   {(r, 3 + c) for r, c in languages["picks"]},
                                   {(r, 3 + c) for r, c in languages["chosen"]}))
    parts.append(f'<div class="translation">{languages["translation"]}</div>')
    if own:
        parts.append(f'<div class="translation">{own_translations_line(own)}</div>')
    rest = f'<span class="rest"> · {overview["summary"]}</span>' if overview["summary"] else ""
    return (f'<details class="formats-info"><summary>Video formats{rest}</summary>\n'
            + "".join(parts) + "</details>")


def about_video_file(info: dict, video_files: list[str] | None = None,
                     video_size: int | None = None) -> str:
    """The "Video file" row: the files --video downloaded, by day (variant
    and size of the video, the extra tracks, the subtitles), then the files
    only found next to the page (a video file found when the page was built
    counts too), else "not downloaded"; then how the list of formats came
    about (Deno, signed in). The planned default shows in the tables (marked)
    and in --video-formats, not here."""
    overview = video_overview(info)
    downloaded = info.get(VIDEO_KEY) or {}
    video = downloaded.get("video")
    extra = downloaded.get("extra_audio") or []
    subs = downloaded.get("subtitles") or []

    def size(value):
        return esc(human_size(value).lstrip("~"))

    def files(video_text, tracks, subtitles):
        # "…; extra audio m4a: pl (dubbed), 69 MB; subtitles srt: en (author)"
        parts = [video_text] if video_text else []
        if tracks:
            parts.append("extra audio m4a: "
                         + ", ".join(esc(a["audio"]) + (f" ({esc(a['role'])})" if a.get("role") else "")
                                     for a in tracks)
                         + f", {size(sum(a['size'] for a in tracks))}")
        if subtitles:   # no sizes (small)
            parts.append("subtitles srt: "
                         + ", ".join(f"{esc(s['code'])} ("
                                     + ("translated" + (f" from {esc(s['source'])}" if s.get("source")
                                                        else "") if s["kind"] == "translated"
                                        else esc(s["kind"])) + ")" for s in subtitles))
        return "; ".join(parts)

    groups = []
    for day in sorted({e["date"] for e in [video or {}, *extra, *subs] if e.get("date")}):
        same = video and video.get("date") == day
        text = (f"{esc(video['quality'])} {esc(video['codec'])} + "
                f"{track_text(video.get('audio') or UNKNOWN_LANGUAGE, esc(video.get('audio_codec') or ''))}"
                f", mp4, {size(video.get('size'))}") if same else ""
        groups.append(f"downloaded {dt.date.fromisoformat(day).strftime(DATE_FORMAT)}: "
                      + files(text, [a for a in extra if a.get("date") == day],
                              [s for s in subs if s.get("date") == day]))
    text = ""
    if not (video and video.get("date")):   # a video file only found
        name, found_size = ((video_files[0], video_size) if video_files else
                            (video["file"], video.get("size")) if video else (None, None))
        text = (f"video {esc(name)}" + (f", {size(found_size)}" if found_size else "")) if name else ""
    found = files(text, [a for a in extra if not a.get("date")], [s for s in subs if not s.get("date")])
    if found:
        groups.append(f"next to the page: {found}")
    text = "; ".join(groups) or "not downloaded"
    if overview is None:
        return f"{text}; {NO_FORMATS}"
    text += f"; Deno at download: {overview['deno']}"
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
                                             "the page again with --page ovr" if saved
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
    lines += audio_lines(overview) if overview["in_video"] else ["audio: none"]
    tracks = overview["subtitles"]
    if not tracks["author"] and not tracks["auto-generated"]:
        return lines + ["subtitles: none"]
    return lines + ["subtitles (srt):"] + [f"  {SUB_KIND_TEXT[kind]}: {', '.join(tracks[kind])}"
                                           for kind in ("author", "auto-generated") if tracks[kind]]


def audio_lines(overview: dict) -> list[str]:
    """The audio block of --video-formats: the track in the video and those
    a download can add (with a main audio only)."""
    in_video, extra, sizes = overview["in_video"], overview["extra_audio"], overview["extra_sizes"]
    lines = []
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
    return lines + [f"  {line}" for line in extra_track_lines(extra)]


def extra_track_lines(extra: list) -> list[str]:
    """The tracks a download can add, one line per role: "auto-dubbed: ar, bn, …"."""
    return [f"{role or 'tracks'}: " + ", ".join(named_code(code) for code, r in extra if r == role)
            for role in dict.fromkeys(role for _, role in extra)]


def list_formats(args: argparse.Namespace) -> int:
    """--video-formats: for each source, the formats a download can take,
    printed; no page is made. Playlists and channels are not listed."""
    failed = left = listed = 0
    for source in args.sources:
        try:
            if not (args.load_json or is_channel_url(source) or playlist_id_from_url(source)):
                taking(args, video_id_from_url(source) or source)
        except TakeReached:
            left += 1
            continue
        if listed:
            print()
        listed += 1
        try:
            if args.load_json:
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
        print("\n".join(format_list(info, saved=args.load_json)), flush=True)
        if not args.load_json and not info[DOWNLOAD_KEY]["deno"]:
            log("Note: no JavaScript runtime (Deno) found, so YouTube may have offered "
                "more formats than listed.")
    if left:
        log(take_note(args, left))
    return 1 if failed else 0


# --json show (the user's decisions, 25.11.2026): what the saved data hold,
# by the four options, with the files next to them; read only, without YouTube.

SRT_KIND = re.compile(r"\.([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)\."
                      r"(author|auto-generated|translated|own|fixed)\.srt$", re.IGNORECASE)
SHOW_INDENT = 14   # "  Next to it: " and the lines under it


def show_lines(label: str, lines: list[str]) -> list[str]:
    """One part of a video's block: the label, then its lines under each other."""
    lines = lines or ["none"]
    head = f"  {label + ':':<{SHOW_INDENT - 2}}"
    return [head + lines[0]] + [" " * SHOW_INDENT + line for line in lines[1:]]


def show_data(info: dict) -> list[str]:
    """The Data part: the download and the later runs."""
    epoch = info.get("epoch")
    when = (date_time(dt.datetime.fromtimestamp(epoch)) if epoch else
            format_date(info[ARCHIVED_KEY]) if info.get(ARCHIVED_KEY) else "date unknown")
    version = (info.get(DOWNLOAD_KEY) or {}).get("yt2html")
    lines = [f"downloaded {when}" + (f" (yt2html {version})" if version else "")]
    for update in info.get(UPDATES_KEY) or []:
        event = ("no longer on YouTube" if update.get("unavailable") else
                 "refreshed" if update.get("refreshed") else "updated")
        done = list(update.get("added") or [])
        comments = update.get("comments") or {}
        done += [plural(comments[key], word) + after for key, word, after in
                 (("new", "new comment", ""), ("gone", "comment", " gone"),
                  ("back", "comment", " back")) if comments.get(key)]
        lines.append(f"{event} {format_date(update['date'])}"
                     + (f" ({', '.join(done)})" if done else ""))
    playlist = info.get(PLAYLIST_KEY)
    if playlist:
        lines.append(f"playlist: video {playlist.get('position') or '?'} of "
                     f"{playlist.get('count') or '?'} in "
                     f"{playlist.get('title') or playlist.get('id') or '?'}")
    unavailable = info.get(UNAVAILABLE_KEY)
    if unavailable:
        lines.append(f"no longer on YouTube: {unavailable.get('reason') or 'reason unknown'}")
    return lines


def show_page(info: dict) -> list[str]:
    """The Page part (--page): the comments, chapters and info cards."""
    status = comment_status(info)
    if status == "not-downloaded":
        comments = "comments left out"
    elif status in ("turned-off", "unavailable"):
        comments = f"comments {status.replace('-', ' ')}"
    else:
        saved = info["comments"]
        count_text, _ = youtube_comment_count(info)
        comments = f"{plural(len(saved), 'comment')}" + (f" of {count_text}" if count_text else "")
        comments += {"complete": "", "interrupted": ", interrupted (Ctrl+C)",
                     "limited": f", limited to {info.get(LIMIT_KEY)}"}[status]
        gone = sum(1 for c in saved if c.get(MISSING_SINCE_KEY))
        if gone:
            comments += f", {gone} not on YouTube"
        closed = info.get(COMMENTS_CLOSED_KEY) or []
        if closed and not closed[-1].get("until"):
            comments += f", turned off on YouTube since {format_date(closed[-1]['from'])}"
    parts = [comments]
    chapters = next((m.get("items") or [] for m in marker_lists_of(info)
                     if m.get("id") == "chapters"), [])
    if chapters:
        parts.append(plural(len(chapters), "chapter"))
    cards = info.get(CARDS_KEY)
    if cards:
        parts.append(plural(len(cards), "info card"))
    return [", ".join(parts)]


def show_versions(transcript: dict) -> list[str]:
    """The --transcript versions --json load can show from the data: plain
    and timed, then for each source the shortest version that takes it
    (dual for a translation, plain for subtitles of their own)."""
    if not transcript.get("cues"):
        return []
    versions = ["plain", "timed"]
    youtube, own = translations_of(transcript), own_of(transcript)
    for code in sorted(set(youtube) | set(own)):
        if code in own:
            versions.append(f"dual:{code}")
        if code in youtube:
            versions.append(f"dual:{code}.translated" if code in own else f"dual:{code}")
    versions += [f"plain:{key}" for key in sorted(transcript.get("tracks") or {})]
    return versions


def show_transcript(info: dict) -> list[str]:
    """The Transcript part (what is not saved: show_not_saved())."""
    transcript = info.get(TRANSCRIPT_KEY)
    if not transcript:
        return ["none"]
    kind = "auto-generated" if transcript.get("kind") == "auto" else "by the author"
    name = transcript.get("name") or transcript.get("language") or "language unknown"
    lines = [f"{transcript.get('language') or '?'} ({name}, {kind}), "
             f"{plural(len(transcript.get('cues') or []), 'line')}"]
    youtube, own = translations_of(transcript), own_of(transcript)
    shown = []
    for code in sorted(set(youtube) | set(own)):
        if code in youtube:
            shown.append(f"{code} (YouTube)")
        if code in own:
            day = (own[code].get("own") or {}).get("date")
            shown.append(f"{code} (own" + (f", {format_date(day)}" if day else "") + ")")
    if shown:
        lines.append("translations: " + ", ".join(shown))
    fixed = transcript.get("fixed") or {}
    if fixed.get("cues"):
        lines.append(f"corrections: {fixed.get('file') or 'file unknown'}"
                     + (f" ({format_date(fixed['date'])})" if fixed.get("date") else ""))
    tracks = transcript.get("tracks") or {}
    if tracks:
        lines.append("other subtitles: " + ", ".join(
            f"{track.get('language') or split_code(key)[0]} ({track.get('kind') or split_code(key)[1]})"
            for key, track in sorted(tracks.items())))
    lines.append("possible: " + (", ".join(show_versions(transcript)) or "none"))
    return lines


def show_not_saved(info: dict) -> tuple[list[str], list[str]]:
    """The Not saved part (NOT_SAVED_KEY), and the hints for what is asked
    for again."""
    lines, hints = [], []
    for key, entry in sorted((info.get(NOT_SAVED_KEY) or {}).items()):
        day = f", {format_date(entry['date'])}" if entry.get("date") else ""
        lines.append(f"{key}: {entry.get('reason') or 'reason unknown'}{day}"
                     + ("" if entry.get("again") else " (not asked for again)"))
        kind, _, source = key.partition(" ")
        if entry.get("again") and source and kind == "transcript":
            hints.append(f"to try again: yt2html {info.get('id')} --json write --transcript "
                         f"plain," + (f"plain:{source}" if "." in source else f"dual:{source}"))
        elif entry.get("again") and kind == "audio":
            hints.append(f"to try again: yt2html {info.get('id')} --json write --video "
                         f"audio:{source}")
    return lines, hints


def show_video(info: dict) -> list[str]:
    """The Video part: what --video took, as the data describe it."""
    record = info.get(VIDEO_KEY)
    if not record:
        return ["none"]
    video = record.get("video") or {}
    lines = []
    if video:
        what = " ".join(str(v) for v in (video.get("quality"), video.get("codec")) if v)
        if video.get("audio"):
            what += f" + {video['audio']}" + (f" {video['audio_codec']}"
                                               if video.get("audio_codec") else "")
        when = format_date(video["date"]) if video.get("date") else "found next to the page"
        parameters = video_parameters(info)
        lines.append(f"{what or video.get('file') or 'video'}, {when}"
                     + (f" (--video {parameters})" if parameters else ""))
    extra = record.get("extra_audio") or []
    if extra:
        lines.append("extra audio: " + ", ".join(
            track["audio"] + (f" ({track['role']})" if track.get("role") else "")
            for track in extra))
    subtitles = record.get("subtitles") or []
    if subtitles:
        lines.append("subtitles (--video sub:, before 0.11.0): " + ", ".join(
            f"{s.get('code')} ({s.get('kind')})" for s in subtitles))
    return lines or ["none"]


def show_files(info: dict, folder: Path, mark: str | None = None) -> tuple[list[str], dict]:
    """The Next to it part: the page (whether it was made from these data,
    `mark`: made_from()), the video files (whether the data describe them:
    the Video file row and the yellow marks need it), the extra audio and the
    SRT files; and what the summary counts."""
    ident = info.get("id")
    record = info.get(VIDEO_KEY) or {}
    video = record.get("video") or {}
    pages = files_with_id(folder, ident, (".html",))
    found = {"page": bool(pages), "behind": bool(pages) and not made_from(pages[0], mark)}
    parts = ["no page" if not pages else
             "page (not made from these data)" if found["behind"] else "page"]
    videos = video_files(folder, ident)
    found["described"] = False
    for path in videos:
        same = bool(video.get("quality")) and video.get("size") == path.stat().st_size
        found["described"] = found["described"] or same
        parts.append(f"{path.suffix.lstrip('.')} ({'described' if same else 'not described'})")
    missing = [video["file"]] if video.get("file") and not videos else []
    for track in record.get("extra_audio") or []:
        key = (track["audio"], track.get("role"))
        if file_by_suffix(folder, ident, audio_suffix(key), "audio files"):
            parts.append(audio_suffix(key).lstrip("."))
        else:
            missing.append(track.get("file") or audio_suffix(key).lstrip("."))
    srt = files_with_id(folder, ident, (".srt",))
    if srt:
        kinds = [f"{m.group(1)} ({m.group(2).lower()})" if (m := SRT_KIND.search(p.name))
                 else p.name for p in srt]
        parts.append(f"{plural(len(srt), 'SRT')}: {', '.join(kinds)}")
    lines = [", ".join(parts)]
    if missing:
        lines.append("missing: " + ", ".join(missing))
    found["missing"] = bool(missing)
    return lines, found


def show_block(info: dict, data_path: Path) -> tuple[list[str], dict]:
    """The block of one video, and what the summary counts of it."""
    size = data_path.stat().st_size
    size_text = f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{-(-size // 1000)} kB"
    lines = [f"{info.get('title') or '(no title)'} [{info.get('id') or '?'}]  "
             f"({data_path.name}, {size_text})"]
    gaps, hints = show_not_saved(info)
    files, found = show_files(info, data_path.parent, data_mark(data_path.read_bytes()))
    lines += (show_lines("Data", show_data(info)) + show_lines("Page", show_page(info))
              + show_lines("Transcript", show_transcript(info))
              + show_lines("Video", show_video(info))
              + (show_lines("Not saved", gaps) if gaps else [])
              + show_lines("Next to it", files))
    if not found["page"] or found["behind"]:
        hints.append(f'to make the page{" from these data" if found["page"] else ""}: '
                     f'yt2html "{data_path.name}" --json load --transcript')
    lines += [f"  ({hint})" for hint in hints]
    record = info.get(TRANSCRIPT_KEY) or {}
    video = (info.get(VIDEO_KEY) or {}).get("video") or {}
    found.update(not_saved=bool(gaps),
                 transcript=bool(record.get("cues")), youtube=sorted(translations_of(record)),
                 own=bool(own_of(record)), fixed=bool((record.get("fixed") or {}).get("cues")),
                 quality=video.get("quality") if info.get(VIDEO_KEY) else None,
                 video=bool(video_files(data_path.parent, info.get("id"))))
    return lines, found


def show_summary(found: list[dict], without: list[str]) -> str:
    """The last line for several videos: what the data and the files have."""
    parts = []
    translations = Counter(code for f in found for code in f["youtube"])
    texts = [f"translation {code} in {n}" for code, n in sorted(translations.items())]
    for key, word in (("own", "own translations"), ("fixed", "corrections")):
        n = sum(1 for f in found if f[key])
        if n:
            texts.append(f"{word} in {n}")
    no_transcript = sum(1 for f in found if not f["transcript"])
    if no_transcript:
        texts.append(f"no transcript in {no_transcript}")
    parts.append(", ".join(texts) or "no translations")
    videos = [f for f in found if f["video"]]
    if videos:
        qualities = Counter(f["quality"] for f in videos if f["quality"])
        detail = [", ".join(f"{q} {n}" for q, n in sorted(
            qualities.items(), key=lambda item: -int(re.match(r"\d*", item[0]).group() or 0)))]
        undescribed = sum(1 for f in videos if not f["described"])
        if undescribed:
            detail.append(f"{undescribed} not described")
        detail = "; ".join(d for d in detail if d)
        parts.append(f"video in {len(videos)}" + (f" ({detail})" if detail else ""))
    else:
        parts.append("no video files")
    for key, word in (("not_saved", "something not saved in"), ("missing", "files missing in")):
        n = sum(1 for f in found if f[key])
        if n:
            parts.append(f"{word} {n}")
    pages = sum(1 for f in found if not f["page"])
    if pages:
        parts.append(f"no page for {pages}")
    behind = sum(1 for f in found if f["behind"])
    if behind:
        parts.append(f"a page not made from its data for {behind}")
    if without:
        parts.append(f"no saved data for {len(without)} ({', '.join(without)})")
    return f"{plural(len(found), 'video')}: " + "; ".join(parts)


def show_saved(args: argparse.Namespace) -> int:
    """--json show: a block for each saved data file (the sources as for
    --json load, or the videos of --index FILE: its video lines and the
    playlists as the index page keeps them, nothing read from YouTube), then
    a summary for several. 1 when a source given in the command has no
    readable data; a video of the list without data is only counted."""
    output_dir = Path(args.output_dir)
    without = []
    if args.index is not None:
        import yt2html_index   # (here: that module imports this one)
        ids, notes = yt2html_index.saved_list_videos(args)
        for note in notes:
            log(note)
        sources = []
        for video_id in ids:
            if file_with_id(output_dir, video_id, (".info.json",), "saved data files"):
                sources.append(video_id)
            else:
                without.append(video_id)
    else:
        sources = args.sources
    failed, found = 0, []
    for source in sources:
        try:
            data_path = saved_data_path(source, output_dir)
            info = read_saved(data_path)
            lines, facts = show_block(info, data_path)
        except (OSError, ValueError) as error:
            log(f"FAILED: {source}: {getattr(error, 'strerror', None) or error}")
            failed += 1
            continue
        if found:
            print()
        print("\n".join(lines), flush=True)
        found.append(facts)
    if len(found) > 1 or (found and without):
        print()
        print(show_summary(found, without), flush=True)
    elif without:
        print(f"No saved data for {', '.join(without)}.")
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
    with --page avatars:no). The same set is downloaded and counted in "About this copy"."""
    return {c.get("author_thumbnail") for c in info.get("comments") or []} - {None, ""}


def avatar_urls(info: dict, comments: bool = True) -> set[str]:
    """The avatars to download: the channel's, and the comment authors' unless
    left out (--page avatars:no)."""
    urls = comment_avatar_urls(info) if comments else set()
    return {url for url in urls | {channel_avatar_url(info)} if url}


def render_page(info: dict, thumbnail: str | None, avatar_uris: dict[str, str],
                archived: dt.date, show_snapshot: bool = True,
                transcript_spec: list | None = None,
                extra_images: dict[str, str] | None = None,
                theme: str = "auto", no_avatars: bool = False,
                from_json: bool = False, built: dt.datetime | None = None,
                video_files: list[str] | None = None, video_size: int | None = None,
                audio_files: dict | None = None, cover: str | None = None,
                data: str | None = None) -> str:
    """The whole HTML page. Images come as data URIs: `thumbnail`,
    `avatar_uris`, `extra_images` ({image url: data URI}) and `cover` (only
    in the metadata, for the index). `no_avatars`,
    `from_json` and `built` (the build time, default now) are only described
    in "About this copy". `video_files` and `audio_files` ({(code, role):
    file}) are what was found next to the page; `data` the mark of the saved
    data it is made from (data_mark(), in the metadata)."""
    video_id = info.get("id") or ""
    title = info.get("title") or video_id
    duration = info.get("duration")
    url = info.get("webpage_url") or f"{YOUTUBE}/watch?v={video_id}"
    uploader = info.get("uploader") or info.get("channel") or "(unknown channel)"
    channel_url = info.get("channel_url") or info.get("uploader_url")   # by id, else the handle
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
    body.append('<div class="channel">'
                + avatars.html(channel_avatar, info.get("uploader") or info.get("channel") or "")
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
                + (refreshed_tile(info, archived) if show_snapshot else "")
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
        sections = render_transcripts(fixed_view(transcript), chapters, video_id, spec)
        if sections and original_uncertain(info):   # once, under the first heading
            sections[0] = sections[0].replace("</h2>", "</h2>" + uncertain_note(info))
        body.extend(sections)

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
        status = comment_status(info)
        body.append("<h2>Comments</h2>")
        # Nothing to count: only why. A download cut short by the comment limit
        # or Ctrl+C must not claim "No comments.", and keeps its numbers.
        if info.get("comments") is None:
            body.append(f'<div class="note">'
                        f'{esc(info.get(COMMENTS_MESSAGE_KEY) or "Comments are unavailable.")}</div>')
        elif not comments and status == "complete" and not shown:
            body.append('<div class="note">No comments.</div>')
        else:
            gone = gone_comments(comments)
            closed = comments_closed(info)
            if closed:
                body.append(f'<div class="note closed">{closed_note(closed)}</div>')
            body.append(f'<div class="note">{format_int(len(comments))} downloaded{shown}'
                        + (f': {format_int(len(roots))} threads, {format_int(replies)} replies · '
                           + (f'{format_int(gone)} no longer on YouTube · ' if gone else '')
                           + f'oldest first · comment dates are relative to '
                           f'{comments_date(info, archived).strftime(DATE_FORMAT)}'
                           if comments else '') + '</div>')
        for c in roots:
            body.append(render_comment(c, children, avatars, video_id, duration, uploader))
        body.append("</section>")

    body.append(render_video_formats(info, own_shown(info.get(TRANSCRIPT_KEY), spec)))
    body.append(render_about(info, archived, thumbnail, avatar_uris, extra_images or {},
                             spec, theme, show_snapshot, no_avatars, from_json,
                             built or dt.datetime.now(), video_files, video_size))
    body.append("</main>")
    overview = video_overview(info)
    in_video = overview["in_video"] if overview else None
    # spoken names of the audio switches: the language as in the languages table
    # (by code and role: one language may have two tracks)
    names = {(html.unescape(r[0]), html.unescape(r[2] or "")): html.unescape(r[1])
             for r in (overview["languages"]["rows"] if overview else []) if r[0]}
    extra_audio = [(a["audio"], a["file"],
                    f"{names.get((a['audio'], a.get('role') or ''), a['audio'])}, "
                    f"{a.get('role') or 'audio'}",
                    (audio_files or {}).get((a["audio"], a.get("role")), ""),
                    audio_suffix((a["audio"], a.get("role"))))
                   for a in (info.get(VIDEO_KEY) or {}).get("extra_audio") or []]
    original = in_video[0] if in_video and in_video[0] != UNKNOWN_LANGUAGE else None
    body.insert(0, render_player(video_names(info), video_files, player_qualities(info),
                                 original, extra_audio,
                                 f"{names.get((original, in_video[1] or ''), original)}, original"
                                 if original else "Original audio"))
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
          for name, value in page_metadata(info, archived, cover, data).items()),
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
# Playlists and what a run learns about them
# --------------------------------------------------------------------------
#
# The index of a list file (--index FILE, --index-only FILE) is made by the
# module yt2html_index.

class Run:
    """What this run learned: the playlists read (for "About this copy" of
    their videos and for the index)."""

    def __init__(self):
        self.playlists: dict[str, dict] = {}  # playlist id -> its list (read_playlist())


def failure_reason(error: Exception) -> str:
    """A short reason, e.g. "Private video. Sign in ..."."""
    text = str(error).strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", text)  # "[youtube] VIDEO_ID: "
    return (text.splitlines() or ["unknown error"])[0][:300]


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


def source_video_id(source: str) -> str | None:
    """The video id of a source: from its address, or for a saved data file
    (--json write, --json refresh) from the id in its name."""
    if source.lower().endswith(".json"):
        return id_in_name(source, playlists=False)
    return video_id_from_url(source)


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


# A later page of a list that YouTube sends without its videos ("Incomplete
# data received", after yt-dlp's retries) is only a warning by default: the
# list then ends there, as if it were YouTube's whole list. With this, it is
# an error, so a playlist is "could not be read" (the index uses the list it
# kept) instead of a shorter one.
WHOLE_LIST = {"raise_incomplete_data": [""]}


def fetch_playlist(url: str, args: argparse.Namespace) -> dict:
    """A playlist's data (title, channel, description, playlist_count, ...)
    with its flat entries: only the list of its videos, in playlist order,
    none of them extracted (one request per ~100 videos)."""
    opts = {"skip_download": True, "extract_flat": "in_playlist", "noprogress": True,
            "extractor_args": {"youtube": WHOLE_LIST}}
    if args.cookies:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies)
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
        return {**info, "entries": list(info.get("entries") or [])}


def read_playlist(source: str, args: argparse.Namespace, run: Run) -> dict:
    """Read a playlist's list (no video) and keep it in `run`: its id,
    title, address, channel (with the link the video pages use), count as
    YouTube gives it (unavailable videos included), description and the
    videos ({"id", "url"}, in playlist order, also twice if listed twice)."""
    log(f"Fetching playlist {source}")
    info = fetch_playlist(source, args)
    playlist_id = playlist_id_from_url(source) or source
    ids = [video_id for video_id in map(entry_video_id, info["entries"]) if video_id]
    section = {"playlist_id": playlist_id, "title": info.get("title") or playlist_id,
               "url": f"{YOUTUBE}/playlist?list={playlist_id}",
               "channel": info.get("channel") or info.get("uploader"),
               "channel_url": info.get("channel_url") or info.get("uploader_url"),  # by id first
               "count": info.get("playlist_count"), "description": info.get("description"),
               "entries": [{"id": video_id, "url": f"{YOUTUBE}/watch?v={video_id}"}
                           for video_id in ids]}
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
    return first_of(files_with_id(folder, ident, extensions), folder, ident, what)


def first_of(found: list[Path], folder: Path, ident: str | None, what: str) -> Path | None:
    """The first of `found` (files with "[ident]" in `folder`); several of
    them get a note, once for each folder, id and kind of file (`what`)."""
    key = (str(folder), ident, what)
    if len(found) > 1 and key not in _SEVERAL_NOTED:
        _SEVERAL_NOTED.add(key)
        log(f"Note: several {what} for [{ident}] in {folder}: "
            + ", ".join(p.name for p in found) + f"; using {found[0].name}")
    return found[0] if found else None


def existing_page(output_dir: Path, video_id: str | None) -> Path | None:
    """The page of this video in the output folder, not its subfolders: any
    ".html" file with "[id]" in its name, so pages may be renamed freely as
    long as that part stays."""
    return file_with_id(output_dir, video_id, (".html",), "pages")


def write_atomically(path: Path, content: str | bytes) -> None:
    """Write to a temporary file and rename it, so an interrupted run never
    leaves a cut-off file (a page or subtitles a later run would take as
    finished, saved data that would be lost)."""
    temporary = path.with_name(path.name + ".part")
    # bytes as they are, so a page has the same line ends on every system
    temporary.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    os.replace(temporary, path)


class CommentsInterrupted(KeyboardInterrupt):
    """Ctrl+C while yt-dlp downloaded the comments: yt-dlp stops only the
    comments, the page is made from those fetched so far, then the run stops."""


def comments_interrupted(info: dict) -> bool:
    """yt-dlp keeps the comments fetched before Ctrl+C but sets
    comment_count to None (it is also None when comments are disabled, but
    then there is no comment list)."""
    return isinstance(info.get("comments"), list) and info.get("comment_count") is None


def narrow_saved_comments(info: dict, args: argparse.Namespace, note: bool = True) -> None:
    """--page comments:no and comments:N with saved data: they can only narrow
    the saved comments, as if the download had used them. The first N are
    kept, in the saved (yt-dlp's) order, replies counted: the comments a
    download with that limit gets. Only the page shows the result (`info`
    is a copy for it); `note` tells when more cannot come from the data."""
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
    elif note and comment_status(info) in ("limited", "interrupted"):
        log(f"  The saved data has only {plural(len(comments), 'comment')}; "
            f"--json refresh --page comments:all adds the rest.")


def update_data_path(source: str, output_dir: Path) -> Path | None:
    """The saved data a --json write source adds to: a saved data file
    given as it is, else the ".info.json" file with the video's id in square
    brackets in the output folder; None when there is none. Raises ValueError
    for a file name that leads nowhere."""
    path = Path(source)
    if path.is_file():   # (other names with an id came as URLs: update_source())
        return path
    if "://" not in source and has_wildcards(source):   # (a URL has its "?")
        raise ValueError(f"no file matches {source}")
    video_id = video_id_from_url(source)
    if not video_id:
        if source.lower().endswith(".json"):
            raise ValueError(f"no such file: {source}")
        return None
    return file_with_id(output_dir, video_id, (".info.json",), "saved data files")


def missing_for_update(info: dict, args: argparse.Namespace) -> tuple[bool, list[str]]:
    """What --transcript asks for and the saved data lack: (the transcript
    itself, the languages of the translations to fetch). What NOT_SAVED_KEY
    notes with `again` (YouTube refused it) is asked for again; what YouTube
    does not have is not."""
    if not args.transcript:
        return False, []
    saved = info.get(TRANSCRIPT_KEY) or {}
    own = set(own_of(saved))
    if getattr(args, "transcript_ovr", False):   # downloaded again, whatever is saved
        # (own translations kept if they fit: keep_own_translations(); the
        # original's language is left out once the transcript is known)
        return True, source_keys(args.transcript, info, None, own)
    if TRANSCRIPT_KEY not in info:
        return True, source_keys(args.transcript, info, None)
    wanted = source_keys(args.transcript, info, saved.get("language"), own)
    transcript = info[TRANSCRIPT_KEY]
    if transcript is None:
        reason = ((info.get(NOT_SAVED_KEY) or {}).get(gap_key()) or {}).get("reason")
        log(f"No transcript in the saved data ({reason or 'none'}); not asked for again.")
        return False, []
    missing = transcript_gaps(info)

    def wanted_now(key):   # not in the data, and not noted as not on YouTube
        entry = missing.get(key)
        if available(transcript, key):
            return False
        if entry and not entry.get("again"):
            what = f"translation into '{key}'" if "." not in key else f"subtitles {key}"
            log(f"No {what} in the saved data ({entry['reason']}); not asked for again.")
            return False
        return True

    targets = []
    for key in wanted:
        first, _, fallback = key.partition("|")
        if wanted_now(first):
            # the translation again; its fallback only if the data lack it too
            targets.append(key if fallback and not available(transcript, fallback) else first)
        elif fallback and not available(transcript, first) and wanted_now(fallback):
            targets.append(fallback)
    return False, targets


def update_transcript(info: dict, args: argparse.Namespace, whole: bool,
                      targets: list[str], fresh: dict | None = None,
                      started: float | None = None) -> list[str]:
    """--json write: the transcript (`whole`) or only its translations into
    `targets`, from fresh player data (no comments, no watch page: caption
    addresses expire, and the saved data know the track and the languages);
    --json refresh passes the `fresh` data it fetched at `started`.
    Returns what was added, for UPDATES_KEY."""
    opts = {"skip_download": True, "noplaylist": True, "ignore_no_formats_error": True,
            "noprogress": True, "quiet": True, "format": NO_MERGE_FORMAT}
    if args.cookies:
        opts["cookiesfrombrowser"] = cookies_spec(args.cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        if fresh is None:
            log("Fetching the video's data (no comments) for what is missing...")
            started = time.monotonic()
            fresh = ydl.sanitize_info(ydl.extract_info(f"{YOUTUBE}/watch?v={info.get('id')}",
                                                       download=False))
        # the track and the languages as the saved data know them
        data = with_page_choices(fresh, info)
        not_before = started + translation_wait()
        if whole:
            saved = info.get(TRANSCRIPT_KEY)
            own = own_of(saved or {})
            fixed = (saved or {}).get("fixed")
            # the transcript's notes go with it; those of this download come
            gaps = without_transcript_gaps(info.get(NOT_SAVED_KEY) or {})
            transcript = fetch_transcript(ydl, data, gaps, targets, not_before)
            if transcript is None and saved:
                olang = data.get(OLANG_KEY)
                if not olang or base_language(olang) == base_language(saved.get("language")):
                    # captions the saved transcript came from, missing now: not a new version
                    log(f"  The saved transcript ({saved.get('name') or saved.get('language')}) "
                        f"stays, with all it holds: YouTube's data have no captions for it "
                        f"now. Try --transcript ovr again later.")
                    return []
                # --olang chose another language, and it has no captions: as asked, but named
                lost = "; ".join(transcript_parts(saved))
                log(f"  The saved transcript is dropped (lost: {lost}).")
            info[TRANSCRIPT_KEY] = transcript
            if transcript is None:
                set_gaps(info, gaps)
                return []
            keep_own_translations(transcript, own)
            keep_fixed(transcript, fixed)
            kept = keep_youtube_translations(transcript, saved or {}, split_keys(targets)[0], gaps)
            set_gaps(info, gaps)
            return (["transcript"] + [f"translation {language}"
                                      for language in translations_of(transcript)
                                      if language not in kept]
                    + [f"subtitles {key}" for key in transcript.get("tracks") or {}])
        transcript = info[TRANSCRIPT_KEY]
        gaps = dict(info.get(NOT_SAVED_KEY) or {})
        before = set(translations_of(transcript)) | set(transcript.get("tracks") or {})
        translations = split_keys(targets)[0]
        track = choose_caption_track(data)
        url = json3_url(track[2]) if track else None
        if translations and (not url or (track[0], track[1])
                             != (transcript.get("kind"), transcript.get("language"))):
            reason = (f"the transcript's captions ({transcript.get('name')}) are not in "
                      "YouTube's data now")
            log(f"No translation into {', '.join(translations)}: {reason}.")
            for target in translations:
                note_gap(gaps, gap_key(target), reason, again=True)
            url = None
        add_sources(ydl, data, transcript, url, targets, gaps, not_before)
        set_gaps(info, gaps)
        return ([f"translation {language}" for language in translations_of(transcript)
                 if language not in before]
                + [f"subtitles {key}" for key in transcript.get("tracks") or {}
                   if key not in before])


def keep_youtube_translations(transcript: dict, saved: dict, asked: list[str],
                              gaps: dict) -> set[str]:
    """--transcript ovr: a translation of YouTube's that the command asks for
    and that did not come now (refused, or no longer offered) stays as saved
    when it still fits the transcript downloaded again (the same lines at the
    same times), instead of its note in `gaps`; one that no longer fits
    is dropped with a message. Those not asked for go, as ovr asks. Returns
    the languages kept."""
    kept = set()
    earlier = translations_of(saved)
    for language in asked:
        if language in translations_of(transcript) or language not in earlier:
            continue
        reason = (gaps.get(gap_key(language)) or {}).get("reason")
        try:
            check_own_cues(earlier[language].get("cues") or [], transcript)
        except ValueError as problem:
            log(f"  YouTube's translation into '{language}' did not come now, and the saved "
                f"one is dropped: it no longer fits the transcript ({problem}).")
            continue
        transcript.setdefault("translations", {})[language] = earlier[language]
        gaps.pop(gap_key(language), None)
        kept.add(language)
        log(f"  YouTube's translation into '{language}' did not come now"
            + (f" ({reason})" if reason else "") + "; the saved one stays.")
    return kept


def transcript_parts(transcript: dict) -> list[str]:
    """What a saved transcript holds, for a message when it is dropped: the
    transcript, YouTube's translations, one's own (with their files, to merge
    again), corrections, other subtitles."""
    parts = [f"the transcript in '{transcript.get('language')}'"]
    youtube = sorted(translations_of(transcript))
    if youtube:
        parts.append(f"YouTube's translation{'s' if len(youtube) > 1 else ''} into "
                     f"{', '.join(youtube)}")
    for language, entry in sorted(own_of(transcript).items()):
        file = (entry.get("own") or {}).get("file")
        parts.append(f"own translation {language}" + (f" ({file})" if file else ""))
    fixed = transcript.get("fixed") or {}
    if fixed.get("cues"):
        parts.append("own corrections" + (f" ({fixed['file']})" if fixed.get("file") else ""))
    tracks = sorted(transcript.get("tracks") or {})
    if tracks:
        parts.append(f"subtitles {', '.join(tracks)}")
    return parts


def keep_own_translations(transcript: dict, own: dict) -> None:
    """--transcript ovr: the translations of one's own that still fit the
    transcript downloaded again (the same lines at the same times) are kept;
    the others are dropped, with a message (their SRT file can be fixed and
    merged again)."""
    for language, entry in own.items():
        if base_language(language) == base_language(transcript.get("language")):
            log(f"  Own translation into '{language}' dropped: the transcript is now in that "
                f"language.")
            continue
        try:
            check_own_cues(entry.get("cues") or [], transcript)
        except ValueError as problem:
            log(f"  Own translation into '{language}' dropped: it no longer fits the "
                f"transcript ({problem}); fix {entry.get('own', {}).get('file') or 'its file'} "
                f"and merge it again.")
            continue
        set_own(transcript, language, entry)


def note_update(info: dict, record: dict, added: list[str] = (), refreshed: bool = False,
                comments: dict | None = None, unavailable: bool = False) -> None:
    """Record what this run of --json write or --json refresh did in its own
    entry of UPDATES_KEY (`record`, empty until something happens): what was
    added, whether the data were refreshed, what changed in the comments,
    whether the video was found no longer on YouTube."""
    if not added and not refreshed and not unavailable:
        return
    if not record:
        now = dt.datetime.now()
        record.update(date=now.date().isoformat(), time=now.strftime("%H:%M"),
                      yt2html=__version__, added=[])
        info.setdefault(UPDATES_KEY, []).append(record)
    record["added"] += [item for item in added if item not in record["added"]]
    if refreshed:
        record["refreshed"] = True
    if unavailable:
        record["unavailable"] = True
    if comments is not None:
        record["comments"] = comments


def merge_comments(saved: list[dict], fresh: list[dict], complete: bool,
                   today: str) -> tuple[list[dict], dict]:
    """--json refresh: the saved comments with the fresh ones, by id. A fresh
    comment replaces its saved self (the old text kept in EDITS_KEY when it
    changed; one missing before comes back, noted in MISSING_HISTORY_KEY),
    a new one is added with the day it came (FIRST_SEEN_KEY), and a saved
    one YouTube did not send stays, marked with the day (MISSING_SINCE_KEY),
    but only when the fresh download is complete (with a limit or after
    Ctrl+C a missing comment proves nothing).
    The saved ones keep their place after their neighbours, in yt-dlp's
    order, which breaks ties of the coarse times. Returns the comments and
    {new, gone, back}."""
    fresh_ids = {c.get("id") for c in fresh}
    saved_by_id = {c.get("id"): c for c in saved}
    stats = {"new": 0, "gone": 0, "back": 0}
    after = defaultdict(list)       # fresh id (None: the start) -> saved ones gone after it
    anchor = None
    for old in saved:
        if old.get("id") in fresh_ids:
            anchor = old.get("id")
            continue
        if complete and not old.get(MISSING_SINCE_KEY):
            old = {**old, MISSING_SINCE_KEY: today}
            stats["gone"] += 1
        after[anchor].append(old)
    merged = list(after[None])
    for new in fresh:
        old = saved_by_id.get(new.get("id"))
        comment = dict(new)
        if old is None:
            stats["new"] += 1
        else:
            history = list(old.get(MISSING_HISTORY_KEY) or [])
            if old.get(MISSING_SINCE_KEY):
                history.append({"from": old[MISSING_SINCE_KEY], "until": today})
                stats["back"] += 1
            edits = list(old.get(EDITS_KEY) or [])
            if (old.get("text") or "") != (new.get("text") or ""):
                edits.append({"text": old.get("text"), "until": today})
            if history:
                comment[MISSING_HISTORY_KEY] = history
            if edits:
                comment[EDITS_KEY] = edits
            if old.get(FIRST_SEEN_KEY):
                comment[FIRST_SEEN_KEY] = old[FIRST_SEEN_KEY]
        if old is None:
            comment[FIRST_SEEN_KEY] = today
        merged.append(comment)
        merged += after[new.get("id")]
    return merged, stats


def refresh_saved(info: dict, fresh: dict) -> dict | None:
    """--json refresh: the counts from fresh data; the title and description
    (the earlier ones kept in PREVIOUS_KEY) and the timestamp lists made from
    them; and the comments merged (merge_comments()). Comments not downloaded
    now (--page comments:no) leave the saved ones; comments turned off on YouTube
    since leave them too, noted in COMMENTS_CLOSED_KEY. Returns what changed
    in the comments, or None when they were not refreshed."""
    today = dt.date.today().isoformat()
    for key in REFRESHED_KEYS:
        if fresh.get(key) is not None:
            info[key] = fresh[key]
    for key in ("title", "description"):
        if fresh.get(key) is not None and fresh[key] != info.get(key):
            info.setdefault(PREVIOUS_KEY, []).append({"until": today, key: info.get(key)})
            info[key] = fresh[key]
    if fresh.get(MARKERS_KEY) is not None:   # None: the watch page not read now
        info[MARKERS_KEY] = fresh[MARKERS_KEY]
    closed = info.get(COMMENTS_CLOSED_KEY) or []
    off_now = bool(closed) and "until" not in closed[-1]
    if "comments" in fresh and fresh["comments"] is None:   # turned off, or not shown
        if info.get("comments") and not off_now:
            info[COMMENTS_CLOSED_KEY] = closed + [
                {"from": today, "turned_off": bool(fresh.get(COMMENTS_MESSAGE_KEY))}]
        return None
    if fresh.get("comments") is None:
        return None
    if off_now:
        closed[-1]["until"] = today
    saved = info.get("comments") if isinstance(info.get("comments"), list) else []
    merged, stats = merge_comments(saved, fresh["comments"], comment_status(fresh) == "complete",
                                   dt.date.today().isoformat())
    info["comments"] = merged
    # None (Ctrl+C) as yt-dlp leaves it; else the number kept, as for a download
    info["comment_count"] = None if fresh.get("comment_count") is None else len(merged)
    info[LIMIT_KEY] = fresh.get(LIMIT_KEY)
    return stats


def merge_video_record(previous: dict, result: dict, image_new: bool) -> dict:
    """VIDEO_KEY after a run of --video: what the run found or downloaded
    (`result`) over the saved record (`previous`), file by file (review A3:
    the record was replaced by the run's, so audio:de after audio:pl lost pl,
    and --json refresh --video every track). The video file: the run's, else
    the saved one; the extra tracks by (audio, role), the run's over the
    saved, the others kept in their order, also for a file no longer there
    (the user, 25.11.2026: its data stay; the page greys out its switch until
    the file is back); the old sub: subtitles kept. "parameters": those of the image from
    the run that downloaded it (`image_new`), else the saved ones, with the
    audio: items of both."""
    if not previous:
        return result
    merged = {**previous, **{key: value for key, value in result.items()
                             if key != "extra_audio"}}
    tracks = {(e.get("audio"), e.get("role")): e for e in previous.get("extra_audio") or []}
    tracks.update(((e.get("audio"), e.get("role")), e) for e in result.get("extra_audio") or [])
    if tracks:
        merged["extra_audio"] = list(tracks.values())

    def items(record: dict) -> list[str]:
        return [item.strip() for item in (record.get("parameters") or "").split(",")
                if item.strip()]

    def audio(item: str) -> bool:   # (the parameters are kept in lower case: video_spec())
        return item.startswith("audio:")

    image = [item for item in items(result if image_new else previous) if not audio(item)]
    seen, extra = set(), []
    for item in filter(audio, items(previous) + items(result)):
        if item not in seen:
            seen.add(item)
            extra.append(item)
    merged["parameters"] = ",".join(image + extra)
    return merged


def video_added(result: dict, previous: dict) -> list[str]:
    """The files this run of --video downloaded, for UPDATES_KEY: dated today
    and not so in the saved data (`previous`, their VIDEO_KEY) already."""
    def dated(entries: list) -> set:
        return {(e.get("file"), e.get("date")) for e in entries if e}

    known = dated([previous.get("video")] + (previous.get("extra_audio") or [])
                  + (previous.get("subtitles") or []))
    today = dt.date.today().isoformat()

    def new(entry: dict | None) -> bool:
        return bool(entry) and entry.get("date") == today and (entry.get("file"), today) not in known

    added = ["video"] if new(result.get("video")) else []
    added += [f"extra audio {e['audio']}" + (f" ({e['role']})" if e.get("role") else "")
              for e in result.get("extra_audio") or [] if new(e)]
    added += [f"subtitles {e['code']} ({e['kind']})"
              for e in result.get("subtitles") or [] if new(e)]
    return added


# --------------------------------------------------------------------------
# --json merge: a translation of one's own (an SRT file) into the saved data
# --------------------------------------------------------------------------

# "Title [ID].pl.own.srt", a translation of one's own, and "Title [ID].en.fixed.srt",
# the transcript with one's own corrections (the user, 25.11.2026): the words set
# them apart from the kinds of YouTube's subtitles in file names (author,
# auto-generated, translated)
OWN_SRT = re.compile(r"\.([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)\.(own|fixed)\.srt$", re.IGNORECASE)
SRT_TIMING = re.compile(r"^\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->")


def srt_cues(path: Path) -> list[dict]:
    """The lines of an SRT file as transcript cues: [{start (s), text}], the
    text of a line on one line. Raises ValueError for what is not SRT or a
    line without text."""
    raw = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.split("\n")
        at = next((i for i, line in enumerate(lines[:2]) if SRT_TIMING.match(line)), None)
        if at is None:
            raise ValueError(f"{path.name} is not an SRT file (block {len(cues) + 1}: "
                             f"{lines[0][:40]!r})")
        h, m, sec, ms = map(int, SRT_TIMING.match(lines[at]).groups())
        text = " ".join(" ".join(lines[at + 1:]).split())
        if not text:
            raise ValueError(f"line {len(cues) + 1} of {path.name} has no text")
        cues.append({"start": round(h * 3600 + m * 60 + sec + ms / 1000, 3), "text": text})
    return cues


def check_own_cues(cues: list[dict], transcript: dict) -> None:
    """The translation must have the transcript's lines: as many, each from
    the same moment (so the file was made from the same captions); raises
    ValueError otherwise."""
    saved = transcript.get("cues") or []
    track = transcript_note(transcript)
    if len(cues) != len(saved):
        raise ValueError(f"{plural(len(cues), 'line')} in the file, {len(saved)} in the "
                         f"transcript ({track}): not made from the transcript's captions")
    for number, (cue, line) in enumerate(zip(cues, saved), 1):
        if round(cue["start"] * 1000) != round(float(line.get("start") or 0) * 1000):
            raise ValueError(f"line {number} starts at {srt_time(round(cue['start'] * 1000))}, "
                             f"the transcript's at {srt_time(round(float(line.get('start') or 0) * 1000))} "
                             f"({track}): not made from the transcript's captions")


def merge_fixed(path: Path, info: dict, data_path: Path, transcript: dict, language: str,
                same: bool) -> Path:
    """--json merge of "Title [ID].en.fixed.srt": the transcript's text with
    one's own corrections (the user, 25.11.2026: obvious mistakes of
    automatic captions), at its lines and times, so every translation still
    fits; kept beside YouTube's text ("fixed"), which stays in the data. The
    page shows it, with a note."""
    if not same:
        raise ValueError(f"'{language}' is not the language of the transcript "
                         f"({transcript_note(transcript)}): corrections are in its language, "
                         f"'.{transcript.get('language')}.fixed.srt'; a translation of your own "
                         f"is '.{language}.own.srt'")
    cues = srt_cues(path)
    check_own_cues(cues, transcript)
    earlier = transcript.get("fixed") or {}
    if earlier.get("date"):
        log(f"  Replaces the corrections added on {format_date(earlier['date'])}.")
    transcript["fixed"] = {"cues": cues, "file": path.name, "date": dt.date.today().isoformat()}
    note_update(info, {}, ["own corrections of the transcript"])
    write_atomically(data_path, json.dumps(info, ensure_ascii=False, indent=1))
    log(f"Updated metadata: {data_path} (own corrections of the transcript, "
        f"{plural(len(cues), 'line')})")
    folder = "" if data_path.parent == Path(DEFAULT_OUTPUT_DIR) else f' -o "{data_path.parent}"'
    log(f"  The page is unchanged; to show it: yt2html \"{data_path.name}\"{folder} --json load "
        f"--transcript")
    return data_path


def keep_fixed(transcript: dict, fixed: dict | None) -> None:
    """--transcript ovr: one's own corrections stay when they still fit the
    transcript downloaded again (its lines at its times), else they are
    dropped with a message."""
    if not (fixed or {}).get("cues"):
        return
    try:
        check_own_cues(fixed["cues"], transcript)
    except ValueError as problem:
        log(f"  Own corrections of the transcript dropped: they no longer fit it ({problem}); "
            f"fix {fixed.get('file') or 'their file'} and merge it again.")
        return
    transcript["fixed"] = fixed


def merge_own_translation(source: str, args: argparse.Namespace,
                          outcome: dict | None = None) -> Path | None:
    """--json merge: a translation of one's own, "Title [ID].CODE.own.srt",
    into the saved data next to it (the ".info.json" file with the same id),
    as one's own translation into CODE ("own", beside YouTube's in that
    language, which stays: the user, 25.11.2026); no page is made (--json
    load then shows it). Nothing is asked of YouTube. It replaces an earlier
    one of one's own. Raises ValueError, with nothing saved, when the file
    does not fit the data."""
    path = Path(source)
    if not path.is_file():
        raise ValueError(f"no file matches {source}" if has_wildcards(source)
                         else f"no such file: {source}")
    named = OWN_SRT.search(path.name)
    if not named:
        raise ValueError(f"{path.name}: the name ends with '.CODE.own.srt' (a translation of "
                         "your own, e.g. 'Title [ID].pl.own.srt') or '.CODE.fixed.srt' (the "
                         "transcript with your corrections, e.g. 'Title [ID].en.fixed.srt')")
    fix = named.group(2).lower() == "fixed"
    language = standard_code(named.group(1))
    if not recognised_code(language, set()):
        raise ValueError(f"{path.name}: '{named.group(1)}' is not a language code "
                         "(e.g. pl, en, pt-BR)")
    video_id = id_in_name(path.name, playlists=False)
    if not video_id:
        raise ValueError(f"{path.name}: no [VIDEO_ID] in the name")
    data_path = file_with_id(path.parent, video_id, (".info.json",), "saved data files")
    if not data_path:
        raise ValueError(f"no saved data for {video_id} next to the file "
                         f"(looked for *[{video_id}]*.info.json in {path.parent})")
    log(f"Merging {path.name} into {data_path}")
    info = read_saved(data_path)
    transcript = info.get(TRANSCRIPT_KEY)
    if not transcript:
        raise ValueError("the saved data have no transcript to translate (download it "
                         "with --json write --transcript first)")
    same = base_language(language) == base_language(transcript.get("language"))
    if fix:
        return merge_fixed(path, info, data_path, transcript, language, same)
    if same:
        raise ValueError(f"'{language}' is the language of the transcript itself "
                         f"({transcript_note(transcript)}); corrections to it go in "
                         f"'{OWN_SRT.sub('', path.name)}.{transcript.get('language')}.fixed.srt'")
    earlier = own_of(transcript).get(language)
    cues = srt_cues(path)
    check_own_cues(cues, transcript)
    if earlier:
        log(f"  Replaces the own translation into '{language}' added on "
            f"{format_date(earlier['own']['date'])}.")
    set_own(transcript, language, {
        "language": language,
        "name": (info.get(TRANSLATIONS_KEY) or {}).get(language) or language,
        "cues": cues, "own": {"file": path.name, "date": dt.date.today().isoformat()}})
    note_update(info, {}, [f"own translation {language}"])
    write_atomically(data_path, json.dumps(info, ensure_ascii=False, indent=1))
    log(f"Updated metadata: {data_path} (own translation into '{language}', "
        f"{plural(len(cues), 'line')})")
    folder = "" if args.output_dir == DEFAULT_OUTPUT_DIR else f' -o "{args.output_dir}"'
    log(f"  The page is unchanged; to show the translation: yt2html \"{data_path.name}\"{folder} "
        f"--json load --transcript plain,dual:{language}")
    return data_path


PLAYLIST_ERROR = "this URL leads to a playlist or channel, not a single video"
CHANNEL_ERROR = ("this is a channel URL; only videos and playlists are supported "
                 "(open the channel's playlist and use its URL)")


def archive(source: str, args: argparse.Namespace, playlist: dict | None = None,
            outcome: dict | None = None) -> Path | None:
    """Make the page for one URL (or one .info.json file).

    Returns the page's path, or None if the video already has a page in the
    output folder and --page ovr was not given (with --json load the page is
    always made again; with --json write and --json refresh too when there
    are saved data, which get what the options need and they lack, and are
    saved back). Everything that can fail for a reason the user should know
    (playlist URL, missing translation) is checked before anything is
    written. `outcome` (for --video) gets the page's path ("target"), and for
    a page made now also its data ("info") and "rebuild": a function that
    saves the data (with --json write) and the page again from it, with the
    images already downloaded.
    """
    if getattr(args, "merge_json", False):
        return merge_own_translation(source, args, outcome)
    outcome = {} if outcome is None else outcome
    output_dir = Path(args.output_dir)
    adding = args.update_json or args.refresh_json     # to saved data, if there are any
    skip_existing = not (args.overwrite or args.load_json or adding)

    if skip_existing:
        # Checked before fetching, from the id in the URL: no request needed.
        found = existing_page(output_dir, video_id_from_url(source))
        if found:
            log(f"Skipped (page exists): {found}"
                + ("; SRT files are made with the page (--page ovr, or --json write with "
                   "saved data)" if getattr(args, "transcript_srt", False) else ""))
            outcome["target"] = found
            return None

    if not args.load_json and is_channel_url(source):
        raise ValueError(CHANNEL_ERROR)
    write_json = args.write_json
    data_path = (saved_data_path(source, output_dir) if args.load_json else
                 update_data_path(source, output_dir) if adding else None)
    updating = adding and data_path is not None
    read_mark = None   # the mark of the saved data read (data_mark())
    if data_path:
        log(f"Reading {data_path}")
        info = read_saved(data_path)
        read_mark = data_mark(data_path.read_bytes())
    else:
        if adding:
            found = existing_page(output_dir, video_id_from_url(source))
            if found:
                log(f"Skipped (page exists, no saved data to add to): {found}. Use "
                    "--page ovr --json write to download everything again with the data.")
                outcome["target"] = found
                return None
            log(f"No saved data for {source}: downloading everything.")
            write_json = True
        taking(args, video_id_from_url(source) or source)
        log(f"Fetching {source}")
        info = fetch_info(source, args)
        info[PLAYLIST_KEY] = playlist

    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError(PLAYLIST_ERROR)
    record = outcome.setdefault("record", {})   # this run's entry in UPDATES_KEY
    if updating and args.olang and info.get(OLANG_KEY) != args.olang:
        # --olang with saved data: recorded; a transcript in another language stays
        # unless --transcript ovr downloads it again (then in the language set)
        info[OLANG_KEY] = args.olang
        note_update(info, record, [f"original language {args.olang}"])
        saved = (info.get(TRANSCRIPT_KEY) or {}).get("language")
        if saved and base_language(saved) != base_language(args.olang) \
                and not args.transcript_ovr:
            log(f"  Warning: the saved transcript is in '{saved}', not in '{args.olang}' "
                f"(--olang); --transcript ovr downloads it again in '{args.olang}'.")
    if updating:
        fresh = started = None
        gone = False
        if args.refresh_json:
            taking(args, info.get("id"))
            log(f"Fetching {source} (to refresh the saved data)")
            started = time.monotonic()
            try:
                # the transcript as the data have it; what it lacks comes below
                fresh = fetch_info(f"{YOUTUBE}/watch?v={info.get('id')}",
                                   argparse.Namespace(**{**vars(args), "transcript": []}))
            except VideoUnavailable as refusal:
                if not refusal.gone:   # may not last: nothing is marked, nothing saved
                    raise
                # recorded; the page is made again from the data, nothing else changes
                gone = True
                info[UNAVAILABLE_KEY] = {"status": refusal.status, "reason": refusal.reason}
                note_update(info, record, unavailable=True)
                log(f"No longer on YouTube: {refusal.reason} (recorded)")
            else:
                info.pop(UNAVAILABLE_KEY, None)
                note_update(info, record, refreshed=True, comments=refresh_saved(info, fresh))
                if (args.page is None or args.page["comments"] is None) \
                        and comment_status(fresh) == "limited":
                    log(f"  Comments refreshed up to the default limit of "
                        f"{DEFAULT_COMMENTS:,}; --page comments:all refreshes all "
                        f"of them.")
        whole, targets = ((False, []) if gone else missing_for_update(info, args))
        if whole or targets:
            taking(args, info.get("id"))
            note_update(info, record, update_transcript(info, args, whole, targets, fresh,
                                                        started))
    for note in resolve_versions(args.transcript, info.get(TRANSCRIPT_KEY))[1]:
        log(f"  Transcript: {note}.")
    # --json write with nothing missing in the data: a page that is there stays
    # as it is (with --video, unless files are added: then it is made again)
    idle = updating and not args.refresh_json and not (whole or targets) and not record

    saved = info.get(TRANSCRIPT_KEY) or {}
    missing = transcript_gaps(info)
    for version, source in (resolve_versions(args.transcript, saved)[0]
                            if args.load_json and saved else []):
        code, _ = split_code(source)
        if not available(saved, source) and source not in missing and code not in missing:
            raise TranscriptError(f"no {version_text(version, source)} in the data (no "
                                  f"transcript translation or subtitles of it); nothing was "
                                  f"saved.")

    # An existing page (possibly renamed by the user) keeps its name when it
    # is made again; otherwise the name follows yt-dlp's video file name.
    found = existing_page(output_dir, info.get("id"))
    if found and (skip_existing or (adding and not updating)):
        # The URL did not show the id (unusual form), the fetched data does.
        log(f"Skipped (page exists): {found}")
        outcome["target"] = found
        return None
    if idle and found and getattr(args, "transcript_srt", False) and any(
            not file_by_suffix(output_dir, info.get("id"), sub_suffix(code, kind), "subtitle files")
            for code, kind, _ in srt_tracks(info, args.transcript)):
        idle = False   # an SRT file is missing: the page and its files are made again
    deferred = bool(idle and found)
    if deferred and args.video is None:
        log(f"Nothing missing in the saved data; the page stays as it is: {found} "
            "(--json load makes it again from the data)")
        if not made_from(found, read_mark):
            log(PAGE_BEHIND_NOTE)
        outcome["target"] = found
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    target = found or output_path(info, output_dir)
    if args.video is not None:
        # what the saved data read now say about the files next to the page:
        # --video keeps what still holds (without --json, or with --page ovr,
        # saved data are not read: the files are only found next to the page)
        outcome["previous"] = (info.get(VIDEO_KEY) or {}) if data_path else {}
    # --page ovr --json write: the fresh data replace the saved ones, in their file
    replaced = (file_with_id(output_dir, info.get("id"), (".info.json",), "saved data files")
                if write_json and args.overwrite else None)

    if replaced:
        log(replaced_data_note(replaced, info))
    if not deferred and found and not args.load_json and not connected():
        raise NoConnection("the connection was lost before the page was made again")

    def shown(note: bool = False) -> dict:
        """What the page shows: from saved data, the comments narrowed by
        --page comments:no or comments:N and the cards left out with
        cards:no (the data keep all of them)."""
        if not data_path:
            return info
        view = dict(info)
        narrow_saved_comments(view, args, note)
        if args.no_cards:
            view.pop(CARDS_KEY, None)
        return view

    images = {}

    def fetch_page_images() -> None:
        """The images the page embeds, once (not at all for a page left as it
        is). What the existing page has comes from it, without asking (review
        A5; the user, 25.11.2026: no needless requests, no placeholders for
        images once downloaded): the thumbnail and the cover unless --page
        ovr or --json refresh asks for them anew (their address stays when
        the channel changes the picture), the other images by their address
        unless --page ovr (fetch_images()). What does not come is taken from
        it too."""
        if images:
            return
        kept = images_of_page(found)
        anew = args.overwrite or args.refresh_json
        thumbnail = None if anew else kept.get("thumbnail")
        cover = None if anew else kept.get("cover")
        if thumbnail and cover:
            log("Thumbnail and cover from the existing page.")
        else:
            log("Downloading thumbnail and cover...")
            thumbnail = thumbnail or fetch_thumbnail(info)
            cover = cover or fetch_image(cover_url(info))
            if not thumbnail:
                thumbnail = kept.get("thumbnail")
                log("  Thumbnail could not be downloaded" +
                    ("; kept from the existing page." if thumbnail else "."))
            if not cover:
                cover = kept.get("cover")
                log("  Cover could not be downloaded" +
                    ("; kept from the existing page." if cover else "."))
        keyed, reuse = kept.get("keyed"), not args.overwrite
        images.update(
            thumbnail=thumbnail, cover=cover,
            avatars=fetch_images(avatar_urls(shown(note=True), comments=not args.no_avatars),
                                 "avatars", resize=resize_avatar_url, kept=keyed, reuse=reuse),
            extra=fetch_images(extra_image_urls(shown()), "images (people, podcast, cards)",
                               kept=keyed, reuse=reuse))

    try:
        archived = dt.date.fromisoformat(info.get(ARCHIVED_KEY) or "")
    except ValueError:
        archived = dt.date.today()  # older JSON files without the date

    def page_html(mark: str | None) -> str:
        """The page, with its images (downloaded here) and the mark of the
        saved data it is made from."""
        fetch_page_images()
        video_files = video_files_for(info, output_dir)
        video_size = (output_dir / video_files[0]).stat().st_size if video_files else None
        audio_files = audio_files_for(info, output_dir)
        page = render_page(shown(), images["thumbnail"], images["avatars"], archived,
                           cover=images["cover"],
                           show_snapshot=not args.no_snapshot,
                           theme=args.theme,
                           transcript_spec=args.transcript,
                           extra_images=images["extra"],
                           no_avatars=args.no_avatars, from_json=bool(data_path),
                           video_files=video_files, video_size=video_size,
                           audio_files=audio_files, data=mark)
        return page

    def save(verb: str) -> None:
        """The page made first, with everything it downloads; only then the
        data (with --json write or refresh) and at once the page, so an
        interrupted run or a lost connection leaves both as they were (review
        B1: the images were downloaded between the two)."""
        content, mark = None, read_mark   # --json load: the data read; else none
        if write_json or updating:
            content = json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8")
            mark = data_mark(content)
        page = page_html(mark)
        if content is not None:
            # adding to saved data: back into the file read, whatever its name
            json_path = data_path if updating else replaced or target.with_suffix(".info.json")
            write_atomically(json_path, content)
            log(f"{verb} metadata: {json_path}")
        write_atomically(target, page)
        if getattr(args, "transcript_srt", False):
            write_srt_files(info, args.transcript, target)

    def rebuild() -> None:
        save("Updated")
        log(f"Updated page: {target}")

    if deferred:
        # --video may add files; the page and the data follow only then
        log(f"Nothing missing in the saved data; the page stays as it is unless --video "
            f"adds files: {found}")
        if not made_from(found, read_mark):
            log(PAGE_BEHIND_NOTE)
        outcome.update(target=target, deferred=info, rebuild=rebuild, updating=True)
        return None
    save("Updated" if updating else "Saved")
    size_kb = target.stat().st_size / 1024
    log(f"{'Replaced' if found else 'Saved'} page: {target} ({size_kb:,.0f} KB, "
        f"{len(shown().get('comments') or [])} comments)")
    if original_uncertain(info):
        log(uncertain_warning(info))
    outcome.update(target=target, info=info, rebuild=rebuild, updating=updating)
    if (not data_path or args.refresh_json) and comments_interrupted(info):
        log("  Comments were interrupted (Ctrl+C): the page has those fetched so far "
            "and counts as finished; " + (
                "use --json refresh again for this video to get the rest (--page ovr "
                "would replace the saved comments)." if data_path else
                "use --page ovr for this video to fetch them again."))
        raise CommentsInterrupted
    return target


# --------------------------------------------------------------------------
# --video: the video file itself
# --------------------------------------------------------------------------

class VideoSkipped(Exception):
    """The video cannot be taken (no ffmpeg, no formats, nothing in the
    limit): the page stays, the reason is told, the run goes on."""


class PartlyDownloaded(Exception):
    """The connection was lost after --video downloaded something for the
    video: `result`, `formats` and `cause` for take_video(), which records
    what is there before the run stops (review A4)."""

    def __init__(self, result: dict, formats: list | None, cause: Exception):
        super().__init__(str(cause))
        self.result, self.formats, self.cause = result, formats, cause


def audio_gap_key(code: str, role: str | None) -> str:
    """The key in NOT_SAVED_KEY of an extra audio track asked for, in the
    words of --video ("audio pl", "audio pl.dubbed")."""
    return f"audio {code}" + (f".{role}" if role else "")


def video_gaps(gaps: dict, changes: dict, ovr: bool) -> dict:
    """NOT_SAVED_KEY after a run of --video: `changes` (download_video()'s
    "_gaps") applied to `gaps`; with "ovr" (all downloaded anew) the old
    notes of --video go first. A note given again with the same reason
    keeps its first day."""
    gaps = {key: entry for key, entry in gaps.items()
            if not (ovr and key.partition(" ")[0] in ("video", "audio"))}
    for key, entry in changes.items():
        if entry is None:
            gaps.pop(key, None)
        elif (gaps.get(key) or {}).get("reason") != entry["reason"]:
            gaps[key] = entry
    return gaps


# How to install what --video needs, per system.
INSTALL_HINTS = {
    "ffmpeg": {"win32": "winget install --id Gyan.FFmpeg -e, then open a new terminal",
               "darwin": "brew install ffmpeg",
               "linux": "your package manager, e.g. sudo apt install ffmpeg"},
    "deno": {"win32": "winget install DenoLand.Deno", "darwin": "brew install deno",
             "linux": "curl -fsSL https://deno.land/install.sh | sh"},
}
YT_DLP_MAX_AGE_DAYS = 60     # older: a note before the download
PAUSE_SECONDS = (5, 15)      # between videos of a run, chosen at random


def install_hint(program: str) -> str:
    hints = INSTALL_HINTS[program]
    return hints.get(sys.platform, hints["linux"])


def yt_dlp_age(version: str, today: dt.date | None = None) -> int | None:
    """Days since the yt-dlp release (its version is a date, "2026.10.26",
    sometimes with a fourth part); None if the version is not a date."""
    try:
        released = dt.date(*map(int, version.split(".")[:3]))
    except (TypeError, ValueError):
        return None
    return ((today or dt.date.today()) - released).days


def yt_download(info: dict, opts: dict) -> None:
    """Let yt-dlp download (and merge) the formats `opts` names, from data
    fetched seconds before (as yt-dlp does with --load-info-json)."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.process_ie_result(json.loads(json.dumps(info)), download=True)


def choose_variant(choice: FormatChoice, spec: dict) -> dict | None:
    """The best variant within the --video limit (height, and frame rate if
    given); the limit is an upper bound, not an exact match."""
    return next((v for v in choice.variants
                 if (spec["height"] is None or v["height"] <= spec["height"])
                 and (spec["fps"] is None or v["fps"] <= spec["fps"])), None)


def limit_text(spec: dict) -> str:
    if spec["height"] is None:
        return f"{DEFAULT_MAX_HEIGHT}p"
    return f"{spec['height']}p" + (str(spec["fps"]) if spec["fps"] else "")


def replaced_data_note(path: Path, fresh: dict) -> str:
    """--page ovr --json write: the saved data at `path` are replaced by fresh
    ones; what only they had is lost and named, so nothing goes unnoticed:
    translations of one's own, comments YouTube no longer has, YouTube's
    translations the fresh data lack, the record of downloaded files."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"Replacing saved data that could not be read: {path}"
    lost = []
    transcript = old.get(TRANSCRIPT_KEY) or {}
    fresh_languages = set(translations_of(fresh.get(TRANSCRIPT_KEY) or {}))
    own = sorted(own_of(transcript))
    if own:
        lost.append(f"own translation{'s' if len(own) > 1 else ''} {', '.join(own)}")
    if (transcript.get("fixed") or {}).get("cues"):
        lost.append("own corrections of the transcript")
    gone = sum(1 for c in old.get("comments") or [] if c.get(MISSING_SINCE_KEY))
    if gone:
        lost.append(f"{plural(gone, 'comment')} no longer on YouTube")
    youtube = sorted(code for code in translations_of(transcript) if code not in fresh_languages)
    if youtube:
        lost.append(f"YouTube's translation{'s' if len(youtube) > 1 else ''} into "
                    f"{', '.join(youtube)}")
    if old.get(VIDEO_KEY):
        lost.append("the record of the downloaded files")
    try:
        since = f" from {format_date(old.get(ARCHIVED_KEY))}" if old.get(ARCHIVED_KEY) else ""
    except ValueError:
        since = ""
    return (f"Replacing the saved data{since}: {path}"
            + (f" (lost: {'; '.join(lost)})" if lost else ""))


def described(entry: dict, earlier: list, *same: str) -> dict:
    """A file found next to the page, with what earlier saved data say about
    it (when it was downloaded, from which format) if they describe the same
    file: the same size and the same `same` fields (code and role, code and
    kind). Its name is the current one."""
    for old in earlier:
        if (old.get("date") and old.get("size") == entry["size"]
                and all(old.get(key) == entry.get(key) for key in same)):
            return {**old, **entry}
    return entry


def download_video(source: str, args: argparse.Namespace, page: Path,
                   notes: set, page_info: dict | None = None,
                   previous: dict | None = None) -> tuple[dict, list | None, bool]:
    """--video for one video whose page is `page`: a fresh list of formats
    (the addresses in older data expire), the variant within the limit
    (FormatChoice), checks, then yt-dlp downloads and merges it into an mp4
    named like the page; then the extra audio tracks. Files already
    next to the page (by the video's id and their ending, whatever the rest
    of the name) are kept, unless `again`, and described too, with the
    details `previous` (the saved data's VIDEO_KEY) gives for the same file.
    Returns the result (VIDEO_KEY), the fresh list of formats (None when none
    was needed) and whether anything was downloaded; the result's "_gaps"
    (not saved as it is) are the changes to NOT_SAVED_KEY: a track not
    offered or not downloaded noted ({reason, date, again}), one there now
    None (its note goes). A track that fails is told and the others go on
    (review A4: one failure lost the record of all). Raises VideoSkipped
    when the video cannot be taken, PartlyDownloaded when the connection is
    lost after something was downloaded; yt-dlp's other errors pass on.
    `notes` keeps the notes told once per run."""
    spec = args.video
    folder = page.parent
    ident = video_id_from_url(source) or source_id(page)
    previous = previous or {}
    today = dt.date.today().isoformat()
    result = {"date": today, "parameters": spec["text"]}
    new = False
    existing = video_files_for({"id": ident}, folder)
    take_image = not existing or spec["ovr"]
    if not take_image:
        log(f"Video skipped (file exists): {folder / existing[0]}. "
            'Add "ovr" to --video to download it again.')
        old = previous.get("video")
        result["video"] = described({"file": existing[0],
                                     "size": (folder / existing[0]).stat().st_size},
                                    [old] if old else [])
        if not spec["audio"]:
            return result, None, False
        # a page left as it is (page_info None: not made now) with every track
        # asked for already here too: nothing to ask YouTube, so the next video
        # need not wait either (a page made now takes the fresh list, which
        # tells which formats the files came from)
        audio_files = files_at_hand(folder, ident, spec) if page_info is None else None
        if audio_files is not None:
            for (code, role), path in audio_files:
                log(f'Audio skipped (file exists): {path}. Add "ovr" to --video to download '
                    'it again.')
            result["_gaps"] = {audio_gap_key(code, role): None for code, role, _ in spec["audio"]}
            result["extra_audio"] = [
                described({"audio": code, "role": role, "file": path.name,
                           "size": path.stat().st_size},
                          previous.get("extra_audio") or [], "audio", "role")
                for (code, role), path in audio_files]
            return result, None, False
    # ffmpeg joins image and sound and fixes m4a
    if (take_image or spec["audio"]) and not shutil.which("ffmpeg"):
        raise VideoSkipped(f"ffmpeg not found (it joins image and sound). "
                           f"Install it: {install_hint('ffmpeg')}.")
    if "deno" not in notes and not shutil.which("deno"):
        notes.add("deno")
        log("Note: no JavaScript runtime (Deno) found, so YouTube may offer fewer formats. "
            f"Install it: {install_hint('deno')}")
    age = yt_dlp_age(yt_dlp.version.__version__)
    if "yt-dlp" not in notes and age is not None and age > YT_DLP_MAX_AGE_DAYS:
        notes.add("yt-dlp")
        log(f"Note: yt-dlp {yt_dlp.version.__version__} is {age} days old; if the download "
            "fails, update: uv tool upgrade yt-video-archiver")
    taking(args, ident)
    log("Video: fetching the current list of formats...")
    notes.add("requested")   # the next video waits a moment first
    not_before = time.monotonic() + translation_wait()   # for translated subtitles
    # by the id: the source may be a saved data file (--json write)
    fresh = fetch_formats(f"{YOUTUBE}/watch?v={ident}", args)
    # the page's data (made now) knows the transcript's track and YouTube's
    # translation languages; the fresh list has neither
    data = with_page_choices(fresh, page_info)
    choice = video_choice(data)
    if choice is not None and data.get(OLANG_KEY) and base_language(
            (choice.original or ("",))[0]) != base_language(data[OLANG_KEY]):
        log(f"  Note: no audio track in '{data[OLANG_KEY]}' (--olang); the video's own audio "
            f"is taken.")
    if choice is None:
        raise VideoSkipped("no video or audio formats in the list (YouTube sometimes sends "
                           "none for a while; try again later).")
    variant = choose_variant(choice, spec)
    audio = choice.main_audio
    if take_image and (not variant or not audio):
        raise VideoSkipped(f"no AV1 or H.264 image up to {limit_text(spec)} "
                           "(see Video formats on the page)." if not variant else
                           "no audio track in the list.")

    def options(fmt: str, name: str) -> dict:
        opts = {"format": fmt, "merge_output_format": "mp4",
                "outtmpl": {"default": str(folder / name.replace("%", "%%")) + ".%(ext)s"},
                "overwrites": spec["ovr"], "noplaylist": True,
                "quiet": True, "noprogress": False}
        if args.cookies:
            opts["cookiesfrombrowser"] = cookies_spec(args.cookies)
        return opts

    failures = 0
    if take_image:
        audio_codec = codec_name(audio.get("acodec"), AUDIO_CODECS)
        audio_code = choice.track(audio)[0]
        log(f"Video: {variant['quality']} {variant['codec']} + "
            f"{track_text(audio_code, audio_codec)}, mp4, {variant['size']}")
        # ovr: into the mp4 that is there, whatever its name (the parameter "ovr")
        target = next((folder / name for name in existing if name.lower().endswith(".mp4")),
                      folder / f"{page.stem}.mp4")
        yt_download(fresh, options(f"{variant['format']['format_id']}+{audio['format_id']}",
                                   target.stem))
        size = target.stat().st_size
        log(f"Saved video: {target} ({human_size(size).lstrip('~')})")
        result["_image_new"] = True   # told to take_video(), not saved
        result["video"] = {"file": target.name, "size": size, "date": today,
                           "quality": variant["quality"], "codec": variant["codec"],
                           "video_format": variant["format"]["format_id"],
                           "audio": None if audio_code == UNKNOWN_LANGUAGE else audio_code,
                           "audio_codec": audio_codec, "audio_format": audio["format_id"]}
        new = True
    # The extra audio tracks: separate m4a files, each on its own.
    wanted, messages, asked, absent = resolve_audio(choice, spec["audio"])
    if messages:
        log("Audio: " + "; ".join(messages) + ".")
        log("  Tracks to add: " + ("; ".join(extra_track_lines(choice.extra)) or "none"))
    gaps = result["_gaps"] = {key: None for key in asked.values()}   # there unless told
    for key in absent:
        note_gap(gaps, key, "YouTube offers no such audio track", again=False)
    extra = []
    for number, key in enumerate(wanted):
        fmt = choice.audio_for(key, "aac")
        there = file_by_suffix(folder, ident, audio_suffix(key), "audio files")
        name = there or audio_name(page, key)
        entry = {"audio": key[0], "role": key[1], "file": name.name}
        if there and not spec["ovr"]:
            log(f'Audio skipped (file exists): {name}. Add "ovr" to --video to download '
                'it again.')
            extra.append(described({**entry, "size": name.stat().st_size},
                                   previous.get("extra_audio") or [], "audio", "role"))
            continue
        log(f"Audio: {key[0]}" + (f" ({key[1]})" if key[1] else "")
            + f", m4a, {human_size(format_size(fresh, fmt))}")
        try:
            yt_download(fresh, options(fmt["format_id"], name.stem))
        except Exception as error:
            reason = failure_reason(error)
            if no_connection(error):
                for left in wanted[number:]:   # this one and those after it
                    note_gap(gaps, asked[left], "no connection", again=True)
                if extra:
                    result["extra_audio"] = extra
                if not new:
                    raise
                raise PartlyDownloaded(result, fresh.get("formats"), error) from error
            log(f"Audio FAILED: {key[0]}" + (f" ({key[1]})" if key[1] else "")
                + f": {reason}. The other files go on; run the same command again to retry.")
            note_gap(gaps, asked[key], reason, again=True)
            failures += 1
            continue
        size = name.stat().st_size
        log(f"Saved audio: {name} ({human_size(size).lstrip('~')})")
        extra.append({**entry, "size": size, "date": today, "audio_format": fmt["format_id"]})
        new = True
    if extra:
        result["extra_audio"] = extra
    if failures:
        result["_failures"] = failures   # told to the run, not saved
    return result, fresh.get("formats"), new


AUDIO_PRIORITY = ("dubbed", "auto-dubbed")   # audio:CODE without a role, in this order


def recognised_code(code: str, known: set) -> bool:
    """A language code that exists: one in the video's data, an ISO 639-1
    code (yt-dlp's table: "pl", "iw"; not "jp"), or any three-letter one
    ("fil", "yue": too many to check)."""
    base = base_language(code)
    return (base in {base_language(k) for k in known} or len(base) == 3
            or ISO639Utils.short2long(base) is not None)


def resolve_audio(choice: FormatChoice, requests: list) -> tuple[list, list[str], dict, list]:
    """The extra tracks the audio: parameters ask for, messages for the rest,
    the key in NOT_SAVED_KEY of the request behind each track found or
    already in the video ({track: key}), and those of the tracks not offered. A code matches exactly, else by language when it has no region
    ("de" finds "de-DE"; "de-AT" does not); without a role: dubbed, then
    auto-dubbed, then other kinds, never an audio description (a regional
    variant each time: the first by role, then code); with a role exactly
    that one (the four known ones, or a kind this video has). Only tracks
    with an ordinary aac format can be added; each once."""
    roles = set(AUDIO_ROLES) | {role for _, role in choice.tracks if role}
    found, messages, asked, keys, absent = [], [], {}, {}, []
    for code, role, text in requests:
        # the code is written the usual way (standard_code()), as in the data;
        # a code with a region never equals a bare language, so "de-AT" stays
        same = ([key for key in choice.tracks if key[0] == code]
                or [key for key in choice.tracks if base_language(key[0]) == code])
        if role and role not in roles:
            messages.append(f'"{text}" not recognised')
            continue
        if role:
            fits = [key for key in same if key[1] == role]
        else:
            fits = sorted((key for key in same if key[1] != "descriptive"),
                          key=lambda key: (AUDIO_PRIORITY.index(key[1]) if key[1] in AUDIO_PRIORITY
                                           else len(AUDIO_PRIORITY), key[0]))
        if fits and fits[0] == choice.in_video:
            messages.append(f'"{text}" is already in the video')
            keys.setdefault(fits[0], audio_gap_key(code, role))
        elif not fits or fits[0] not in choice.extra:
            messages.append(f'"{text}" not available')
            absent.append(audio_gap_key(code, role))
        elif fits[0] in asked:   # "de" and "de-DE": one track
            messages.append(f'"{text}" gives the same track as "{asked[fits[0]]}"; taken once')
        else:
            found.append(fits[0])
            asked[fits[0]] = text
            keys[fits[0]] = audio_gap_key(code, role)
    return found, messages, keys, absent


# The video's own subtitles as --video-formats lists them (what --transcript
# CODE.author and CODE.auto-generated take).
SUB_KIND_TEXT = {"author": "by the author", "auto-generated": "auto-generated"}


def timedtext_url(formats: list[dict]) -> str | None:
    """A subtitles track's address on YouTube's subtitles service (fetched as
    json3, and the SRT file made from it). Entries that are HLS playlists
    (yt-dlp lists some as automatic captions) are left out."""
    for fmt in formats:
        if "/api/timedtext" in str(fmt.get("url") or ""):
            return fmt["url"]
    return None


def subtitle_tracks(info: dict) -> dict:
    """The subtitles a download can take: {"author": {code: url},
    "auto-generated": {code: url}} (automatic ones: yt-dlp's "-orig" tracks,
    made from one audio track's speech), SRT addresses only, by code."""
    # (live chat replays have no subtitles address, so they drop out too)
    author = {code: timedtext_url(fmts) for code, fmts in (info.get("subtitles") or {}).items()
              if fmts}
    auto = {code.removesuffix("-orig"): timedtext_url(fmts)
            for code, fmts in (info.get("automatic_captions") or {}).items()
            if code.endswith("-orig") and fmts}
    return {"author": {c: u for c, u in sorted(author.items()) if u},
            "auto-generated": {c: u for c, u in sorted(auto.items()) if u}}


def files_at_hand(folder: Path, ident: str | None, spec: dict) -> list | None:
    """The files next to the page for every audio: parameter, found without
    the list of formats: [((code, role), path)], or None when one is missing
    (then the list decides). A file counts by its ending after "[id]"
    ("[id].pl.dubbed.m4a"): its code as resolve_audio() matches it (exact,
    else a bare language finds a regional code), its role as asked; without
    one, any track but an audio description."""
    def endings(extension: str) -> list[tuple[str, str | None, Path]]:
        found = []
        for path in files_with_id(folder, ident, (extension,)):
            if FORMAT_PART_RE.search(path.name):   # a part of an unfinished download
                continue
            parts = path.name[path.name.rindex(f"[{ident}]") + len(ident) + 2:].split(".")
            if len(parts) in (3, 4) and LANGUAGE_CODE.fullmatch(parts[1]):
                found.append((parts[1], parts[2] if len(parts) == 4 else None, path))
        return found

    def first(found, code, fits):
        same = ([f for f in found if f[0] == code]
                or [f for f in found if base_language(f[0]) == code])
        same = [f for f in same if fits(f[1])]
        return same[0] if same else None

    audio = []
    tracks = endings(".m4a")
    for code, role, _ in spec["audio"]:
        hit = first(tracks, code, (lambda r: r == role) if role else
                    (lambda r: r != "descriptive"))
        if not hit:
            return None
        if hit[2] not in [path for _, path in audio]:
            audio.append(((hit[0], hit[1]), hit[2]))
    return audio


def sub_name(page: Path, code: str, kind: str) -> Path:
    return page.parent / (page.stem + sub_suffix(code, kind))


def audio_name(page: Path, key: tuple) -> Path:
    """An extra track's file: "Title [id].pl.auto-dubbed.m4a"."""
    return page.parent / (page.stem + audio_suffix(key))


def source_id(page: Path) -> str | None:
    """The video id in a page's name ("Title [ID].html")."""
    found = re.search(r"\[([A-Za-z0-9_-]{11})\]", page.name)
    return found.group(1) if found else None


LANGUAGE_CODE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")


def standard_code(code: str) -> str:
    """A language code in the usual notation, as YouTube writes it: "PT-br"
    -> "pt-BR", "zh-hans" -> "zh-Hans" (language lower case, region upper
    case, script capitalised). Option values ignore the case of letters."""
    language, *rest = code.split("-")
    return "-".join([language.lower()] + [part.upper() if len(part) == 2 else
                                          part.capitalize() for part in rest])
# --page: what the page has (defaults: comments up to DEFAULT_COMMENTS,
# snapshot tile, info cards and comment avatars yes, theme auto) and ovr.
DEFAULT_COMMENTS = 1000
PAGE_SWITCHES = ("snap", "cards", "avatars")


def page_spec(value: str) -> dict:
    """Parse --page: "" (the option alone: the defaults) or comma-separated
    parameters: "ovr" (make the page again even if the video has one),
    "comments:no|0|N|all" (no comments asked for; asked, none taken; at most
    N, replies counted; all), "snap:yes|no", "cards:yes|no", "avatars:yes|no",
    "theme:auto|light|dark"; any case of letters. Each parameter once.
    Raises ArgumentTypeError (before anything is fetched), also for an
    address taken as parameters ("--page URL")."""
    spec = {"ovr": False, "comments": None, "snap": True, "cards": True, "avatars": True,
            "theme": "auto", "given": set()}
    if not value.strip():
        return spec
    for item in (part.strip() for part in value.split(",")):
        if not item:
            raise argparse.ArgumentTypeError(f"empty item in '{value}' (check the commas)")
        key, colon, setting = (part.strip().lower() for part in item.partition(":"))
        name = key
        if name in spec["given"]:
            raise argparse.ArgumentTypeError(f"'{name}' is given more than once")
        if key == "ovr" and not colon:
            spec["ovr"] = True
        elif key == "comments" and colon:
            if setting in ("no", "all"):
                spec["comments"] = setting
            elif setting.isdigit():
                spec["comments"] = int(setting)
            else:
                raise argparse.ArgumentTypeError(
                    f"'{item}': comments is no, a number (0 too) or all, e.g. comments:200")
        elif key in PAGE_SWITCHES and colon:
            if setting not in ("yes", "no"):
                raise argparse.ArgumentTypeError(f"'{item}': {key} is yes or no")
            spec[key] = setting == "yes"
        elif key == "theme" and colon:
            if setting not in THEMES:
                raise argparse.ArgumentTypeError(f"'{item}': theme is auto, light or dark")
            spec["theme"] = setting
        else:
            raise argparse.ArgumentTypeError(
                f"'{item}' is not a parameter (use ovr, comments:N, snap:no, cards:no, "
                f"avatars:no or theme:dark); the video's address goes before --page")
        spec["given"].add(name)
    return spec


ONCE_GIVEN = "_given_once"   # the options Once has seen in this command


class Once(argparse.Action):
    """An option that may be given only once: a second --page would silently
    replace the first (argparse keeps the last), so it is an error; all
    parameters go in one comma-separated value. What was given is kept apart
    (ONCE_GIVEN, removed after parsing), not read from the value: an option
    with a default (--transcript [], -o the current folder) counts too."""
    def __call__(self, parser, namespace, values, option_string=None):
        given = namespace.__dict__.setdefault(ONCE_GIVEN, set())
        if self.dest in given:
            parser.error(f"{option_string} is given more than once"
                         + ("; put all its parameters in one, comma-separated"
                            if self.dest in ("page", "transcript", "video") else ""))
        given.add(self.dest)
        setattr(namespace, self.dest, values)


JSON_PARAMETERS = ("write", "refresh", "load", "merge", "show")

DEFAULT_TAKE = 100   # videos that ask YouTube, at most, in one run (--take)


def take_value(value: str) -> int | str:
    """--take N|all: a whole number, 1 or more, or "all" (any case). 0 and
    no are errors: in comments: "no" means none, and a run that takes no
    video would do nothing."""
    text = value.strip().lower()
    if text == "all":
        return text
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(
            f"'{value}': --take is how many videos a run takes from YouTube, 1 or more, "
            f"or all")
    return int(text)


class TakeReached(Exception):
    """--take: a video would ask YouTube, but the run has taken its number."""


def taking(args: argparse.Namespace, ident: str | None) -> None:
    """--take, called just before a video's first question to YouTube: the
    video (with all its files) counts once, the run's videos up to the
    limit; past it, TakeReached, before anything is asked or written.
    Videos left as they are, without YouTube, do not count."""
    taken = getattr(args, "taken", None)
    if taken is None:
        taken = args.taken = set()
    if ident in taken:
        return
    limit = getattr(args, "take_limit", None)
    if limit is not None and len(taken) >= limit:
        raise TakeReached(ident)
    taken.add(ident)


def take_exhausted(args: argparse.Namespace) -> bool:
    """Whether --take's number is taken: no other video asks YouTube now."""
    limit = getattr(args, "take_limit", None)
    return limit is not None and len(getattr(args, "taken", None) or ()) >= limit


def take_note(args: argparse.Namespace, left: int) -> str:
    """The run's end when --take left videos: how far it got, and how to go on."""
    taken = len(args.taken)
    total = taken + left
    text = (f"--take {args.take_limit}: {taken} of {total} videos that need YouTube were "
            f"taken; ")
    if args.overwrite or args.refresh_json:
        every = "--page ovr" if args.overwrite else "--json refresh"
        return text + (f"{every} asks YouTube for every video, so the same command would "
                       f"take the same ones again: give --take {total} or --take all.")
    return text + "run the same command again for the next ones."


def json_parameter(value: str) -> str:
    """--json: one of JSON_PARAMETERS, any case."""
    if value.strip().lower() not in JSON_PARAMETERS:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a parameter (use write, refresh, load, merge or show); the "
            f"video's address goes before --json")
    return value.strip().lower()


def apply_json_parameter(args: argparse.Namespace) -> None:
    """What --json PARAMETER asks for, as the attributes the program reads:
    write saves the data (write_json), adding to the saved ones where there
    are any (update_json; else a download that saves them), or with --page
    ovr a download that replaces them."""
    mode = args.json
    args.write_json = mode == "write"
    args.update_json = mode == "write" and not args.overwrite
    args.refresh_json, args.load_json, args.merge_json, args.show_json = (
        mode == "refresh", mode == "load", mode == "merge", mode == "show")


def apply_page_spec(args: argparse.Namespace) -> None:
    """The settings --page gives (args.page; None without the option), as
    the attributes the rest of the program reads: overwrite, no_comments,
    max_comments (an explicit limit, which also narrows saved comments),
    comment_limit (the download's: DEFAULT_COMMENTS when not given, None for
    all), no_snapshot, no_cards, no_avatars, theme."""
    spec = args.page if args.page is not None else page_spec("")
    comments = spec["comments"]
    args.overwrite = spec["ovr"]
    args.no_comments = comments == "no"
    args.max_comments = comments if isinstance(comments, int) else None
    args.comment_limit = (DEFAULT_COMMENTS if comments is None else
                          None if comments in ("all", "no") else comments)
    args.no_snapshot, args.no_cards = not spec["snap"], not spec["cards"]
    args.no_avatars, args.theme = not spec["avatars"], spec["theme"]


TRANSCRIPT_VERSIONS = ("plain", "timed", "dual")


def transcript_spec(value: str) -> list[tuple[str, str | None]]:
    """Parse --transcript: "" (the option alone: plain) or comma-separated
    versions, each with a language code or without one (the original):
    "plain,timed:pl,dual:de"; "srt" (an SRT file for each of them, next to
    the page) and "ovr" (the saved transcript downloaded again, with --json). Without the option there is no transcript ([], the
    parser's default).

    Returns the versions in the given order as (version, language) pairs,
    srt and ovr as ("srt", None), ("ovr", None). Any case of letters ("Plain", "dual:PT-br").
    Raises ArgumentTypeError (before anything is fetched) for unknown
    versions ("none" too: without the option there is no transcript), also
    for an address taken as versions ("--transcript URL"), dual without a
    language code and a version given twice in the same words.
    """
    if not value.strip():
        return [("plain", None)]
    items = [item.strip().lower() for item in value.split(",")]
    if any(not item for item in items):
        raise argparse.ArgumentTypeError(f"empty item in '{value}' (check the commas)")
    spec = []
    for item in items:
        version, colon, language = item.partition(":")
        if item in ("ovr", "srt"):
            entry = (item, None)
        elif version not in TRANSCRIPT_VERSIONS:
            raise argparse.ArgumentTypeError(
                f"'{item}' is not a parameter (use plain, timed, dual:CODE, srt or ovr, a "
                f"version with :CODE for a language); the video's address goes before "
                f"--transcript")
        elif version == "dual" and not language:
            raise argparse.ArgumentTypeError("'dual' needs a language code, e.g. dual:pl")
        elif colon and not language:
            raise argparse.ArgumentTypeError(f"'{item}': a language code goes after the colon")
        else:
            code, dot, kind = language.partition(".")
            if language:
                check_code(code, item)
            if dot and kind not in SOURCE_KINDS:
                raise argparse.ArgumentTypeError(
                    f"'{item}': the kind after the code is one of {', '.join(SOURCE_KINDS)}, "
                    f"e.g. {version}:{code}.author")
            if version == "dual" and kind in TRACK_KINDS:
                raise argparse.ArgumentTypeError(
                    f"'{item}': dual puts the translation of the same lines beside the "
                    f"original, so only dual:{code}, dual:{code}.translated or "
                    f"dual:{code}.own")
            entry = (version, (standard_code(code) + (f".{kind}" if kind else ""))
                     if language else None)
        if entry in spec:
            raise argparse.ArgumentTypeError(f"'{item}' is given more than once")
        spec.append(entry)
    if all(version in ("ovr", "srt") for version, _ in spec):
        spec.insert(0, ("plain", None))   # "ovr" or "srt" alone: with the option alone
    return spec


def olang_code(value: str) -> str:
    """--olang: a language code, written the usual way."""
    check_code(value.strip(), value.strip())
    return standard_code(value.strip())


def check_code(code: str, item: str) -> None:
    """A language code in a parameter must be one: written as a code and
    known to yt-dlp's ISO 639-1 table or of three letters ("jp" is not), so a
    mistyped language stops the run before anything is fetched. Whether the
    video has it is checked later."""
    if not LANGUAGE_CODE.fullmatch(code) or not recognised_code(code, set()):
        raise argparse.ArgumentTypeError(
            f"'{item}': '{code}' is not a language code (e.g. pl, en, pt-BR)")


VIDEO_QUALITY = re.compile(r"(\d+)p(\d+)?")


def video_spec(value: str) -> dict:
    """Parse --video: "" (the default), or comma-separated parameters: a
    quality as an upper limit ("720p": up to 720p at any frame rate;
    "720p30": up to 720p and 30 fps; at most 1080p), "ovr" (download the
    files that are there again) and extra audio tracks ("audio:pl",
    "audio:pl.auto-dubbed"), one item for each (the code is written the
    usual way); any case of letters. Subtitles are not here: --transcript
    srt. Raises ArgumentTypeError (before anything is fetched), also for an
    address taken as parameters ("--video URL"), a code that is no language
    code, and a repeated item: the same one twice, or a language both alone
    and with a role: what was meant is not clear."""
    spec = {"height": None, "fps": None, "ovr": False, "audio": [],
            "text": value.strip().lower()}
    if not spec["text"]:
        return spec
    for item in (part.strip() for part in value.split(",")):
        if not item:
            raise argparse.ArgumentTypeError(f"empty item in '{value}' (check the commas)")
        quality = VIDEO_QUALITY.fullmatch(item.lower())
        kind, _, rest = item.partition(":")
        if kind.lower() == "audio" and rest:
            # audio:CODE or audio:CODE.ROLE (split at the first dot: codes have
            # none, roles may have a hyphen); checked against the video later
            code, _, role = rest.partition(".")
            if not LANGUAGE_CODE.fullmatch(code) or (role and not re.fullmatch(r"[a-z-]+", role.lower())):
                raise argparse.ArgumentTypeError(
                    f"'{item}': write audio:CODE or audio:CODE.ROLE, e.g. audio:pl or "
                    f"audio:pl.auto-dubbed")
            check_code(code, item)
            add_once(spec["audio"], (standard_code(code), role.lower() or None, item))
        elif item.lower() == "ovr":
            if spec["ovr"]:
                raise argparse.ArgumentTypeError("'ovr' is given more than once")
            spec["ovr"] = True
        elif quality and int(quality.group(1)) in QUALITY_LADDER:
            height, fps = int(quality.group(1)), quality.group(2)
            if height > DEFAULT_MAX_HEIGHT:
                raise argparse.ArgumentTypeError(
                    f"'{item}' is above {DEFAULT_MAX_HEIGHT}p; yt2html downloads up to "
                    f"{DEFAULT_MAX_HEIGHT}p")
            if spec["height"] is not None:
                raise argparse.ArgumentTypeError("give one quality only")
            if fps is not None and not 1 <= int(fps) <= 120:
                raise argparse.ArgumentTypeError(f"'{item}': no such frame rate")
            spec["height"], spec["fps"] = height, int(fps) if fps else None
        else:
            raise argparse.ArgumentTypeError(
                f"'{item}' is not a parameter (use a quality such as 720p or 720p30, "
                f"audio:CODE or ovr; subtitles: --transcript srt); the video's address goes "
                f"before --video")
    return spec


def add_once(items: list, new: tuple) -> None:
    """Add (code, role, text) to the audio: items, refusing the same one
    twice and a code both alone and with a role."""
    for code, sort, text in items:
        if code == new[0] and sort == new[1]:
            raise argparse.ArgumentTypeError(f"'{new[2]}' is given more than once")
        if code == new[0] and (sort is None or new[1] is None):
            raise argparse.ArgumentTypeError(
                f"'{text}' and '{new[2]}': give the language alone or with a role each "
                f"time, not both")
    items.append(new)


def plural(n: int, word: str) -> str:
    """'1 video', '2 videos'."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def has_wildcards(text: str) -> bool:
    return "*" in text or "?" in text


def expand_pattern(source: str) -> list[str]:
    """--json load paths with * or ?, expanded by the program (Windows shells
    pass them on as they are), sorted. Square brackets stay literal, since
    every page name ends with "[VIDEO_ID]". A pattern without matches is kept;
    saved_data_path() reports it."""
    if not has_wildcards(source):
        return [source]
    pattern = re.sub(r"[][]", lambda m: f"[{m.group(0)}]", source)
    return sorted(glob.glob(pattern)) or [source]


def saved_data_path(source: str, output_dir: Path) -> Path:
    """The .info.json file for a --json load source: an existing file; else a
    video id, or any file name with "[ID]" (a page, a video, saved data),
    looked up in the output folder as a ".info.json" file with "[ID]" in its
    name. Raises ValueError with what was looked for."""
    path = Path(source)
    if path.is_file() and path.name.lower().endswith(".json"):   # (else a name with the id)
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


def update_source(source: str) -> str:
    """A --json write source: a saved data file stays a file (it may lie
    outside the output folder), any other name with an id becomes its URL."""
    if source.lower().endswith(".json") and Path(source).is_file():
        return source
    return source_from_page_name(source)


def same_folder(a: Path, b: Path) -> bool:
    """Whether two folders are the same one (relative or absolute, any case
    on Windows, "." and ".." resolved)."""
    try:
        return os.path.samefile(a, b)
    except OSError:   # one of them does not exist (yet)
        return (os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b)))


def placed_source(source: str, output_dir: str) -> str:
    """A source that names a file (not an address or an id) belongs to the
    output folder: a bare name is taken there; a path elsewhere is an error,
    so that a video's page, data and files never split between two folders.
    Raises ValueError with the -o that would fit."""
    if ("://" in source or video_id_from_url(source) or playlist_id_from_url(source)
            or is_channel_url(source)):
        return source
    path = Path(source)
    if path.parent == Path("."):
        return source if output_dir == DEFAULT_OUTPUT_DIR else str(Path(output_dir) / source)
    if not same_folder(path.parent, Path(output_dir)):
        raise ValueError(f"{source} is not in the output folder ({output_dir}); the pages, "
                         f"their data and files stay in one folder: add -o \"{path.parent}\" "
                         "or give the name only")
    return source


def collect_sources(args: argparse.Namespace) -> list[str]:
    """The sources given in the command, each video only once.

    Two URLs are the same video if they have the same video id (e.g.
    youtu.be/ID and watch?v=ID); anything else (playlist URLs, .info.json
    paths) must match exactly. Raises ValueError if there is nothing to do.
    """
    files = args.load_json or args.merge_json or args.show_json   # file names as they are
    what = "file" if files else "URL"
    seen = set()
    args.sources = [placed_source(source, args.output_dir) for source in args.sources]
    if files:
        args.sources = [path for source in args.sources for path in expand_pattern(source)]
    else:
        adding = args.update_json or args.refresh_json
        args.sources = [update_source(path) if adding else source_from_page_name(path)
                        for source in args.sources
                        for path in (expand_pattern(source)
                                     if adding and "://" not in source else [source])]

    def new(source: str) -> bool:
        key = source if files else (source_video_id(source) or source)
        if key in seen:
            return False
        seen.add(key)
        return True

    sources = [source for source in args.sources if new(source)]
    if len(sources) < len(args.sources):
        log(f"Ignored {plural(len(args.sources) - len(sources), 'duplicate ' + what)} "
            f"given on the command line.")
    if not sources:
        raise ValueError("give at least one URL, or a list file with --index FILE")
    return sources


# Options --index-only refuses: they download or shape the video pages,
# and it only makes the index from what is in the folder.
INDEX_ONLY_REFUSES = (("--video", "video"), ("--transcript", "transcript"),
                      ("--json", "json"), ("--page", "page"), ("--olang", "olang"),
                      ("--take", "take"))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt2html",
        description="Save a YouTube video page (metadata, description, chapters, "
                    "transcript, comments) as a single offline HTML file.",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__} (yt-dlp {yt_dlp.version.__version__})")
    parser.add_argument("sources", nargs="*", metavar="URL",
                        help="video or playlist URL(s) or id(s), or the name of a page "
                             "made by yt2html ('Title [ID].html'); a playlist archives all its "
                             "videos. With --json load: .info.json file(s), also as patterns "
                             "with * or ?, or video id(s) or page names, looked up in the "
                             "output folder (-o). With --json merge: SRT files of your own "
                             "translations. Not with --index / --index-only (the videos "
                             "then come from the list file)")
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR, action=Once,
                        help="folder for the generated pages, their saved data and files "
                             "(default: the current folder); a file given by its name is "
                             "taken there, one given with a path elsewhere is an error")
    parser.add_argument(
        "--page", nargs="?", const="", type=page_spec, default=None, action=Once,
        metavar="PARAMETERS",
        help="the page, with comma-separated parameters: ovr (make it again even if the "
             "video already has one in the output folder, recognised by '[VIDEO_ID]' in "
             "the .html file's name; such videos are skipped by default), comments:no|0|N|all "
             f"(default {DEFAULT_COMMENTS}; with saved data: fewer of the saved ones), "
             "snap:yes|no (the grey 'Snapshot' date tile), cards:yes|no (the author's info "
             "cards), avatars:yes|no (comment authors' avatars; letters instead), "
             "theme:auto|light|dark; e.g. --page ovr,comments:200,theme:dark")
    parser.add_argument(
        "--transcript", nargs="?", const="", type=transcript_spec, default=[], action=Once,
        metavar="VERSIONS",
        help="also add the transcript (without this option the page has none): alone, "
             "as continuous text in the original language; or versions, comma-separated, "
             "shown in this order: plain (continuous text), timed (one line per caption), "
             "dual:CODE (timed, with the translation into that language beside it), each "
             "with :CODE for a language (plain:pl, timed:de: your own translation, else "
             "YouTube's, else the author's subtitles, else the automatic ones of a dub; "
             ".own, .translated, .author or .auto-generated after the code chooses one) or "
             "without, for "
             "the original; srt (each version's subtitles also as an SRT file next to the page); "
             "ovr (with --json: the saved transcript downloaded again). What YouTube has not or "
             "refuses is left out with a note. Write the address first: yt2html URL "
             "--transcript plain,dual:pl")
    parser.add_argument(
        "--olang", type=olang_code, default=None, action=Once, metavar="CODE",
        help="the video's original language, when you know it better than YouTube shows it: "
             "the transcript is in it, the video's main audio and the Languages table follow it")
    parser.add_argument(
        "--json", type=json_parameter, default=None, action=Once, metavar="PARAMETER",
        help="the saved data next to the page ('Title [ID].info.json': yt-dlp's data and what "
             "yt2html adds), one parameter: write (save them with the page; for a video "
             "whose page and data are there, add only what the options ask for and the data "
             "lack: the transcript, translations YouTube refused before, --video files; "
             "nothing missing leaves the page as it is; with --page ovr everything is "
             "downloaded again and replaces them), refresh (as write, and also refresh what "
             "changes: the counts, title and description, the comments merged with the saved "
             "ones, those YouTube no longer has kept and marked; a video gone from YouTube is "
             "recorded), load (make the pages only from saved data, without YouTube: .info.json "
             "files, also as patterns with * or ?, or video ids and page names looked up in "
             "-o) or merge (add a translation of your own, SRT files 'Title [ID].CODE.own.srt', "
             "or your corrections of the transcript, 'Title [ID].CODE.fixed.srt' in its "
             "language, each with the lines and times of the transcript, merged into the data "
             "with the same id; makes no page: --json load then shows it) or show (list what "
             "the saved data hold, without YouTube: the transcript and its translations with "
             "the versions --json load can show, the comments, the video files, the files next "
             "to them; sources as for load, or --index FILE)")
    parser.add_argument("--cookies", metavar="BROWSER", type=cookies_argument, action=Once,
                        help="use cookies from a browser (firefox, chrome, edge, ...) "
                             "when YouTube asks to sign in; a profile after a colon: "
                             "\"firefox:PROFILE_FOLDER\" (its folder name or path)")
    parser.add_argument("--index", metavar="FILE", action=Once,
                        help="archive the videos of a list file (UTF-8 text: video and "
                             "playlist addresses or ids, page names, video files of your own; "
                             "a '# Title', '## Chapter' to '#### Chapter', '> notes', "
                             "'= Title' above a line, '// comments', settings such as "
                             "'!numbers: no', '!dates: no', '!layout: cards', '!theme: dark') "
                             "and make its index page in the output folder, "
                             "named after the file ('series.txt' -> 'series.html'): built from "
                             "the pages and video files in the folder; playlists are read from "
                             "YouTube each time (the index keeps their lists for when YouTube "
                             "cannot be reached). With --json load, --json write or "
                             "--json refresh: those, then the index")
    parser.add_argument("--index-only", metavar="FILE", action=Once,
                        help="only make the index page of a list file from what is in the "
                             "output folder, downloading nothing (playlists: only their lists "
                             "are read); with -o and --cookies only")
    parser.add_argument("--video", nargs="?", const="", type=video_spec, action=Once,
                        metavar="PARAMETERS",
                        help="also download the video itself, after its page, as an mp4 "
                             "named like the page (needs ffmpeg): the best quality up to "
                             "1080p in AV1, else H.264, with the original audio; then the "
                             "page is updated. Parameters, comma-separated: a quality as an "
                             "upper limit (720p, or 720p30 to limit the frame rate too), "
                             "audio:CODE for an extra audio track as a separate m4a (e.g. "
                             "audio:pl: a dub by the author, else YouTube's; audio:pl.auto-dubbed "
                             "for that one; repeat for more) and ovr (download again the "
                             "files that are already there); subtitles as SRT files: "
                             "--transcript srt. "
                             "Write the address first: yt2html URL --video 720p30")
    # unofficial (the user's, 20.11.2026): --reverse INDEX LIST [details:...], the
    # videos of an index page written to a list file; not in --help
    # (yt2html_index.reverse_index()); details:title,date,lang (22.11.2026, one
    # option since 24.11.2026): each video's title, date and language on "=" lines
    parser.add_argument("--reverse", nargs="+", action=Once, metavar="VALUE",
                        help=argparse.SUPPRESS)
    # unofficial (the user's, 22.11.2026): --channel CHANNEL LIST PERIOD, some
    # videos of a channel, by their dates or the newest N, written to a list
    # file (yt2html_index.channel_list()); at most --take of them
    parser.add_argument("--channel", nargs=3, action=Once, metavar=("CHANNEL", "LIST", "PERIOD"),
                        help=argparse.SUPPRESS)
    parser.add_argument(
        "--take", type=take_value, default=None, action=Once, metavar="N",
        help=f"the number of videos a run takes from YouTube, at most (default "
             f"{DEFAULT_TAKE}), or all; only those that need it count: a video whose page "
             f"and files are there is skipped without it. A playlist longer than that is "
             f"done in parts: run the same command again for the next ones")
    parser.add_argument("--video-formats", action="store_true",
                        help="only list, for each video, the qualities a download can take "
                             "(with the parameter that chooses each, the codec and the size) "
                             "and the audio tracks it can add; no page is made")
    args = parser.parse_args(argv)
    vars(args).pop(ONCE_GIVEN, None)
    args.take_limit = (DEFAULT_TAKE if args.take is None else
                       None if args.take == "all" else args.take)
    args.taken = set()   # the videos that asked YouTube in this run (taking())
    apply_page_spec(args)
    apply_json_parameter(args)
    args.transcript_ovr = ("ovr", None) in args.transcript
    args.transcript_srt = ("srt", None) in args.transcript
    args.transcript = [entry for entry in args.transcript if entry[0] != "ovr"]   # srt stays
    # the options that only write a list file: (what they do, what may go with them)
    alone = {"reverse": ("only writes the list of an index page", ()),
             "channel": ("only writes a list of a channel's videos", ("cookies", "take"))}
    special = next((dest for dest in alone if getattr(args, dest) is not None), None)
    if special:
        option = "--" + special
        what, allowed = alone[special]
        used = [action.option_strings[0] if action.option_strings else "a URL"
                for action in parser._actions
                if action.dest not in ("help", "version", special, *allowed)
                and getattr(args, action.dest) not in (action.default, [])]
        if used:
            parser.error(f"{option} {what}, so it cannot be used with {', '.join(used)}")
        import yt2html_index   # (here: that module imports this one)
        try:
            if special == "channel":
                channel, name, period = args.channel
                choice = yt2html_index.channel_choice(period)
                if isinstance(choice, int) and args.take_limit is not None \
                        and choice > args.take_limit:
                    raise yt2html_index.ListFileError(
                        f"--channel: last:{choice} puts {choice} videos in the list, more than "
                        f"--take {args.take_limit}{' (the default)' if args.take is None else ''};"
                        f" give --take {choice} or --take all")
                args.channel_job = (yt2html_index.channel_videos_url(channel), choice,
                                    *yt2html_index.list_target(name))
            else:
                args.reversed = yt2html_index.reverse_index(*args.reverse)
        except yt2html_index.ListFileError as error:
            parser.error(str(error))
        return args
    if args.show_json:
        used = [name for name, on in (("--page", args.page is not None),
                                      ("--transcript", bool(args.transcript) or args.transcript_ovr),
                                      ("--video", args.video is not None),
                                      ("--video-formats", args.video_formats),
                                      ("--olang", bool(args.olang)),
                                      ("--take", args.take is not None)) if on]
        if used:
            parser.error(f"--json show only shows what the saved data hold, so it cannot be "
                         f"used with {', '.join(used)}")
    if args.take is not None and args.json in ("load", "merge"):
        parser.error(f"--take counts the videos that need YouTube, and --json {args.json} "
                     f"asks YouTube for nothing; leave --take out")
    if args.transcript_ovr and args.json not in ("write", "refresh"):
        parser.error("--transcript ovr downloads the saved transcript again, so it needs "
                     "--json write or --json refresh (without saved data the transcript is "
                     "downloaded anyway)")
    own = [version_text(version, token) for version, token in args.transcript
           if token and split_code(token)[1] == "own"]
    if own and args.json is None:
        # one's own translations are only in the saved data (--json merge)
        kind = "is a translation" if len(own) == 1 else "are translations"
        parser.error(f"{', '.join(own)} {kind} of your own, kept only in saved data: "
                     f"add --json write (or --json load)")
    if args.olang and args.json in ("load", "merge"):
        parser.error(f"--olang chooses what is downloaded, so it cannot be used with --json "
                     f"{args.json}; use --json write --transcript ovr to download the "
                     f"transcript again in that language")
    for version, language in args.transcript if args.olang else []:
        if language and base_language(split_code(language)[0]) == base_language(args.olang):
            if version == "dual":
                parser.error(f"dual:{language} is the original itself (--olang {args.olang}): "
                             f"nothing to put beside it; use timed")
            if (version, None) in args.transcript:
                parser.error(f"{version} and {version}:{language} are the same transcript "
                             f"(--olang {args.olang}); give one of them")
    if args.video_formats:
        used = [name for name, on in (
            ("--page", args.page is not None), ("--index", args.index is not None),
            ("--index-only", args.index_only is not None),
            (f"--json {args.json}", args.json not in (None, "load")),
            ("--transcript", bool(args.transcript)),
            ("--video", args.video is not None)) if on]
        if used:
            parser.error(f"--video-formats makes no page, so it cannot be used with "
                         f"{', '.join(used)}")
    if args.merge_json:
        used = [name for name, on in (("--video", args.video is not None),
                                      ("--page", args.page is not None),
                                      ("--transcript", bool(args.transcript)),
                                      ("--index", args.index is not None),
                                      ("--index-only", args.index_only is not None)) if on]
        if used:
            parser.error(f"--json merge only merges SRT files into the saved data and makes "
                         f"no page, so it cannot be used with {', '.join(used)}"
                         + ("; make the page then with: yt2html ID --json load --transcript "
                            "plain,dual:CODE" if args.transcript else ""))
    if args.overwrite and args.json in ("load", "refresh"):
        parser.error(f"--page ovr downloads everything again from YouTube and --json "
                     f"{args.json} uses the saved data; use one of them")
    if args.video is not None and args.load_json:
        parser.error("--video needs YouTube, so it cannot be used with --json load; "
                     "give the URL instead")
    list_file = args.index if args.index is not None else args.index_only
    if args.index is not None and args.index_only is not None:
        parser.error("--index downloads and makes the index, --index-only only makes it; "
                     "use one of them")
    if args.index_only is not None:
        used = [name for name, key in INDEX_ONLY_REFUSES
                if getattr(args, key) is not None and getattr(args, key) is not False
                and getattr(args, key) != []]
        if used:
            parser.error(f"--index-only downloads nothing and only makes the index, so it "
                         f"cannot be used with {', '.join(used)} (use --index FILE for that)")
    if list_file is not None:
        if args.sources:
            parser.error(f"--index{'-only' if args.index is None else ''} takes the videos "
                         f"from the list file; add {args.sources[0]} to the file instead of "
                         "giving it in the command")
        import yt2html_index   # (here: that module imports this one)
        try:
            args.list = yt2html_index.read_list_file(list_file)
            if args.json != "show":          # (show writes no index)
                yt2html_index.check_index_target(list_file, Path(args.output_dir))
        except yt2html_index.ListFileError as error:
            parser.error(str(error))
        return args
    if (args.load_json or args.merge_json or args.show_json) and args.cookies:
        log(f"Note: --cookies has no effect with --json {args.json} "
            f"(nothing is downloaded).")
    try:
        args.sources = collect_sources(args)
    except ValueError as error:
        parser.error(str(error))
    return args


def readable_output() -> None:
    """Characters the output cannot encode become "?" instead of stopping
    the run. On Windows, output sent to a file or a pipe (`> log.txt`,
    `| Tee-Object`) uses the system's code page, which lacks e.g. the
    full-width `｜` and `：` that yt-dlp puts in file names for `|` and `:`."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(errors="replace")


def main(argv=None) -> int:
    readable_output()
    args = parse_args(argv)
    if getattr(args, "channel_job", None):
        import yt2html_index
        return yt2html_index.channel_list(args)
    if getattr(args, "reversed", None):
        import yt2html_index
        yt2html_index.write_reverse(*args.reversed)
        return 0
    counts = {"saved": 0, "skipped": 0, "failed": 0, "left": 0}
    without_data = []   # --json load --index: videos of the list without saved data
    videos = {"downloaded": 0, "skipped": 0, "failed": 0}
    notes = set()   # --video: notes told once, and whether YouTube was asked
    caption_cache.clear()   # caption tracks: once per run

    run = Run()

    def attempt(source: str, action):
        """Run one step; a failure is reported and counted, the run goes on."""
        try:
            return action()
        except TakeReached:
            left_for_next_run()
            return None
        except NoConnection:
            counts["failed"] += 1
            raise
        except yt_dlp.utils.DownloadError as error:
            if no_connection(error):
                counts["failed"] += 1
                raise NoConnection(failure_reason(error)) from error
            log(f"FAILED: {source}")  # yt-dlp has already printed the reason
        except Exception as error:
            if no_connection(error):
                counts["failed"] += 1
                raise NoConnection(failure_reason(error)) from error
            log(f"FAILED: {source}: {error}")
        counts["failed"] += 1
        return None

    def left_for_next_run():
        """A video --take leaves (it would ask YouTube): counted; said once."""
        if not counts["left"]:
            log(f"--take {args.take_limit} reached: the other videos that need YouTube are "
                f"left for the next run (those that do not are still done).")
        counts["left"] += 1

    def archive_video(source: str, playlist: dict | None = None):
        if args.video is not None and "requested" in notes and not take_exhausted(args):
            # a moment between videos that asked YouTube for a video file
            notes.discard("requested")
            seconds = random.uniform(*PAUSE_SECONDS)
            log(f"Waiting {seconds:.0f} s before the next video...")
            time.sleep(seconds)
        outcome = {}
        try:
            result = attempt(source, lambda: archive(source, args, playlist, outcome)
                             or "skipped")
        except CommentsInterrupted:
            counts["saved"] += 1  # the page was made, then the run stops
            raise
        if result:
            counts["skipped" if result == "skipped" else "saved"] += 1
        if args.video is not None and result and outcome.get("target"):
            take_video(source, outcome)

    def take_video(source: str, outcome: dict):
        """--video after the page: the video file, then the page again (when it
        was made now) with the fresh list of formats and the result. A lost
        connection after files were downloaded: they are recorded too (the
        page and the data), then the run stops (review A4)."""
        lost = None
        try:
            result, formats, new = download_video(source, args, outcome["target"], notes,
                                                  outcome.get("info"), outcome.get("previous"))
        except PartlyDownloaded as part:
            result, formats, new, lost = part.result, part.formats, True, part.cause
            left = [key for key, entry in result["_gaps"].items()
                    if entry and entry["reason"] == "no connection"]
            log("Video: the connection was lost; saved what was downloaded: "
                + ", ".join(video_added(result, outcome.get("previous") or {}))
                + "; not downloaded: " + ", ".join(left) + ".")
            result["_failures"] = 1
        except TakeReached:
            left_for_next_run()
            return
        except VideoSkipped as reason:
            log(f"Video skipped: {reason}")
            videos["skipped"] += 1
            return
        except Exception as error:
            videos["failed"] += 1
            if no_connection(error):
                raise NoConnection(failure_reason(error)) from error
            log(f"Video FAILED: "
                f"{failure_reason(error) if isinstance(error, yt_dlp.utils.DownloadError) else error}"
                ". The page is saved; run the same command again to retry.")
            return
        videos["failed" if result.pop("_failures", 0) else "downloaded" if new
               else "skipped"] += 1
        changes = result.pop("_gaps", {})
        image_new = result.pop("_image_new", False)
        if outcome.get("deferred") is not None:   # --json write, nothing missing in the data
            known = outcome["deferred"].get(NOT_SAVED_KEY) or {}
            if not new and video_gaps(known, changes, args.video["ovr"]) == known:
                return   # nothing added or noted: the page and the data stay as they are
            outcome["info"] = outcome["deferred"]
        if new and not outcome.get("rebuild"):   # a page that was there and is left as it is
            log("  Warning: the page was not made again, so its Video file row does not list "
                "the new files" + (" and the player has no switch for the new audio tracks"
                                   if result.get("extra_audio") else "")
                + ". To add them: --page ovr (everything again from YouTube), or --json "
                "write if the page has saved data.")
        if outcome.get("rebuild"):   # a page made now describes the files, downloaded or found
            info = outcome["info"]
            if formats:
                info["formats"] = formats   # the list the video was chosen from
            info[VIDEO_KEY] = merge_video_record(outcome.get("previous") or {}, result, image_new)
            set_gaps(info, video_gaps(info.get(NOT_SAVED_KEY) or {}, changes, args.video["ovr"]))
            if outcome.get("updating"):
                note_update(info, outcome["record"],
                            video_added(result, outcome.get("previous") or {}))
            outcome["rebuild"]()
        if lost is not None:
            raise NoConnection(failure_reason(lost), partly=bool(outcome.get("rebuild")))

    def archive_list(videos: list[tuple[str, str | None]]):
        """--index: each video of the list (with its playlist, if from one)."""
        for url, playlist_id in videos:
            if args.load_json:
                url = video_id_from_url(url)   # looked up in the output folder
                if not file_with_id(Path(args.output_dir), url, (".info.json",),
                                    "saved data files"):
                    log(f"No saved data for {url}; skipped.")
                    without_data.append(url)
                    continue
            archive_video(url, playlist_context(run, playlist_id, url) if playlist_id else None)

    if args.video_formats:
        return list_formats(args)
    if args.show_json:
        return show_saved(args)
    listing = args.index is not None or args.index_only is not None
    if listing:
        import yt2html_index as index   # (here: that module imports this one)
        try:
            lists, unread = index.start(args, run)   # the playlists, before any download
        except KeyboardInterrupt:
            log("Interrupted (Ctrl+C) while reading the playlists: nothing was downloaded "
                "and the index was not changed.")
            return 130
        if args.index_only is not None:
            return 0 if index.write_index(args, lists) and not unread else 1
        counts["failed"] += unread
    seen = set()  # video ids handled in this run
    playlists = 0
    interrupted = offline = False
    try:
        if listing:
            archive_list(index.videos_to_download(args.list, run))
        # the playlists first (--take counts from the whole list), then the videos
        queue = []   # (video, the playlist it comes from, or None)
        for source in [] if listing else args.sources:
            if not args.load_json and playlist_id_from_url(source):
                playlists += 1
                queue += [(url, source) for url in attempt(
                    source, lambda: playlist_videos(source, args, seen, run)) or []]
                continue
            video_id = None if args.load_json else source_video_id(source)
            if video_id in seen:
                log(f"Ignored duplicate: {source} (already in a playlist above)")
                continue
            if video_id:
                seen.add(video_id)
            queue.append((source, None))
        for source, playlist in queue:
            archive_video(source, playlist_context(run, playlist, source) if playlist else None)
    except KeyboardInterrupt:
        interrupted = True
        log("Interrupted (Ctrl+C). Run the same command again to continue: "
            "videos with a page are skipped.")
    except NoConnection as error:
        offline = True
        log(f"FAILED: no connection to YouTube ({error}). The run stops here: "
            + ("this video is saved as far as it got, the ones after it are as they were"
               if error.partly else "this video and the ones after it are as they were")
            + ". Run the same command again when the connection is back.")
    index_failed = listing and not index.write_index(args, lists)
    if sum(counts.values()) > 1 or playlists or listing or interrupted or offline:
        log(f"Done: {counts['saved']} saved, {counts['skipped']} skipped (page exists), "
            f"{counts['failed']} failed"
            + (f", {len(without_data)} without saved data" if without_data else "")
            + (f", {counts['left']} left for the next run" if counts["left"] else "")
            + (", stopped: no connection" if offline else "") + "."
            + (f" Videos: {videos['downloaded']} downloaded, {videos['skipped']} skipped, "
               f"{videos['failed']} failed." if args.video is not None else ""))
    if counts["left"]:
        log(take_note(args, counts["left"]))
    if interrupted:
        return 130  # the usual exit code after Ctrl+C
    return 1 if counts["failed"] or videos["failed"] or index_failed else 0


if __name__ == "__main__":
    sys.exit(main())
