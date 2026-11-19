"""The index of a list file (--index FILE, --index-only FILE).

A list file is plain UTF-8 text, close to Markdown: videos (address, id or
a page name with "[VIDEO_ID]"), playlists, video files of the user's own,
an optional title ("# Title"), chapters ("##" to "####"), notes ("> text"),
a title of the user's own for the next video or playlist ("= Title"),
comments ("// text") and settings ("!numbers: no", "!thumbnails: no"). The
whole file is checked before anything is downloaded; a line that is not
understood stops the run with its number.

The index is always built from what lies in the output folder: pages
(found by "[VIDEO_ID].html", read only up to their <title>: the yt2html:...
metadata), video files (found by "[VIDEO_ID]" and a video extension) and
the user's own files, so --index, --index-only and --load-json give the
same index for the same folder. Only the videos of a playlist come from
YouTube, read each time; the index keeps them (a JSON data block, not
shown) for when YouTube cannot be reached.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import struct
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import yt2html as core


# --------------------------------------------------------------------------
# The list file
# --------------------------------------------------------------------------

# Settings ("!key: value", at the top of the file): the allowed values, the
# first one the default.
SETTINGS = {"numbers": ("yes", "no"), "thumbnails": ("yes", "no")}
LEVELS = 3   # chapters: "##" (1) to "####" (3)
HEADING = re.compile(r"(#+)(?:\s+(.*))?")
YOUTUBE_HOSTS = ("www.youtube.", "youtube.", "m.youtube.", "music.youtube.", "youtu.be/")


class ListFileError(ValueError):
    """A list file that cannot be used; the message names the line."""


@dataclass
class Item:
    """A line with a video ("video": a YouTube video id), a playlist
    ("playlist": its id) or a file of the user's own ("file": its name)."""
    kind: str
    ref: str
    line: int
    text: str                      # the line as written
    title: str | None = None       # from a "=" line above it
    notes: list[str] = field(default_factory=list)   # notes under a video or file


@dataclass
class Chapter:
    level: int                     # 1 to 3
    text: str                      # may be empty
    line: int
    children: list = field(default_factory=list)


@dataclass
class ListFile:
    path: Path
    title: str                     # "" without "#" or with an empty "#"
    settings: dict[str, str]       # every key of SETTINGS, with the default if not given
    children: list                 # Item, Chapter and str (a note: one paragraph)

    def items(self, children: list | None = None):
        """Every Item, in the order of the file."""
        for child in self.children if children is None else children:
            if isinstance(child, Item):
                yield child
            elif isinstance(child, Chapter):
                yield from self.items(child.children)


def link_line(text: str) -> tuple[str, str] | None:
    """What a line with a link refers to: ("video", id), ("playlist", id),
    ("file", name), or None. Raises ValueError for an address that is no
    video or playlist. The order: an address; a playlist id; a name with
    "[VIDEO_ID]" (a page, a video file); a video id; a file name with a
    video extension and no folder."""
    if "://" in text or text.lower().startswith(YOUTUBE_HOSTS):
        if "://" not in text:
            text = "https://" + text       # copied from the address bar without it
        video_id = core.video_id_from_url(text)
        if video_id:
            return "video", video_id
        playlist_id = core.playlist_id_from_url(text)
        if playlist_id:
            return "playlist", playlist_id
        if core.is_channel_url(text):
            raise ValueError(f"a channel address ({text}); a list takes videos and playlists")
        raise ValueError(f"not the address of a YouTube video or playlist: {text}")
    playlist_id = core.playlist_id_from_url(text)       # a bare id: no address is left
    if playlist_id:
        return "playlist", playlist_id
    video_id = core.id_in_name(text, playlists=False)
    if video_id:
        return "video", video_id
    if core.VIDEO_ID.fullmatch(text):
        return "video", text
    if ("/" not in text and "\\" not in text
            and Path(text).suffix.lower().lstrip(".") in core.VIDEO_EXTENSIONS):
        return "file", text
    return None


