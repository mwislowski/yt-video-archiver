"""yt2html - archive a YouTube video page as a single self-contained HTML file.

The page contains the video's metadata (thumbnail, title, channel, counts,
dates, full description) and all comments with their reply threads, sorted
oldest first. Images (thumbnail, avatars) are embedded as data URIs, so the
result is one file that works offline, like a page saved with SingleFile.
The video itself is not downloaded.

Usage:
    uv run yt2html.py URL [URL ...] [options]
    uv run yt2html.py --help
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import re
import sys
import urllib.request
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Default folder for generated pages (relative to the current directory).
DEFAULT_OUTPUT_DIR = "output"

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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

YOUTUBE = "https://www.youtube.com"


# --------------------------------------------------------------------------
# Fetching data
# --------------------------------------------------------------------------

def fetch_info(url: str, args: argparse.Namespace) -> dict:
    """Extract metadata and comments with yt-dlp (no video download)."""
    max_comments = "all" if args.max_comments is None else str(args.max_comments)
    opts = {
        "skip_download": True,
        "noplaylist": True,
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
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)


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


def fetch_avatars(urls: set[str]) -> dict[str, str]:
    """Download avatars in parallel; returns {original_url: data_uri}."""
    urls = sorted(u for u in urls if u)
    if not urls:
        return {}
    log(f"Downloading {len(urls)} avatars...")
    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
        results = pool.map(lambda u: fetch_image(resize_avatar_url(u)), urls)
        avatars = {u: d for u, d in zip(urls, results) if d}
    missing = len(urls) - len(avatars)
    if missing:
        log(f"  {missing} avatars could not be downloaded (letter placeholders used).")
    return avatars


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
    return (f'<a href="{esc(href)}" target="_blank" '
            f'rel="noopener noreferrer">{esc(text)}</a>')


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
  --line:#3f3f3f; --link:#3ea6ff; --chip:#3a3a3a;
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:Roboto,"Segoe UI",Arial,sans-serif;font-size:14px;line-height:20px}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
.page{max-width:1280px;margin:0 auto;padding:24px}
.thumbnail{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;
  border-radius:12px;background:#000}
.thumbnail.missing{display:flex;align-items:center;justify-content:center;color:var(--muted)}
h1.title{font-size:20px;line-height:28px;font-weight:700;margin:12px 0 0;
  overflow-wrap:anywhere}
h1.title a{color:var(--text)}
.channel{display:flex;align-items:center;gap:12px;margin-top:12px}
.channel .name{font-size:16px;font-weight:500;line-height:22px}
.channel .name a{color:var(--text)}
.channel .subs{color:var(--muted);font-size:12px;line-height:18px}
.badge{color:var(--muted);font-size:12px;margin-left:4px}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.fact{background:var(--panel);border-radius:12px;padding:10px 16px;min-width:120px}
.fact .value{font-size:16px;font-weight:700;line-height:22px;white-space:nowrap}
.fact .label{color:var(--muted);font-size:12px;line-height:18px}
.description{background:var(--panel);border-radius:12px;padding:12px;margin-top:12px;
  white-space:pre-wrap;overflow-wrap:anywhere}
.description.empty{color:var(--muted)}
.comments{margin-top:24px}
.comments h2{font-size:20px;line-height:28px;font-weight:700;margin:0}
.comments .note{color:var(--muted);font-size:12px;margin:4px 0 16px}
.comment{display:flex;gap:16px;margin-top:16px}
.comment .body{min-width:0;flex:1}
.comment .head{font-size:13px;line-height:18px}
.comment .author{font-weight:500}
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
@media (max-width:600px){
  .page{padding:12px}
  .replies{margin-left:20px}
  .replies .replies{margin-left:12px}
}
"""


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


def fact(label: str, value: str) -> str:
    return (f'<div class="fact"><div class="value">{esc(value)}</div>'
            f'<div class="label">{esc(label)}</div></div>')


