# yt-video-archiver

`yt2html.py` saves a YouTube video page as **one self-contained HTML file** for offline use, in the spirit of the SingleFile browser extension. Given a video URL, it fetches the video's metadata and all comments with [yt-dlp](https://github.com/yt-dlp/yt-dlp) and builds a dark, YouTube-like page. No AI, no browser, no API key: only Python, yt-dlp and an internet connection.

The video and subtitles are **not** downloaded; they are expected to be downloaded separately (e.g. with yt-dlp). The page gets the same base name as yt-dlp's default video file name, so both can sit side by side.

---

## A. What the page contains

| Part | Content |
|---|---|
| Thumbnail | Best available resolution, embedded in the file. |
| Title | Links to the original video. |
| Channel | Name (link), verified mark, subscriber count in YouTube's short form (`1.23M subscribers`). The channel avatar appears only if the uploader commented under the video (it is not part of the video metadata); otherwise a letter placeholder. |
| Facts | `Duration`, `Published`, `Views`, `Likes`, `Comments` (the count YouTube reports), and at the end, in grey, `Snapshot`: the date the data was fetched. It is added by the program, not by YouTube, hence the grey colour; `--no-snapshot` leaves it out (the date is still in the footer and in the comments note). Dates are `DD.MM.YYYY`. |
| Description | Full text (what YouTube hides behind *Show more*), with clickable URLs, timestamps (link to the video at that second), `#hashtags` and `@handles`. |
| Timestamp lists | YouTube's *Walkthroughs* (AI-generated questions with timestamps), *Chapters* and similar lists, under the name YouTube gives them. On YouTube they are horizontally scrolling cards; here each is a plain vertical list: timestamp (link to the video at that second) followed by the title. Shown only when the video has them. |
| Comments | All comments, top level oldest first; each thread with all replies already expanded, also oldest first, indented with a vertical line. Author, relative time as YouTube showed it on the download day (tooltip: estimated date), text with clickable links, likes, *Pinned* and *heart by creator* marks, creator highlighted. Avatars are embedded and not clickable. |

At the bottom: a footer with the source URL and the snapshot date.

Not included: the top bar, the video player, the transcript, *Video details* (it repeats the data above), recommendations.

---

## B. Notes for developers (human or agent)

### Files

| File | Role |
|---|---|
| `yt2html.py` | The whole program: fetching (yt-dlp), image embedding, comment tree, HTML rendering, CLI. |
| `pyproject.toml`, `uv.lock` | Project and dependency (`yt-dlp[default]`) definition for `uv`. |
| `.python-version` | Python version used by `uv`. |

### Conventions

- **English everywhere in code:** identifiers, comments, CLI options, program messages, fixed page labels and invisible HTML (classes, ids, meta tags). Only the archived content itself is in its original language.
- **Configuration in constants at the top of `yt2html.py`** (`DEFAULT_OUTPUT_DIR`, `OUTPUT_TEMPLATE`, `DATE_FORMAT`, `THOUSANDS_SEPARATOR`, `AVATAR_SIZE`, `IMAGE_WORKERS`, ...); per-run choices are CLI options.
- **One output file, no JavaScript.** Static HTML + CSS; every image is a `data:` URI. The page must work when opened from disk with no network.
- Program messages are prefixed with `[yt2html]`; yt-dlp prints its own `[youtube]` progress lines.
- A failure for one URL does not stop the others; the exit code is 1 if anything failed.

### How the data is obtained

`yt_dlp.YoutubeDL(...).extract_info(url, download=False)` with:

- `skip_download`, `noplaylist`, `getcomments`;
- `ignore_no_formats_error: True` — the video is not downloaded, so missing formats (e.g. when no JavaScript runtime is installed for YouTube's player challenges) must not abort the run;
- `extractor_args = {"youtube": {"comment_sort": ["new"], "max_comments": [N or "all", "all", "all", "all"]}}`.

Then, in the same `YoutubeDL` instance (same cookies and consent), the watch page is fetched once more with `ydl.urlopen()` for the timestamp lists (see below). The program adds two keys of its own to the info dict, both kept by `--save-json`: `yt2html_archived` (ISO date of fetching) and `yt2html_marker_lists`.

Used fields: `id`, `title`, `webpage_url`, `uploader`, `uploader_url`, `channel_is_verified`, `channel_follower_count`, `view_count`, `like_count`, `comment_count`, `duration`, `release_timestamp` / `timestamp` / `upload_date`, `thumbnail`, `thumbnails`, `description`, `chapters` (fallback only), `comments`.

Each comment: `id`, `parent` (`"root"` or the parent's id), `text`, `author`, `author_url`, `author_thumbnail`, `author_is_uploader`, `author_is_verified`, `like_count`, `is_pinned`, `is_favorited`, `timestamp`, `_time_text`.

### Comment order

- yt-dlp emits comments depth-first: a top-level comment, then its replies, then the next top-level comment.
- With `comment_sort=new` the top level comes **newest first** in YouTube's exact order; the program reverses it. The pinned comment arrives first regardless of its age.
- Replies come in YouTube's thread order, which is oldest first.
- YouTube gives only relative times (`2 years ago`), so `timestamp` is a coarse estimate. `chronological()` does a **stable** sort by it: the exact order from YouTube is kept within equal estimates, and only items whose estimate clearly disagrees (the pinned comment) move. Missing timestamps inherit the previous item's value.
- Comments whose parent is missing are shown at the top level. Deeper nesting (if YouTube ever returns it) is rendered recursively.

### Timestamp lists (Walkthroughs, Chapters)

- On the watch page they are engagement panels in `ytInitialData`: `engagementPanels[].engagementPanelSectionListRenderer` whose `content` is a `macroMarkersListRenderer` (e.g. `panelIdentifier` `engagement-panel-macro-markers-problem-walkthroughs`, title `Walkthroughs`; description chapters and auto chapters use the same renderer). Each item is a `macroMarkersListItemRenderer` with `title`, `timeDescription` (`2:12`) and `onTap.watchEndpoint.startTimeSeconds`.
- yt-dlp reads the same panels, but keeps only the first list (or the player-bar chapters instead) as `chapters` and drops its name. Therefore `fetch_marker_lists()` downloads `/watch?v=ID&hl=en` and `parse_marker_lists()` reads **all** such panels with their titles: the JSON after `ytInitialData =` is parsed with `json.JSONDecoder().raw_decode()`, which stops exactly at the end of the object.
- Result: `[{id, title, items: [{start_time, time_text, title}]}]` in `info["yt2html_marker_lists"]`. If the extra request fails, the value is `None` and the page falls back to yt-dlp's `chapters` under the title `Chapters`; an empty list means the video has none.
- Rendered after the description as `<section class="markers">` with an `<ol>`, one row per item.

### Images

- `fetch_image()` downloads with `urllib` (browser User-Agent, timeout, retries) and returns a `data:` URI; the MIME type comes from the response header or is sniffed from the bytes.
- Avatars are requested small: `=s88-...` in the URL is replaced by `=s{AVATAR_SIZE}`. They are downloaded in parallel (`ThreadPoolExecutor`).
- Each avatar is embedded **once** as a CSS class (`.av12{background-image:url(data:...)}`) and referenced by every comment of that author. The `<style>` in `<head>` is generated after the body, when all classes are known.
- A failed or missing avatar becomes a coloured circle with the first letter of the name (hue from a CRC32 of the name, so it is stable).
- The thumbnail falls back through `thumbnails` from best to worst if `thumbnail` fails.

### Text to HTML

`linkify()` escapes everything and recognises, in one regex pass: URLs (`http(s)://...`, `www....`; trailing punctuation and unbalanced `)`/`]` are cut off), timestamps `M:SS` / `H:MM:SS` (only valid values not longer than the video), `#hashtags` (not purely numeric), `@handles` (not inside e-mail addresses). Newlines are kept via `white-space: pre-wrap`.

### Snapshot date

`render_page()` receives the date from `info["yt2html_archived"]` (today for older JSON files without it), so a page rebuilt with `--from-json` keeps the date of the original fetch. The `Snapshot` tile uses the class `fact extra` (grey value); `show_snapshot=False` (`--no-snapshot`) omits it. The footer and the comments note always show the date.

### File names

`ydl.prepare_filename({**info, "ext": "html"}, outtmpl=OUTPUT_TEMPLATE)` — yt-dlp's own sanitizer, so characters forbidden on Windows are replaced exactly as in the video file name (e.g. `:` → `：`).

### Testing

`--from-json` renders a page from a saved `.info.json` (made with `--save-json`) without contacting YouTube for metadata, which makes layout changes quick to test. A synthetic `.info.json` is useful for edge cases: pinned comment, replies by the creator, missing avatar, HTML special characters in text, Polish characters and emoji, URLs in parentheses, invalid and too-long timestamps, e-mail addresses next to handles, a title with `: " | / < > ?`.

---

## C. User guide

### Requirements

- [uv](https://docs.astral.sh/uv/). `uv run` creates `.venv` and installs yt-dlp on first use.
- Optional, recommended by yt-dlp for YouTube: a JavaScript runtime, e.g. Deno (`winget install DenoLand.Deno`). Without it yt-dlp prints warnings about formats; for this program they are harmless, because the video is not downloaded.

### Commands

All commands in PowerShell, in the project folder.

| Task | Command |
|---|---|
| Archive one video | `uv run yt2html.py "https://www.youtube.com/watch?v=..."` |
| Several videos at once | `uv run yt2html.py "URL1" "URL2" "URL3"` |
| Save into another folder (e.g. next to the videos) | `uv run yt2html.py "URL" -o "D:\Videos\Channel"` |
| Without comments | `uv run yt2html.py "URL" --no-comments` |
| Only the first N comments (quick test) | `uv run yt2html.py "URL" --max-comments 200` |
| Without avatars (smaller file) | `uv run yt2html.py "URL" --no-avatars` |
| Without the grey Snapshot date tile | `uv run yt2html.py "URL" --no-snapshot` |
| When YouTube asks to sign in / confirm you are not a bot | `uv run yt2html.py "URL" --cookies-from-browser firefox` |
| Also keep the raw metadata | `uv run yt2html.py "URL" --save-json` |
| Rebuild a page from saved metadata (keeps the original snapshot date) | `uv run yt2html.py --from-json "output\Title [id].info.json"` |
| Help | `uv run yt2html.py --help` |

Always put the URL in quotes: in PowerShell `&` in a URL would otherwise be treated as an operator.

By default pages are saved in the `output` folder inside the project, named like yt-dlp names videos by default: `Title [video id].html`. An existing page with the same name is overwritten.

### What to expect

- Comments take most of the time: YouTube serves about 20 per request, so a video with 10,000 comments takes a few minutes. yt-dlp shows progress (`Downloading comment API JSON page 12 (240/~10000)`).
- `Ctrl+C` while comments are downloading stops the download and still builds the page from the comments fetched so far.
- The number of downloaded comments is usually a bit lower than YouTube's count (deleted, held for review or hidden comments); the page shows both.
- Subscriber counts are rounded by YouTube itself (`1.23M`); views and likes are exact.
- Comment times are as YouTube showed them on the download day (`3 years ago`). Hover over one to see the estimated date.
- *Walkthroughs* and other timestamp lists appear only for videos that have them on YouTube. If yt2html prints `Could not read timestamp lists`, the page uses yt-dlp's chapters instead (under the title `Chapters`), and *Walkthroughs* may be missing or appear under that title.
- The `Published` date is shown in your computer's time zone, as YouTube shows it.
- Typical page size: a few hundred KB up to a few MB for thousands of comments.

### Keeping it working

YouTube changes often and yt-dlp follows within days. When fetching starts failing, update yt-dlp first:

```powershell
uv lock --upgrade-package yt-dlp
uv sync
```

Then commit the updated `uv.lock`.

### Troubleshooting

- *`Sign in to confirm you're not a bot` / age-restricted video:* use `--cookies-from-browser firefox`. Firefox works best on Windows; Chrome and Edge encrypt their cookies in a way yt-dlp often cannot read.
- *`Comments are turned off`:* the page is still created, with an empty comment section.
- *`FAILED: ... this is a playlist or channel URL`:* pass a single video URL.
- *Some avatars are letters:* those images could not be downloaded (or the author has none); run again if many are missing.

### Files that are not committed

`.gitignore` excludes `.venv`, `output/`, `*.html` and `*.info.json`. The repository contains only code and configuration, never archived pages.