def read_list_file(name: str) -> ListFile:
    """Read and check a list file. Raises ListFileError, naming the line."""
    path = Path(name)
    if path.suffix.lower() in (".html", ".htm"):
        raise ListFileError(f"{name} is a page; give the list file the index is made from "
                            f"(e.g. {path.stem}.txt)")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ListFileError(f"list file '{name}' is not UTF-8 text") from None
    except OSError as error:
        raise ListFileError(f"cannot read list file '{name}': "
                            f"{error.strerror or error}") from None

    def fail(number: int, message: str):
        raise ListFileError(f"{name}, line {number}: {message}")

    title = None                   # (text, line) of the "#" line
    settings = {key: values[0] for key, values in SETTINGS.items()}
    given: set[str] = set()        # the settings written in the file
    root = Chapter(0, "", 0)
    open_chapters = [root]
    header = True                  # before the first chapter, video or playlist
    pending = None                 # (title, line) of a "=" line
    owner = None                   # the video or file the next notes belong to
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        found = None
        if line and not line.startswith(("//", "!", "#", ">", "=")):
            try:
                found = link_line(line)
            except ValueError as error:
                fail(number, str(error))
            if not found:
                fail(number, f"not a video, playlist or video file: {line} "
                             "(comments start with //)")
        if pending and not found:
            fail(pending[1], "a '=' title must stand directly above a video, playlist or "
                             "file" + (" (two '=' lines in a row)" if line.startswith("=") else ""))
        if not line or line.startswith("//"):
            continue
        if line.startswith("!"):                  # "!key: value", any case and spacing
            key, _, value = line[1:].partition(":")
            key, value = key.strip().lower(), value.strip().lower()
            if key not in SETTINGS:
                fail(number, f"unknown setting: {line} (known: "
                             + ", ".join(f"!{k}" for k in SETTINGS) + ")")
            if value not in SETTINGS[key]:
                fail(number, f"{line}: !{key} is one of: " + ", ".join(SETTINGS[key])
                             + f" (e.g. !{key}: {SETTINGS[key][-1]})")
            if not header:
                fail(number, f"{line}: settings go at the top of the file, before the "
                             "first chapter, video or playlist")
            if key in given:
                fail(number, f"!{key} is given twice")
            given.add(key)
            settings[key] = value
            continue
        if line.startswith("#"):
            heading = HEADING.fullmatch(line)
            if not heading:
                fail(number, f"not a title or chapter: {line} (a space goes after the #; "
                             "comments start with //)")
            level = len(heading.group(1)) - 1
            words = (heading.group(2) or "").strip()
            if level == 0:
                if title:
                    fail(number, f"a second title (the first is on line {title[1]}); "
                                 "chapters are ## to ####, comments start with //")
                title = (words, number)
                continue
            if level > LEVELS:
                fail(number, "too deep: chapters go from ## to #### (three levels)")
            while open_chapters[-1].level >= level:
                open_chapters.pop()
            chapter = Chapter(level, words, number)
            open_chapters[-1].children.append(chapter)
            open_chapters.append(chapter)
            header, owner = False, None
            continue
        if line.startswith(">"):
            note = line[1:].strip()
            if note:
                (owner.notes if owner else open_chapters[-1].children).append(note)
            continue
        if line.startswith("="):
            words = line[1:].strip()
            if not words:
                fail(number, "an empty '=' title")
            pending = (words, number)
            continue
        kind, ref = found
        item = Item(kind, ref, number, line, pending[0] if pending else None)
        open_chapters[-1].children.append(item)
        pending, header = None, False
        owner = item if kind != "playlist" else None   # notes after a playlist follow it
    if pending:
        fail(pending[1], "a '=' title must stand directly above a video, playlist or file")
    return ListFile(path, title[0] if title else "", settings, root.children)


def index_path(list_file: str, output_dir: Path) -> Path:
    """seria.txt -> OUTPUT/seria.html (the last extension, if any, dropped)."""
    return output_dir / (Path(list_file).stem + ".html")


# --------------------------------------------------------------------------
# Playlists: read from YouTube, else the list kept in the earlier index
# --------------------------------------------------------------------------

DATA_ID = "yt2html-playlists"
DATA_RE = re.compile(rf'<script type="application/json" id="{DATA_ID}">(.*?)</script>', re.S)


def kept_playlists(path: Path) -> dict[str, dict]:
    """The playlists an earlier index keeps ({} if there is none)."""
    try:
        found = DATA_RE.search(path.read_text(encoding="utf-8"))
        data = json.loads(found.group(1)) if found else {}
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data


def playlist_data(section: dict) -> dict:
    """What the index needs of a playlist read in this run (and keeps)."""
    return {key: section.get(key) for key in
            ("title", "channel", "channel_url", "count", "description")} | {
        "entries": [entry["id"] for entry in section["entries"]],
        "read": dt.datetime.now().strftime("%Y-%m-%dT%H:%M")}


def read_playlists(listing: ListFile, args: argparse.Namespace, run: core.Run,
                   kept: dict[str, dict]) -> tuple[dict[str, dict | None], int]:
    """Each playlist of the list: read from YouTube (only the list of its
    videos; kept in `run` for the download), else as the earlier index kept
    it, else None. Also the number of playlists that could not be read at
    all."""
    found, failed = {}, 0
    for item in listing.items():
        if item.kind != "playlist" or item.ref in found:
            continue
        url = f"{core.YOUTUBE}/playlist?list={item.ref}"
        try:
            found[item.ref] = playlist_data(core.read_playlist(url, args, run))
            continue
        except Exception as error:
            reason = core.failure_reason(error)
        if item.ref in kept:
            found[item.ref] = kept[item.ref]
            when = kept[item.ref].get("read") or ""
            core.log(f"Playlist {item.ref} could not be read ({reason}); using the list kept "
                     f"in the index" + (f" (read {when_text(when)})" if when else "") + ".")
        else:
            found[item.ref] = None
            failed += 1
            core.log(f"FAILED: playlist {url}: {reason}")
    return found, failed


def when_text(moment: str) -> str:
    """2026-11-18T14:05 -> 18.11.2026 14:05."""
    try:
        return dt.datetime.fromisoformat(moment).strftime(f"{core.DATE_FORMAT} %H:%M")
    except ValueError:
        return moment


def videos_to_download(listing: ListFile, run: core.Run) -> list[tuple[str, str | None]]:
    """(watch URL, playlist id or None) of each YouTube video of the list, in
    its order, each once: the video lines and the videos of the playlists
    read in this run."""
    seen, found = set(), []
    for item in listing.items():
        if item.kind == "video":
            ids = [(item.ref, None)]
        elif item.kind == "playlist" and item.ref in run.playlists:
            ids = [(entry["id"], item.ref) for entry in run.playlists[item.ref]["entries"]]
        else:
            continue
        for video_id, playlist_id in ids:
            if video_id not in seen:
                seen.add(video_id)
                found.append((f"{core.YOUTUBE}/watch?v={video_id}", playlist_id))
    return found


# --------------------------------------------------------------------------
# The structure of the index: chapters, rows, notes
# --------------------------------------------------------------------------

@dataclass
class Row:
    kind: str                      # "video", "file" or "playlist" (could not be read)
    ref: str
    line: int
    title: str | None = None       # from "="
    name: str | None = None        # the line, when it is a page or file name
    notes: list[str] = field(default_factory=list)