def render_page(info: dict, thumbnail: str | None, avatar_uris: dict[str, str],
                archived: dt.date) -> str:
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
                + fact("Downloaded", archived.strftime(DATE_FORMAT))
                + fact("Duration", format_duration(duration))
                + fact("Published", published_date(info))
                + fact("Views", format_int(info.get("view_count")))
                + fact("Likes", format_int(info.get("like_count")))
                + fact("Comments", format_int(info.get("comment_count")
                                              if info.get("comment_count") is not None
                                              else (len(comments) or None)))
                + "</div>")

    description = info.get("description") or ""
    if description.strip():
        body.append(f'<section class="description">{linkify(description, video_id, duration)}</section>')
    else:
        body.append('<section class="description empty">No description.</section>')

    if "comments" in info:
        roots, children = build_comment_tree(comments)
        replies = len(comments) - len(roots)
        body.append('<section class="comments">')
        expected = info.get("comment_count")
        shown = (f" (YouTube shows {format_int(expected)})"
                 if expected is not None and expected != len(comments) else "")
        body.append("<h2>Comments</h2>")
        body.append(f'<div class="note">{format_int(len(comments))} downloaded{shown}: '
                    f'{format_int(len(roots))} threads, {format_int(replies)} replies · '
                    f'oldest first · comment dates are relative to '
                    f'{archived.strftime(DATE_FORMAT)}</div>')
        if info.get("comments") is None:
            body.append('<div class="note">Comments are turned off or unavailable.</div>')
        elif not comments:
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
        # The body is already rendered here, so every avatar class is known.
        f"<style>{CSS}\n{avatars.css()}\n</style>",
        "</head>",
        "<body>",
        *body,
        "</body>",
        "</html>",
        "",
    ])


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def output_path(info: dict, output_dir: Path) -> Path:
    """File name built by yt-dlp's own sanitizer, so it matches the video file."""
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        name = ydl.prepare_filename({**info, "ext": "html"}, outtmpl=OUTPUT_TEMPLATE)
    return output_dir / Path(name).name


def archive(source: str, args: argparse.Namespace) -> Path:
    if args.from_json:
        log(f"Reading {source}")
        info = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        log(f"Fetching {source}")
        info = fetch_info(source, args)

    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError("this is a playlist or channel URL, not a single video")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_path(info, output_dir)

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
        avatar_uris = fetch_avatars({c.get("author_thumbnail")
                                     for c in info.get("comments") or []})

    page = render_page(info, thumbnail, avatar_uris, dt.date.today())
    target.write_text(page, encoding="utf-8")
    size_kb = target.stat().st_size / 1024
    log(f"Saved page: {target} ({size_kb:,.0f} KB, "
        f"{len(info.get('comments') or [])} comments)")
    return target


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt2html",
        description="Save a YouTube video page (metadata, description, all comments) "
                    "as a single offline HTML file.",
    )
    parser.add_argument("sources", nargs="+", metavar="URL",
                        help="video URL(s); with --from-json: path(s) to .info.json files")
    parser.add_argument("-o", "--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"folder for the generated pages (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--no-comments", action="store_true",
                        help="do not download comments")
    parser.add_argument("--max-comments", type=int, metavar="N",
                        help="limit the number of downloaded comments (replies included)")
    parser.add_argument("--no-avatars", action="store_true",
                        help="do not embed avatars (letter placeholders instead)")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help="use cookies from a browser (firefox, chrome, edge, ...) "
                             "when YouTube asks to sign in")
    parser.add_argument("--save-json", action="store_true",
                        help="also save the raw yt-dlp metadata next to the page")
    parser.add_argument("--from-json", action="store_true",
                        help="build pages from saved .info.json files instead of URLs")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    failed = 0
    for source in args.sources:
        try:
            archive(source, args)
        except yt_dlp.utils.DownloadError:
            failed += 1  # yt-dlp has already printed the reason
            log(f"FAILED: {source}")
        except Exception as error:
            failed += 1
            log(f"FAILED: {source}: {error}")
    if len(args.sources) > 1:
        log(f"Done: {len(args.sources) - failed} saved, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