@dataclass
class Heading:
    level: int
    text: str
    children: list                 # Heading, Row, Notes, PlaylistNotes


@dataclass
class Notes:
    paragraphs: list[str]


@dataclass
class PlaylistNotes:
    playlist_id: str
    data: dict


def item_row(item: Item) -> Row:
    named = item.kind == "file" or ("[" in item.text and "://" not in item.text)
    return Row(item.kind, item.ref, item.line, item.title, item.text if named else None,
               item.notes)


def lone_playlist(children: list) -> Item | None:
    """The playlist that is the only element (not counting notes) here."""
    elements = [c for c in children if not isinstance(c, str)]
    if len(elements) == 1 and isinstance(elements[0], Item) and elements[0].kind == "playlist":
        return elements[0]
    return None


def structure(children: list, level: int, playlists: dict[str, dict | None]) -> list:
    """The index's blocks for one level: a playlist that is the only element
    of its level puts its videos in its place; next to other elements it
    becomes a chapter one level lower, at the lowest level its videos stand
    next to the other elements. Notes in a row are one block."""
    lone = lone_playlist(children)
    blocks: list = []
    for child in children:
        if isinstance(child, str):
            if blocks and isinstance(blocks[-1], Notes):
                blocks[-1].paragraphs.append(child)
            else:
                blocks.append(Notes([child]))
        elif isinstance(child, Chapter):
            inner = lone_playlist(child.children)
            text = child.text
            if not text and inner:
                data = playlists.get(inner.ref)
                text = inner.title or (data or {}).get("title") or ""
            blocks.append(Heading(child.level, text,
                                  structure(child.children, child.level, playlists)))
        elif child.kind != "playlist":
            blocks.append(item_row(child))
        elif playlists.get(child.ref) is None:
            blocks.append(Row("playlist", child.ref, child.line, child.title))
        else:
            data = playlists[child.ref]
            videos = [PlaylistNotes(child.ref, data)] + [
                Row("video", video_id, child.line) for video_id in data["entries"]]
            if child is lone or level >= LEVELS:
                blocks.extend(videos)
            else:
                blocks.append(Heading(level + 1, child.title or data.get("title") or "", videos))
    return blocks


def list_title(listing: ListFile, playlists: dict[str, dict | None]) -> str:
    """The title of the index: the "#" line; else, for the only playlist of
    the file, its "=" title or its title on YouTube; else the file's name
    without its extension."""
    if listing.title:
        return listing.title
    found = [item for item in listing.items() if item.kind == "playlist"]
    if len(found) == 1:
        data = playlists.get(found[0].ref) or {}
        title = found[0].title or data.get("title")
        if title:
            return title
    return listing.path.stem


# --------------------------------------------------------------------------
# Durations of the user's own files, from their headers (no ffmpeg)
# --------------------------------------------------------------------------

def mp4_duration(file) -> float | None:
    """Seconds from the "mvhd" box of an MP4/M4V file (inside "moov", which
    may stand before or after the media data)."""
    def boxes(end: int | None):
        while end is None or file.tell() < end:
            start = file.tell()
            head = file.read(8)
            if len(head) < 8:
                return
            size, kind = struct.unpack(">I4s", head)
            if size == 1:
                size = struct.unpack(">Q", file.read(8))[0]
            elif size == 0:
                size = None        # to the end of the file
            yield kind, start, size
            if size is None or size < 8:
                return
            file.seek(start + size)

    for kind, start, size in boxes(None):
        if kind != b"moov":
            continue
        for inner, inner_start, inner_size in boxes(start + size if size else None):
            if inner != b"mvhd":
                continue
            version = file.read(4)[0]
            if version == 1:
                file.read(16)
                scale, length = struct.unpack(">IQ", file.read(12))
            else:
                file.read(8)
                scale, length = struct.unpack(">II", file.read(8))
            return length / scale if scale else None   # (0: a broken file)
        return None
    return None


def ebml_number(file, keep_marker: bool) -> int:
    """A variable-length number of EBML: an element id (with its marker
    bit) or a size (without it; an "unknown" size, all ones, stays a huge
    number, which only the Segment may have here, and it is not skipped)."""
    first = file.read(1)
    if not first:
        raise EOFError
    byte = first[0]
    length = next((n for n in range(8) if byte & (0x80 >> n)), None)
    if length is None:
        raise ValueError("not EBML")
    value = byte if keep_marker else byte & (0xFF >> (length + 1))
    for b in file.read(length):
        value = (value << 8) | b
    return value


SEGMENT, INFO, CLUSTER = 0x18538067, 0x1549A966, 0x1F43B675
TIMESTAMP_SCALE, EBML_DURATION = 0x2AD7B1, 0x4489


def ebml_duration(file) -> float | None:
    """Seconds from the Duration in the Info element of a WebM/MKV file
    (near its start, before the first Cluster)."""
    try:
        while True:
            element, size = ebml_number(file, True), ebml_number(file, False)
            if element == SEGMENT:
                break              # look inside
            file.seek(size, 1)
        while True:
            element, size = ebml_number(file, True), ebml_number(file, False)
            if element == CLUSTER:
                return None
            if element != INFO:
                file.seek(size, 1)
                continue
            end, scale, length = file.tell() + size, 1_000_000, None
            while file.tell() < end:
                element, size = ebml_number(file, True), ebml_number(file, False)
                data = file.read(size)
                if element == TIMESTAMP_SCALE:
                    scale = int.from_bytes(data, "big")
                elif element == EBML_DURATION and size in (4, 8):
                    length = struct.unpack(">f" if size == 4 else ">d", data)[0]
            return length * scale / 1e9 if length is not None else None
    except (EOFError, ValueError):
        return None


def file_duration(path: Path) -> float | None:
    """Seconds of a video file, read from its header; None if not found."""
    try:
        with open(path, "rb") as file:
            if path.suffix.lower() in (".webm", ".mkv"):
                return ebml_duration(file)
            return mp4_duration(file)
    except (OSError, struct.error, IndexError):
        return None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

URL_RE = re.compile(r"\bhttps?://[^\s<>\"]+")
BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITALIC_RE = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])"
                       r"|(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")


def links_only(text: str, emphasis: bool = False) -> str:
    """Escaped text with http(s) addresses as links (punctuation at the end
    stays outside); with `emphasis`, **bold** and *italic* / _italic_ too.
    Anything else stays as written, tags included."""
    def plain(part: str) -> str:
        part = core.esc(part)
        if emphasis:
            part = BOLD_RE.sub(r"<strong>\1</strong>", part)
            part = ITALIC_RE.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", part)
        return part

    out, position = [], 0
    for found in URL_RE.finditer(text):
        url, tail = core.trim_url(found.group(0))
        out.append(plain(text[position:found.start()]))
        out.append(core.link(url, url))
        position = found.start() + len(url)
    out.append(plain(text[position:]))
    return "".join(out)


PLAY_ICON = ('<svg viewBox="0 0 10 12" aria-hidden="true"><path d="M1.6 1v10l8-5z"/></svg>')


class Builder:
    """Renders the blocks; reads the folder once per video."""

    def __init__(self, output_dir: Path, settings: dict[str, str]):
        self.output_dir = output_dir
        self.numbers = settings["numbers"] == "yes"
        self.thumbnails = settings["thumbnails"] == "yes"
        self.notes: list[tuple[int, str, str]] = []   # (line, note, advice) for the console
        self.old_pages: set[str] = set()
        self.missing: dict[int, list[str]] = {}   # line -> videos not downloaded

    # -- one row ---------------------------------------------------------------

    def row(self, row: Row, depth: int, number: int) -> str:
        if row.kind == "file":
            parts = self.own_file(row)
        elif row.kind == "playlist":
            parts = self.unread_playlist(row)
        else:
            parts = self.video(row)
        thumb, title, line, missing = parts
        notes = (f'<div class="note">{"".join(f"<p>{links_only(p, True)}</p>" for p in row.notes)}'
                 f'</div>' if row.notes else "")
        return (f'<div class="row d{depth}{" missing" if missing else ""}">'
                + (f'<span class="num">{number}</span>' if self.numbers else "")
                + f'{thumb}<div class="info">{title}'
                f'<div class="line">{" · ".join(p for p in line if p)}</div>{notes}</div></div>')

    def thumb(self, duration: float | None, cover: str | None = None, label: str = "",
              file: Path | None = None) -> str:
        """The image (or, with !thumbnails: no, the round play button); with
        a video file it plays the file in the player (data-file: its address
        relative to the index)."""
        play = f' data-file="{core.esc(urllib.parse.quote(file.name))}"' if file else ""
        if not self.thumbnails:
            return f'<span class="play{"" if file else " off"}"{play}>{PLAY_ICON}</span>'
        time = (f'<span class="time">{core.format_duration(duration)}</span>'
                if duration is not None else "")
        classes = "thumb" + ("" if file else " off")
        if cover:
            return (f'<span class="{classes}"{play}><img src="{core.esc(cover)}" alt="">{time}'
                    f'</span>')
        if label:
            return f'<span class="{classes} format"{play}>{core.esc(label)}{time}</span>'
        return f'<span class="{classes} empty"></span>'

    def time_in_line(self, duration: float | None) -> str:
        """The duration at the start of the line, when it is not on the image."""
        if self.thumbnails or duration is None:
            return ""
        return core.format_duration(duration)

    def video(self, row: Row) -> tuple[str, str, list[str], bool]:
        video_id = row.ref
        page = core.existing_page(self.output_dir, video_id)
        meta = core.read_page_metadata(page) if page else {}
        if page and "yt2html:metadata" not in meta:
            self.old_pages.add(page.name)
        file = core.first_of(core.video_files(self.output_dir, video_id), self.output_dir,
                             video_id, "video files")
        original = meta.get("yt2html:title")
        title = (row.title or original or name_without_id(row.name, video_id)
                 or (name_without_id(page.name, video_id) if page else None)
                 or (name_without_id(file.name, video_id) if file else None))
        shown = core.esc(title) if title else f'<span class="id">{core.esc(video_id)}</span>'
        url = f"{core.YOUTUBE}/watch?v={video_id}"
        hint = f' title="{core.esc(original)}"' if original else ""
        youtube = (f'<span class="miss"{hint}>no longer on YouTube</span>'
                   if "yt2html:unavailable" in meta else
                   f'<a href="{core.esc(url)}" target="_blank" rel="noopener noreferrer"{hint}>'
                   f'YouTube</a>')
        if page:
            name = (f'<a class="name" href="{core.esc(urllib.parse.quote(page.name))}" '
                    f'target="_blank" rel="noopener noreferrer">{shown}</a>')
            try:
                duration = float(meta["yt2html:duration"])
            except (KeyError, ValueError):
                duration = None
            language = (meta.get("yt2html:language") or "").lower()
            if file:
                kind = format_label(file)
                return (self.thumb(duration, meta.get("yt2html:cover"), kind, file),
                        name, [self.time_in_line(duration), language,
                               f'<span title="{core.esc(file.name)}">{kind}</span>', youtube],
                        False)
            return (self.thumb(duration, meta.get("yt2html:cover")), name,
                    [self.time_in_line(duration), language,
                     '<span class="miss">only the page downloaded</span>', youtube], True)
        name = f'<span class="name">{shown}</span>'
        if file:
            duration = file_duration(file)
            kind = format_label(file)
            self.notes.append((row.line, f"line {row.line}: {video_id} has a video file but "
                                         f"no page ({file.name})", " (--index makes it)"))
            return (self.thumb(duration, label=kind, file=file), name,
                    [self.time_in_line(duration),
                     f'<span title="{core.esc(file.name)}">{kind}</span>', youtube], False)
        self.missing.setdefault(row.line, []).append(video_id)
        youtube = f'<a href="{core.esc(url)}" target="_blank" rel="noopener noreferrer">YouTube</a>'
        return (self.thumb(None), name,
                ['<span class="miss">not downloaded</span>', youtube], True)

    def own_file(self, row: Row) -> tuple[str, str, list[str], bool]:
        path = self.output_dir / row.ref
        title = row.title or Path(row.ref).stem
        name = f'<span class="name">{core.esc(title)}</span>'
        if not path.is_file():
            self.notes.append((row.line, f"line {row.line}: file not found: {row.ref} (looked "
                                         f"for in {self.output_dir}; check the name)", ""))
            return (self.thumb(None), name,
                    [f'<span class="miss" title="{core.esc(row.ref)}">file not found</span>'], True)
        duration = file_duration(path)
        kind = format_label(path)
        return (self.thumb(duration, label=kind, file=path), name,
                [self.time_in_line(duration), f'<span title="{core.esc(path.name)}">{kind}</span>'],
                False)

    def unread_playlist(self, row: Row) -> tuple[str, str, list[str], bool]:
        url = f"{core.YOUTUBE}/playlist?list={row.ref}"
        shown = core.esc(row.title) if row.title else f'<span class="id">{core.esc(row.ref)}</span>'
        return (self.thumb(None), f'<span class="name">{shown}</span>',
                ['<span class="miss">playlist could not be read</span>',
                 f'<a href="{core.esc(url)}" target="_blank" rel="noopener noreferrer">YouTube</a>'],
                True)

    # -- blocks ----------------------------------------------------------------

    def blocks(self, blocks: list, depth: int) -> list[str]:
        out, number = [], 0
        for block in blocks:
            if isinstance(block, Notes):
                out.append(f'<div class="note d{depth}">'
                           + "".join(f"<p>{links_only(p, True)}</p>" for p in block.paragraphs)
                           + "</div>")
            elif isinstance(block, PlaylistNotes):
                out.append(playlist_notes(block, depth))
            elif isinstance(block, Heading):
                number += 1
                tag = f"h{block.level + 1}"
                out.append(f'<{tag} class="chapter d{block.level - 1}">'
                           + (f'<span class="num">{number}</span>' if self.numbers else "")
                           + f'<span class="text">{core.esc(block.text)}</span></{tag}>')
                out.extend(self.blocks(block.children, block.level))
            else:
                number += 1
                out.append(self.row(block, depth, number))
        return out

    def console_notes(self, advice: bool) -> list[str]:
        """What the console gets after the index: rows without a page or
        file, pages of older versions."""
        # the advice names --index, so only after --index-only
        lines = [(line, text + (tip if advice else "")) for line, text, tip in self.notes]
        for line, videos in self.missing.items():
            what = videos[0] if len(videos) == 1 else core.plural(len(videos), "video")
            lines.append((line, f"line {line}: {what} not downloaded"
                          + (" (--index downloads it)" if advice and len(videos) == 1 else
                             " (--index downloads them)" if advice else "")))
        lines = [note for _, note in sorted(lines, key=lambda pair: pair[0])]
        lines += [f"Made by an older version, without the data the index needs (make it "
                  f"again): {name}" for name in sorted(self.old_pages)]
        return lines


def name_without_id(name: str | None, video_id: str) -> str | None:
    """'Title [ID].html' -> 'Title'; None without a name."""
    if not name:
        return None
    return name.split(f"[{video_id}]")[0].strip() or None


def format_label(path: Path) -> str:
    return path.suffix.lstrip(".").upper()


def playlist_notes(block: PlaylistNotes, depth: int) -> str:
    """The notes the program adds to a playlist, as on its page on YouTube:
    title · Playlist · channel · count; then its description, as written."""
    data = block.data
    url = f"{core.YOUTUBE}/playlist?list={block.playlist_id}"
    channel = data.get("channel")
    first = [core.link(url, data.get("title") or block.playlist_id), "Playlist",
             (core.link(data["channel_url"], channel) if channel and data.get("channel_url")
              else core.esc(channel or "")),
             core.esc(f'{core.format_int(data["count"])} video{"" if data["count"] == 1 else "s"}')
             if data.get("count") is not None else ""]
    description = data.get("description") or ""
    return (f'<div class="note d{depth} playlist-note"><p>{" · ".join(p for p in first if p)}</p>'
            + (f'<p class="about">{links_only(description)}</p>' if description else "")
            + "</div>")


INDEX_CSS = """
/* Index of a list file: rows with a small thumbnail, chapters indented */
.index{--indent:24px}
.index .chapter{display:flex;gap:8px;font-weight:700;margin:20px 0 6px;overflow-wrap:anywhere}
.index h2.chapter{font-size:17px;line-height:24px}
.index h3.chapter{font-size:16px;line-height:22px}
.index h4.chapter{font-size:15px;line-height:21px}
.index .num{flex:none;min-width:2ch;text-align:right;color:var(--muted);
  font-variant-numeric:tabular-nums}
.index .d1{margin-left:var(--indent)}
.index .d2{margin-left:calc(2 * var(--indent))}
.index .d3{margin-left:calc(3 * var(--indent))}
.index .row{display:flex;gap:12px;align-items:flex-start;padding:6px 8px;margin-right:-8px;
  border-radius:8px}
.index .row .num{line-height:20px;font-weight:600}
.index .thumb{position:relative;display:flex;flex:none;width:128px;height:72px;
  border-radius:8px;overflow:hidden;background:var(--chip);align-items:center;
  justify-content:center;color:var(--muted);font-size:12px;font-weight:600}
.index .thumb img{width:100%;height:100%;object-fit:cover}
.index .thumb.empty,.index .thumb.off,.index .play.off{opacity:.45}
.index .thumb .time{position:absolute;right:4px;bottom:4px;padding:1px 4px;border-radius:4px;
  background:var(--badge-bg);color:var(--badge-fg);font-size:12px;line-height:16px;
  font-weight:600;font-variant-numeric:tabular-nums}
.index .play{display:flex;flex:none;width:28px;height:28px;border-radius:50%;
  background:var(--chip);color:var(--text);align-items:center;justify-content:center}
.index .play svg{width:10px;height:12px;margin-left:.6px;fill:currentColor}
.index .info{min-width:0;flex:1}
.index .name{font-weight:600;overflow-wrap:anywhere}
.index a.name{color:var(--text)}
.index .name .id{font-family:ui-monospace,Consolas,"Cascadia Mono",monospace;font-size:13px}
.index .line{color:var(--muted);font-size:12px;line-height:18px;margin-top:2px}
.index .line [title]{cursor:help}
.index .miss{font-style:italic}
.index .note{background:var(--panel);border-radius:8px;padding:8px 12px;margin-top:6px;
  margin-bottom:6px;
  font-size:13px;line-height:18px;overflow-wrap:anywhere}
.index .row .note{margin-bottom:0}
.index .note p{margin:0 0 6px}
.index .note p:last-child{margin-bottom:0}
.index .note .about{white-space:pre-wrap}
@media (max-width:600px){
  .index{--indent:12px}
  .index .thumb{width:96px;height:54px}
}
/* The player on the left (the page player's look, shown by the script):
   one row of eight equal buttons that wraps in a narrow panel, the speeds,
   the title of the file playing; greyed until the first play */
.player.ix-player .ix-controls button{width:50px}
.player.ix-player button[data-do=autoplay][aria-pressed=true]{background:var(--accent);
  color:var(--on-accent)}
.ix-player .seek input:disabled{opacity:.45}
.ix-player .ix-screen{position:relative}
.ix-player .ix-failed{position:absolute;inset:0;display:flex;flex-direction:column;gap:4px;
  align-items:center;justify-content:center;padding:16px;color:var(--muted);text-align:center;
  overflow-wrap:anywhere}
.ix-player .ix-failed[hidden]{display:none}
.ix-player .ix-playing{font-size:16px;line-height:22px;font-weight:600;overflow-wrap:anywhere}
.player-open .index [data-file]{cursor:pointer}
.player-open .index [data-file]:hover{filter:var(--hover)}
.index .row.now .name{color:var(--accent-text)}
.index .row.now .thumb{box-shadow:0 0 0 2px var(--accent)}
.index .row.now .play{background:var(--accent);color:var(--on-accent)}
.index .row.failed .thumb,.index .row.failed .play{opacity:.45}
.player-open .index .row.failed [data-file]{cursor:default;filter:none}
"""

SPEEDS = core.PLAYER_SPEEDS


def render_player() -> str:
    """The player panel: hidden without the script (the index then is a plain
    list), always shown with it. Buttons that show symbols have an
    aria-label: the name screen readers and voice control use."""
    speeds = "".join(f'<button type="button" data-speed="{v}" aria-pressed="{str(v == "1").lower()}" '
                     f'aria-label="Speed {v}">{v}×</button>' for v in SPEEDS)
    buttons = [
        ("previous", "Previous file (Shift+P)", "Previous file", "|◀◀"),
        ("start", "To the start (Ctrl+←)", "To the start", "|◀"),
        ("back", "10 s back (←)", "10 seconds back", "−10 s"),
        ("toggle", "Play / pause (space)", "Play", "▶"),
        ("forward", "10 s forward (→)", "10 seconds forward", "+10 s"),
        ("end", "To the end (Ctrl+→)", "To the end", "▶|"),
        ("next", "Next file (Shift+N)", "Next file", "▶▶|"),
    ]
    main = ' class="main"'   # Play in the accent colour, as on a video's page
    return (
        '<aside class="player ix-player" aria-label="Video player">'
        '<div class="ix-screen"><video preload="none"></video>'
        '<div class="ix-failed" hidden><div>This file could not be played.</div>'
        '<div class="ix-failed-title"></div></div></div>'
        '<div class="seek"><span class="now">0:00</span>'
        '<input type="range" min="0" max="0" step="0.1" value="0" aria-label="Position">'
        '<span class="total">0:00</span></div>'
        '<div class="buttons ix-controls">'
        + "".join(f'<button type="button" data-do="{do}"{main if do == "toggle" else ""} '
                  f'title="{core.esc(title)}" aria-label="{label}">{core.esc(text)}</button>'
                  for do, title, label, text in buttons)
        + '<button type="button" data-do="autoplay" aria-pressed="false" '
        'title="Autoplay: play the next file when this one ends" aria-label="Autoplay">A</button>'
        '</div>'
        f'<div class="buttons speed">{speeds}</div>'
        '<div class="ix-playing"></div>'
        '</aside>')


# The only script of the index: the player. The thumbnails (or round
# buttons) of rows with a video file play it; the panel plays one file at a
# time. Without the script the index is a plain list.
PLAYER_JS = r"""
(function () {
  var body = document.body, panel = document.querySelector('.ix-player');
  if (!panel) return;
  function all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }
  var video = panel.querySelector('video'), seek = panel.querySelector('.seek input');
  var failedBox = panel.querySelector('.ix-failed'), playing = panel.querySelector('.ix-playing');
  var buttons = {};
  all('[data-do]', panel).forEach(function (b) { buttons[b.dataset.do] = b; });
  var speeds = all('[data-speed]', panel);
  // The files, in the order of the list; current: the one in the player
  // (-1 before the first play); failed: files that could not be played,
  // skipped until the page is loaded again (a file put right gets a new chance).
  var items = all('.index [data-file]'), current = -1, failed = [];
  // What previous / next carry over (user's decision 19.11.2026): playing
  // goes on playing, stopped (paused, at the start, at the end) waits at the
  // start of the other file. A file that failed has no state of its own: the
  // one from before it stays. A thumbnail and Autoplay always play.
  var playOn = true;
  function clock(s) {
    s = Math.floor(s || 0);
    var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = String(s % 60);
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + sec.padStart(2, '0');
  }
  function rowOf(i) { return items[i].closest('.row'); }
  function titleOf(i) {
    var name = rowOf(i).querySelector('.name');
    return name ? name.textContent : '';
  }
  function isFailed(i) { return failed.indexOf(i) >= 0; }
  function playableFrom(i, step) {  // the next file to play before or after i, or -1
    for (i += step; i >= 0 && i < items.length; i += step) if (!isFailed(i)) return i;
    return -1;
  }
  function setState() {  // greyed: everything before the first play, the controls of a failed file
    var started = current >= 0, usable = started && !isFailed(current);
    ['start', 'back', 'toggle', 'forward', 'end'].forEach(function (k) {
      buttons[k].disabled = !usable;
    });
    seek.disabled = !usable;
    buttons.previous.disabled = !started || playableFrom(current, -1) < 0;
    buttons.next.disabled = !started || playableFrom(current, 1) < 0;
    buttons.autoplay.disabled = !started;
    speeds.forEach(function (b) { b.disabled = !started; });
  }
  function showTime() {
    seek.value = video.currentTime || 0;
    panel.querySelector('.now').textContent = clock(video.currentTime);
  }
  function load(i, play) {
    if (current >= 0) rowOf(current).classList.remove('now');
    playOn = play;
    current = i;
    rowOf(i).classList.add('now');
    playing.textContent = titleOf(i);
    failedBox.hidden = true;
    seek.max = 0;
    panel.querySelector('.total').textContent = clock(0);
    video.preload = 'metadata';          // waiting: the duration and the first frame
    video.src = items[i].dataset.file;   // keeps the speed (defaultPlaybackRate)
    showTime();
    setState();
    if (play) video.play().catch(function () {});
  }
  function fail(i) {
    failed.push(i);
    rowOf(i).classList.add('failed');
    failedBox.querySelector('.ix-failed-title').textContent = titleOf(i);
    failedBox.hidden = false;
    setState();
  }
  video.addEventListener('error', function () {
    if (current >= 0 && video.getAttribute('src')) fail(current);
  });
  // The visible part of the window: below the panel when it stands above
  // the list (narrow windows).
  function edges() {
    var top = getComputedStyle(panel).position === 'sticky'
      ? Math.max(0, panel.getBoundingClientRect().bottom) : 0;
    return {top: top, bottom: window.innerHeight};
  }
  function visible(row) {
    var r = row.getBoundingClientRect(), e = edges();
    return r.bottom > e.top && r.top < e.bottom;
  }
  // A row that would be out of sight comes into view, with a margin, by the
  // shortest scroll (at the ends of the list the margin goes).
  function reveal(row) {
    var r = row.getBoundingClientRect(), e = edges(), by = 0, MARGIN = 48;
    if (r.top < e.top) by = r.top - e.top - MARGIN;
    else if (r.bottom > e.bottom) by = r.bottom - e.bottom + MARGIN;
    if (!by) return;
    var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollBy({top: by, behavior: still ? 'auto' : 'smooth'});
  }
  function go(step) {
    var i = playableFrom(current, step);
    if (i < 0) return;
    load(i, isFailed(current) ? playOn : !video.paused);
    reveal(rowOf(i));
  }
  // Autoplay: the next file that is not known to fail; scrolls only if the
  // row that ended was in sight (it never pulls the reader away from the list).
  video.addEventListener('ended', function () {
    if (buttons.autoplay.getAttribute('aria-pressed') !== 'true') return;
    var i = playableFrom(current, 1);
    if (i < 0) return;
    var follow = visible(rowOf(current));
    load(i, true);
    if (follow) reveal(rowOf(i));
  });
  function jump(t) { video.currentTime = Math.max(0, Math.min(video.duration || 0, t)); }
  var actions = {
    previous: function () { go(-1); },
    start: function () { video.pause(); jump(0); },  // always stopped at the start
    back: function () { jump(video.currentTime - 10); },
    toggle: function () { if (video.paused) video.play(); else video.pause(); },
    forward: function () { jump(video.currentTime + 10); },
    end: function () { jump(video.duration); },
    next: function () { go(1); },
    autoplay: function () {
      var on = buttons.autoplay.getAttribute('aria-pressed') !== 'true';
      buttons.autoplay.setAttribute('aria-pressed', String(on));
    }
  };
  all('[data-do]', panel).forEach(function (button) {
    button.addEventListener('click', function () { actions[button.dataset.do](); button.blur(); });
  });
  speeds.forEach(function (button) {
    button.addEventListener('click', function () {
      video.defaultPlaybackRate = video.playbackRate = Number(button.dataset.speed);
      speeds.forEach(function (other) { other.setAttribute('aria-pressed', String(other === button)); });
      button.blur();
    });
  });
  // A thumbnail plays its file; the file in the player, or one that failed,
  // does nothing (the panel controls it).
  items.forEach(function (item, i) {
    item.setAttribute('role', 'button');
    item.tabIndex = 0;
    item.setAttribute('aria-label', 'Play: ' + titleOf(i));
    function choose() { if (i !== current && !isFailed(i)) load(i, true); }
    item.addEventListener('click', choose);
    item.addEventListener('keydown', function (e) {  // other keys work as anywhere else
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      e.stopPropagation();
      choose();
    });
  });
  seek.addEventListener('input', function () { jump(Number(seek.value)); });
  video.addEventListener('loadedmetadata', function () {
    seek.max = video.duration;
    panel.querySelector('.total').textContent = clock(video.duration);
  });
  video.addEventListener('timeupdate', showTime);
  video.addEventListener('play', function () {
    buttons.toggle.textContent = '❚❚'; buttons.toggle.setAttribute('aria-label', 'Pause');
  });
  video.addEventListener('pause', function () {
    buttons.toggle.textContent = '▶'; buttons.toggle.setAttribute('aria-label', 'Play');
  });
  video.addEventListener('enterpictureinpicture', function () { body.classList.add('pip'); });
  video.addEventListener('leavepictureinpicture', function () { body.classList.remove('pip'); });
  if ('mediaSession' in navigator) {  // the browser's own controls (picture-in-picture, media keys)
    [['seekbackward', 'back'], ['seekforward', 'forward'],
     ['previoustrack', 'previous'], ['nexttrack', 'next']].forEach(function (pair) {
      try {
        navigator.mediaSession.setActionHandler(pair[0], function () {
          if (!buttons[pair[1]].disabled) actions[pair[1]]();
        });
      } catch (e) { /* not supported */ }
    });
  }
  // Keys as on a video's page (space, arrows, Ctrl+arrows), Shift+P and
  // Shift+N the previous and next file; a greyed button's key does nothing.
  document.addEventListener('keydown', function (e) {
    if (e.altKey || e.metaKey
        || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(e.target.tagName)) return;
    var key = (e.ctrlKey ? 'Ctrl+' : '') + (e.shiftKey ? 'Shift+' : '')
      + (e.key.length === 1 ? e.key.toUpperCase() : e.key);
    var action = {' ': 'toggle', 'ArrowLeft': 'back', 'ArrowRight': 'forward',
                  'Ctrl+ArrowLeft': 'start', 'Ctrl+ArrowRight': 'end',
                  'Shift+P': 'previous', 'Shift+N': 'next'}[key];
    if (action && !buttons[action].disabled) { e.preventDefault(); actions[action](); }
  });
  body.classList.add('player-open');
  setState();
})();
"""


def render_index(listing: ListFile, playlists: dict[str, dict | None], output_dir: Path,
                 theme: str = "auto", today: dt.date | None = None) -> tuple[str, Builder]:
    """The index page, and the builder with its notes for the console."""
    today = today or dt.date.today()
    builder = Builder(output_dir, listing.settings)
    body = builder.blocks(structure(listing.children, 0, playlists), 0)
    title = list_title(listing, playlists)
    kept = {pid: data for pid, data in playlists.items() if data}
    # "<" escaped, so the data can never close its <script> element
    data = json.dumps(kept, ensure_ascii=False, indent=1).replace("<", "\\u003c")
    page = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta name="generator" content="yt2html {core.__version__} (yt-video-archiver)">',
        '<meta name="yt2html:index" content="list">',
        f"<title>{core.esc(title)}</title>",
        f'<meta name="color-scheme" content="{core.COLOR_SCHEMES.get(theme, "dark")}">',
        f"<style>{core.CSS}\n{INDEX_CSS}\n{core.theme_css(theme)}\n</style>",
        "</head>",
        "<body>",
        render_player(),
        '<main class="page index">',
        f'<h1 class="title">{core.esc(title)}</h1>',
        *body,
        f'<footer class="footer">Index made with yt2html on {today.strftime(core.DATE_FORMAT)}.'
        f'</footer>',
        "</main>",
        f'<script type="application/json" id="{DATA_ID}">{data}</script>',
        f"<script>{PLAYER_JS}</script>",
        "</body>",
        "</html>",
        "",
    ])
    return page, builder


def start(args: argparse.Namespace, run: core.Run) -> tuple[dict[str, dict | None], int]:
    """Before any download: the playlists of the list (see read_playlists())."""
    path = index_path(args.index or args.index_only, Path(args.output_dir))
    return read_playlists(args.list, args, run, kept_playlists(path))


def write_index(args: argparse.Namespace, playlists: dict[str, dict | None]) -> bool:
    """Write the index of the list file from the folder; False if it failed."""
    output_dir = Path(args.output_dir)
    path = index_path(args.index or args.index_only, output_dir)
    try:
        page, builder = render_index(args.list, playlists, output_dir, args.theme)
        output_dir.mkdir(parents=True, exist_ok=True)
        core.write_atomically(path, page)
    except OSError as error:
        core.log(f"FAILED: index {path}: {error.strerror or error}")
        return False
    core.log(f"Saved index: {path}")
    for note in builder.console_notes(advice=not args.index):
        core.log(f"  {note}")
    return True
