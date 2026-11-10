# Development notes

How `yt2html` works, where it takes its data from, and how to test and release it. For using the program see [README.md](../README.md); for the history of versions see [CHANGELOG.md](../CHANGELOG.md).

YouTube does not document the page data the program reads. The paths below were checked on real pages (reference videos in the sections and in `tests/test_online.py`); when YouTube changes something, this document says where to look.

## Working on the project

Requirements: [uv](https://docs.astral.sh/uv/) and git.

```powershell
git clone https://github.com/mwislowski/yt-video-archiver
cd yt-video-archiver
uv sync                                   # creates .venv with the project and pytest
uv run yt2html.py --version               # run from the source folder
uv run pytest                             # tests
```

To use the source folder as the `yt2html` command everywhere, install it *editable* (changes to `yt2html.py` work at once):

```powershell
uv tool install --editable PATH\TO\yt-video-archiver
```

After a change in `pyproject.toml` (dependencies, command) add `--reinstall`. To update yt-dlp in the project: `uv lock --upgrade-package yt-dlp`, then `uv sync`, and commit the updated `uv.lock`.

`.gitignore` excludes `.venv`, the test cache (`.pytest_cache/`), build output (`build/`, `dist/`), `*.html`, `*.html.part` and `*.info.json` (pages created when the command is run in the project folder), and `*.local.md` (private notes, kept only on this computer, so without a copy on GitHub). The repository contains only code, configuration and documentation, never archived pages.

## Files

| File | Role |
|---|---|
| `yt2html.py` | The whole program: fetching (yt-dlp), image embedding, comment tree, HTML rendering, CLI. Holds the version (`__version__`). |
| `pyproject.toml`, `uv.lock` | Project, dependency (`yt-dlp[default]`) and packaging definition for `uv`: build backend hatchling, the `yt2html` command, version read from `yt2html.py`. |
| `.python-version` | Python version used by `uv`. |
| `LICENSE` | MIT License. |
| `README.md`, `CHANGELOG.md`, `docs/DEVELOPMENT.md` | User guide, history of versions, this document. |
| `tests/` | Automated tests (pytest): `conftest.py` with synthetic YouTube data and fakes, one file per area, `test_online.py` for checks against real YouTube pages. |

## Version, packaging, releases

- **One source of the version:** `__version__` in `yt2html.py`. `pyproject.toml` has `dynamic = ["version"]` and `[tool.hatch.version] path = "yt2html.py"`, so the package version and `yt2html --version` always agree. `--version` prints it together with the yt-dlp version (most problems come from an outdated yt-dlp).
- **Versioning:** the second number for new features or visible changes (0.2.0, 0.3.0), the third for fixes (0.2.1). What changed in each version is in [CHANGELOG.md](../CHANGELOG.md).
- **Command:** `[project.scripts] yt2html = "yt2html:main"`; the wheel contains only `yt2html.py` (`[tool.hatch.build.targets.wheel] only-include`). `main()` returns the exit code, which the generated command passes on.
- **Release:** run the tests (also `--online`), change `__version__`, move the *Unreleased* entries in `CHANGELOG.md` under the new version with its date (and update the compare links at the bottom), commit, then `git tag -a vX.Y.Z -m "..."`, `git push` and `git push origin vX.Y.Z`. The changelog entries can serve as the GitHub release notes.

## Conventions

- **English everywhere in code:** identifiers, comments, CLI options, program messages, fixed page labels and invisible HTML (classes, ids, meta tags). Only the archived content itself is in its original language.
- **Configuration in constants at the top of `yt2html.py`** (`DEFAULT_OUTPUT_DIR`, `OUTPUT_TEMPLATE`, `DATE_FORMAT`, `THOUSANDS_SEPARATOR`, `AVATAR_SIZE`, `IMAGE_WORKERS`, ...); per-run choices are CLI options.
- **One output file, works without JavaScript.** Static HTML + CSS; every image is a `data:` URI. The page must work when opened from disk with no network. The only script is the built-in player (`PLAYER_JS`), which adds something and never replaces content; index pages have no script.
- Program messages are prefixed with `[yt2html]`; yt-dlp prints its own `[youtube]` progress lines.
- A failure for one URL does not stop the others; the exit code is 1 if anything failed.

## The page, part by part

What a user sees, with the rules behind each part; the sections further down explain where the data comes from.

| Part | Content |
|---|---|
| Thumbnail | Best available resolution, embedded in the file. |
| Title | Links to the original video. |
| Channel | Name (link), verified mark, subscriber count in YouTube's short form (`1.23M subscribers`). Channel avatar from the owner row under the video on the watch page (`videoSecondaryInfoRenderer.owner.videoOwnerRenderer.thumbnail`, the largest size; yt-dlp gives the avatar only for channel pages). Fallback when the watch page could not be read, or for older JSON files: the avatar of a comment by the uploader; otherwise a letter placeholder. |
| Facts | `Duration`, `Published`, `Views`, `Likes`, `Comments` (the count YouTube shows, e.g. `116`, or `1.2K` when YouTube shortens it), and at the end, in grey, `Snapshot`: the date the data was fetched. It is added by the program, not by YouTube, hence the grey colour; `--no-snapshot` leaves it out (the date is still in *About this copy* and in the comments note). Dates are `DD.MM.YYYY`. |
| Description | Full text (what YouTube hides behind *Show more*), with clickable URLs, timestamps (link to the video at that second), `#hashtags` and `@handles`. |
| AI-generated video summary | YouTube's summary of the video (on YouTube collapsed under *Summary* below the description), shown expanded: the heading and the text, nothing else. Only when YouTube offers one. |
| Chapters | The chapters YouTube marks on the progress bar, with a grey note on their origin: `from description` (the author's list in the description) or `auto-generated` (created by YouTube). The note appears only when the program can confirm the origin (see *Chapters and other timestamp lists*); otherwise the heading is just `Chapters`. |
| Other timestamp lists | E.g. *Walkthroughs* (questions with timestamps, apparently generated by YouTube; not documented by YouTube), under the name YouTube gives them, without an origin note. On YouTube these lists (and chapters) are horizontally scrolling cards; here each is a plain vertical list after the description: timestamp (link to the video at that second), then the title. |
| People mentioned | YouTube's automatic list of well-known people mentioned in the video (matched from the transcript against Google's Knowledge Graph), after the timestamp lists: heading, number of people, YouTube's explanatory note (normally behind the ⓘ button; its *Learn more* link stays clickable), then a photo of each person with the name and YouTube's short description below it, always shown in full. Photos are embedded and not clickable. Only when YouTube shows the section. |
| Explore the podcast | The podcast (playlist marked as a podcast by the channel) the video belongs to, under YouTube's heading (e.g. `Explore the podcast`; YouTube uses the same element for courses): square cover and title, both linking to the playlist, the line below the title and the number of episodes. YouTube's large *Podcasts* button is left out. |
| Transcript | The text YouTube shows after *Show transcript*, already open, after the timestamp lists. With chapters it is split under the chapter titles. Versions, chosen with `--transcript` and shown in the given order: **`plain`** (default): each part (or the whole text without chapters) is one continuous text, captions joined with spaces, no added paragraphs, no timestamps; **`timestamps`**: every caption is its own line starting with its timestamp (a link to the video at that second); **`bilingual:LANG`**: as `timestamps`, plus a second column with YouTube's automatic translation into `LANG` (e.g. `bilingual:pl`), with grey column headings naming both languages; if that translation is not available, the program stops with an error and saves nothing. A grey note gives the language and, for automatic captions, `auto-generated`; when both `plain` and `timestamps` are shown, the timestamped one adds `with timestamps`. |
| License | Only on videos whose author chose a license other than the standard YouTube one (e.g. *Creative Commons Attribution license (reuse allowed)*), after the transcript: heading `License` and the text as YouTube shows it, with its link kept clickable. The rest of YouTube's *Video details* is not repeated. |
| Comments | All comments, top level oldest first; each thread with all replies already expanded, also oldest first, indented with a vertical line. Author, relative time as YouTube showed it on the download day (tooltip: estimated date), text with clickable links, likes, *Pinned* and *heart by creator* marks, creator highlighted. Avatars are embedded and not clickable. |

At the bottom: *About this copy*, folded (see below).

Not included: the top bar, the video player, *Video details* (it repeats the data above), recommendations.

## How the data is obtained

`yt_dlp.YoutubeDL(...).extract_info(url, download=False)` with:

- `skip_download`, `noplaylist`, `getcomments`;
- `extract_flat: "in_playlist"`: a safety net. `noplaylist` only turns watch URLs with `&list=` into the video; an unrecognised URL that leads to a playlist or channel would otherwise be extracted video by video, comments included. With it, yt-dlp returns only the flat list, and `archive()` fails with `this URL leads to a playlist or channel`. Playlist URLs never get here (see *Playlists*), channel URLs fail before any request (`is_channel_url()`);
- `ignore_no_formats_error: True` — the video is not downloaded, so missing formats (e.g. when no JavaScript runtime is installed for YouTube's player challenges) must not abort the run;
- `extractor_args = {"youtube": {"comment_sort": ["new"], "max_comments": [N or "all", "all", "all", "all"]}}`.

Then, in the same `YoutubeDL` instance (same cookies and consent), the watch page is fetched once more with `ydl.urlopen()` for the timestamp lists and the description extras (see below). The program adds keys of its own to the info dict (constants `*_KEY` at the top of `yt2html.py`), all kept by `--save-json`, among them: `yt2html_archived` (ISO date of fetching), `yt2html_marker_lists`, `yt2html_description_extras`, `yt2html_channel_avatar` (the avatar URL, see *The page, part by part*), `yt2html_youtube_comment_count` (the comment count text from the comments panel header, `engagementPanels[]` with `panelIdentifier` `engagement-panel-comments-section` → `header.engagementPanelTitleHeaderRenderer.contextualInfo`) and `yt2html_max_comments` (the `--max-comments` value, `null` for no limit).

Why the program reads the comment count itself: when yt-dlp downloads comments, it **replaces** `comment_count` with the number of comments it downloaded (and sets it to `null` after `Ctrl+C`). So with `--max-comments 50` yt-dlp's `comment_count` is 50. `youtube_comment_count()` therefore uses the page's count; without it, yt-dlp's `comment_count` is used only when it can be trusted (comments not downloaded, or downloaded completely without a limit).

Used fields: `id`, `title`, `webpage_url`, `uploader`, `uploader_url`, `channel_is_verified`, `channel_follower_count`, `view_count`, `like_count`, `comment_count`, `duration`, `release_timestamp` / `timestamp` / `upload_date`, `thumbnail`, `thumbnails`, `description`, `chapters` (fallback only), `comments`.

Each comment: `id`, `parent` (`"root"` or the parent's id), `text`, `author`, `author_url`, `author_thumbnail`, `author_is_uploader`, `author_is_verified`, `like_count`, `is_pinned`, `is_favorited`, `timestamp`, `_time_text`.

## Comment order

- yt-dlp emits comments depth-first: a top-level comment, then its replies, then the next top-level comment.
- With `comment_sort=new` the top level comes **newest first** in YouTube's exact order; the program reverses it. The pinned comment arrives first regardless of its age.
- Replies come in YouTube's thread order, which is oldest first.
- YouTube gives only relative times (`2 years ago`), so `timestamp` is a coarse estimate. `chronological()` does a **stable** sort by it: the exact order from YouTube is kept within equal estimates, and only items whose estimate clearly disagrees (the pinned comment) move. Missing timestamps inherit the previous item's value.
- Comments whose parent is missing are shown at the top level. Deeper nesting (if YouTube ever returns it) is rendered recursively.

## Description extras (AI-generated video summary, People mentioned, Explore the podcast)

YouTube shows extra elements below the description text. They are the `items` of the engagement panel `engagement-panel-structured-description` (`content.structuredDescriptionContentRenderer.items`), in display order, e.g. (reference video `Z8lf5oWKfj0`): `expandableVideoDescriptionBodyRenderer` (the description itself), `expandableMetadataRenderer` (the summary), `howThisWasMadeSectionViewModel` (*How this was made*), `horizontalCardListRenderer` (the chapter cards), `videoDescriptionCourseSectionRenderer`, `videoDescriptionTranscriptSectionRenderer`, `videoDescriptionInfocardsSectionRenderer`.

`parse_description_extras()` reads these items, keeping YouTube's order, into `info["yt2html_description_extras"]` (`None` if the extra request failed, `[]` if there are none):

| `type` | Source item | Stored | Rendered |
|---|---|---|---|
| `video_summary` | `expandableMetadataRenderer` whose `expandedContent` is a `videoSummaryContentViewModel` | `title` (`header.expandedTitle`, `AI-generated video summary`), `paragraphs` (`paragraphs[].videoSummaryParagraphViewModel.text.content`). The collapsed teaser, the disclaimer subtitle and the feedback button are left out. | Before the chapters: `<section class="desc-extra video-summary">`, one `<p>` per paragraph (with `linkify()`). |
| `people` | `videoAttributesSectionViewModel` (`parse_people_section()`) | `title` (`headerTitle`, `People mentioned`), `subtitle` (`headerSubtitle`, `2 people`), `info` (the text of the ⓘ dialog: `headerInfoButton…dialogViewModel.content.basicContentViewModel.paragraphs[0].text`), `people[]`: `name` (`videoAttributeViewModel.title`), `description` (`subtitle`), `image` (`image.sources`, the smallest ≥ 240 px). | After the chapters: `<section class="desc-extra people">`; cards `.person` 150 px wide with photo, name and the full description (`.about`, wrapped, never cut). |
| `playlists` | `videoDescriptionCourseSectionRenderer` (`parse_playlist_section()`), items `mediaLockups[].structuredDescriptionPlaylistLockupRenderer` | `title` (`sectionTitle`, `Explore the podcast`), `items[]`: `title`, `url` (`navigationEndpoint.browseEndpoint.browseId` without the leading `VL` → `/playlist?list=…`), `byline` (`shortBylineText`), `count` (`videoCountShortText`, `557 episodes`), `image` (`thumbnail.thumbnails`, ≥ 240 px). `topicLink` (the *Podcasts* button) is left out. | After *People mentioned*: `<section class="desc-extra playlists">`, cover 120 px with the details aligned to its top; the podcast title is bold 14 px under the 16 px section heading (the same step as chapter headings in the transcript); cover and title link to the playlist. |

Attributed text (`attributed_text()`, `render_attributed()`): YouTube text with `commandRuns`. Only runs that open a web address (`innertubeCommand.urlEndpoint.url`) become links; others, such as *send feedback* (`userFeedbackEndpoint`, which runs YouTube's own script), stay plain text. `startIndex`/`length` count UTF-16 code units (as in JavaScript), so the text is sliced in UTF-16.

Images of the extras are collected by `extra_image_urls()` and embedded by `fetch_images()` (in parallel, unchanged size). A missing image becomes a grey box with the first letter (`.img-placeholder`).

CSS class names must not clash with existing ones: `description` (the description box) and `extra` (the grey Snapshot tile) are taken; hence `desc-extra`, `.about`, `.img-placeholder`.

Seen on reference video `9uSXOr-AdAU` (People mentioned + Explore the podcast) and `Z8lf5oWKfj0` (Explore the podcast). According to YouTube Help, *People mentioned* is generated automatically and being extended to more videos; *Explore the podcast* appears for videos in a playlist marked as a podcast in YouTube Studio.

## License

- On YouTube the license is a row of the video's metadata (`videoSecondaryInfoRenderer.metadataRowContainer.metadataRowContainerRenderer.rows[].metadataRowRenderer` with `title` `License`), not part of the expanded description's *Video details* list. It exists only for non-standard licenses. Reference video `KZtvWKpXPro`: `Creative Commons Attribution license (reuse allowed)` → `https://www.youtube.com/t/creative_commons`.
- `parse_license()` keeps the text runs and their web links (`navigationEndpoint.urlEndpoint.url`; YouTube `/redirect?q=` links are unwrapped by `unwrap_redirect()`). Stored in `info["yt2html_license"]` as `{title, runs: [{text, url}]}`, `None` when the video has none.
- `license_of()`: if the watch page could not be read (or for an older JSON file), yt-dlp's plain-text `license` field is used instead, without the link.
- Rendered after the transcript, before the comments, as `<section class="desc-extra license">`.

## Chapters and other timestamp lists

Everything is read from `ytInitialData` of the watch page, downloaded once more by `add_watch_page_data()` (`/watch?v=ID&hl=en`), which also reads the description extras above; the JSON after `ytInitialData =` is parsed with `json.JSONDecoder().raw_decode()`, which stops exactly at the end of the object. yt-dlp is not used for this: it keeps only one list as `chapters`, without its name or origin, and when the progress bar has no chapters it takes the first panel, which can be *Walkthroughs*.

Two places in `ytInitialData` hold timestamp lists:

| Place | Path | What it says |
|---|---|---|
| Progress bar | `playerOverlays.playerOverlayRenderer.decoratedPlayerBarRenderer.decoratedPlayerBarRenderer.playerBar.multiMarkersPlayerBarRenderer.markersMap[]` | `key` = `DESCRIPTION_CHAPTERS` or `AUTO_CHAPTERS`; `value.chapters[].chapterRenderer` with `title` and `timeRangeStartMillis`. The older `chapteredPlayerBarRenderer.chapters` (no key) is read too. |
| Engagement panels | `engagementPanels[].engagementPanelSectionListRenderer` with `content.macroMarkersListRenderer` | `panelIdentifier` and header title; items `macroMarkersListItemRenderer` with `title`, `timeDescription` and `onTap.watchEndpoint.startTimeSeconds`. Info rows (`macroMarkersInfoItemRenderer`, e.g. "These chapters are auto-generated") are skipped. |

Panel identifiers seen so far (04.11.2026): `engagement-panel-macro-markers-description-chapters`, `engagement-panel-macro-markers-auto-chapters` (both titled `Chapters`) and `engagement-panel-macro-markers-problem-walkthroughs` (`Walkthroughs`; the progress bar has no markers for it). Reference videos: `Z8lf5oWKfj0` (description chapters), `qyqCTMirNWg` (auto chapters), `hz6-3-7GGyI` (Walkthroughs only).

`parse_marker_lists()` builds the lists:

- **Chapters** (panels whose id contains `chapters`, plus the progress bar): the progress bar defines the chapters and their times (a chapter panel is used only when the bar has none); the panel gives the heading. Shown first, as one list.
- **Origin** (`decide_chapter_source()`): YouTube's own markings (progress-bar key and panel id) are combined with an independent check of the description (`description_chapter_starts()`: the run of consecutive timestamped lines beginning with the first `0:00` line, at least three, ascending, each chapter at least 10 s — YouTube's rules).
  - YouTube says *description* and the description has a valid list with the same start times → `description`.
  - YouTube says *auto* and the description has no valid list → `auto`.
  - Anything else — markings that disagree with each other or with the description, or an unknown key/panel id → `uncertain`: neutral heading, warning `Chapter source unclear: ...` on screen, the reason kept in `source_problem`. The program never guesses an origin.
- **Other panels** (e.g. Walkthroughs): as they are, `source: null`, in page order after the chapters.

Stored in `info["yt2html_marker_lists"]` as `[{id, title, source, source_problem?, items: [{start_time, time_text, title}]}]`. `None` means the extra request failed: the page then falls back to yt-dlp's `chapters` under a neutral `Chapters` heading (it may even be a Walkthroughs list). An empty list means the video has none.

Rendered after the description as `<section class="markers">` with an `<ol>`, one row per item; the origin note is `<span class="source">` (grey) in the heading, using the labels in `SOURCE_NOTES`.

## Transcript

- YouTube's own transcript panel loads its text from `/youtubei/v1/get_transcript` (parameters in `engagement-panel-searchable-transcript`). Checked on 05.11.2026: a request built outside the browser is refused (`400 Precondition check failed`); the browser sends extra integrity data that only YouTube's own JavaScript produces. So the program does not use it.
- The panel shows the text of a caption track, so `fetch_transcript()` downloads that track instead, from the URLs yt-dlp has already extracted (`info["subtitles"]`, `info["automatic_captions"]`; yt-dlp also deals with tokens YouTube may require for captions). Format `json3`, read with `ydl.urlopen()`.
- Track choice: the panel opens with the caption track of the **original audio**, which matters for auto-dubbed videos. They have one audio track per dubbed language and automatic captions for each of them, and yt-dlp marks every one of those with `-orig` (reference video `Z8lf5oWKfj0`: English original, 20 dubs; taking the first `-orig` track gave Arabic).
  - `default_caption_track()` reads `ytInitialPlayerResponse` from the watch page: `captions.playerCaptionsTracklistRenderer.audioTracks[defaultAudioTrackIndex]` is the original audio; its `defaultCaptionTrackIndex` points into `captionTracks` (language and `kind: "asr"` for automatic captions). Without that index, the language of the audio stream whose `audioTrack.displayName` contains `original` (e.g. `English (US) original`, id `en-US.4`) is used, else the audio track's id, else its first caption track. Stored as `info["yt2html_default_caption_track"]` = `{language, kind: "asr" | "manual" | null}`.
  - `choose_caption_track()` then picks from yt-dlp's tracks in that language: automatic captions first if the page says `asr`, otherwise author subtitles first (`en`, `en-US`, ...), then automatic captions (`<lang>-orig`, `<lang>`). Without page data the original language comes from yt-dlp's formats (`language_preference` 10 = original), then `info["language"]`, then a single `-orig` track; with none of these, any author subtitles. `live_chat` is ignored; translated tracks are never used.
- `parse_json3_captions()`: one cue per json3 event with `segs`; the `utf8` pieces are joined, line breaks and repeated spaces become single spaces; events holding only a line break (automatic captions add them) are skipped. Cue start = `tStartMs` / 1000.
- Stored in `info["yt2html_transcript"]` as `{language, name, kind: "manual" | "auto", cues: [{start, text}], translations?: {LANG: {language, name, cues}}}`; `None` if the video has no transcript or the download failed; absent with `--transcript none`. The cues are kept, so `--from-json` can rebuild the page with any versions (`--transcript none` there leaves a saved transcript out; a `bilingual:LANG` needs that translation in the file).
- Bilingual (`bilingual:LANG`): `fetch_translation()` downloads the same caption track with `tlang=LANG` added (`with_query()`), i.e. YouTube's auto-translate, which keeps the original timing; several languages are downloaded one after another. The languages YouTube offers are read from the watch page (`ytInitialPlayerResponse` → `captions.playerCaptionsTracklistRenderer.translationLanguages`) into `info["yt2html_translation_languages"]` (`{code: name}`). YouTube answers `HTTP 429 Too Many Requests` to auto-translated captions requested less than about a minute after the video data was fetched; original captions and author subtitles are not affected (yt-dlp issue #13831, where a maintainer confirmed that waiting at least 60 s works). So translations are requested no sooner than `TRANSLATION_WAIT` (65 s) after fetching started (comments downloaded meanwhile count towards it), and a 429 is retried after `TRANSLATION_RETRY_WAITS` (60 s, then 120 s). Errors (`TranscriptError`, raised during fetching, so nothing is written, not even `--save-json`): no transcript, a language not offered (the message lists the available codes), a language equal to the transcript's own, an empty or failed download. `align_translation()` gives each original cue the translated cues starting at the same time (or before the next original cue); `render_bilingual_transcript()` draws rows as a 3-column CSS grid (time, original, translation; on narrow screens the translation goes under the original, in grey).
- Option: `--transcript VERSIONS`, one comma-separated argument (so it cannot swallow the video URL, unlike space-separated values), parsed by `transcript_spec()` into an ordered list of `(version, language)` pairs: `plain`, `timestamps`, `bilingual:LANG`; `none` gives `[]`; default `[("plain", None)]`. Errors (argparse, before anything is fetched): empty item, unknown version, `bilingual` without a valid language code, a language after `plain`/`timestamps`, a repeated item, `none` combined with anything. `render_transcripts()` makes one section per item, in order (`render_transcript()`, class `transcript` or `transcript with-timestamps`; `render_bilingual_transcript()`, class `transcript bilingual`).
- `render_transcript()` puts each cue under the last chapter that starts at or before it (chapters from the chapter list described above; `<h3>` per chapter, empty chapters skipped). Without timestamps one `<p>` per part; with timestamps `<div class="cue">` rows.
- The cues are the caption lines, which is also how the panel divides the text; tiny differences to the panel are possible.

**Speaker changes.** YouTube's automatic captions start a new speaker's words with `>>` (`>>>` for a new topic), a convention of TV captions; translations do not have it. `speaker_html()` replaces each mark (`SPEAKER_RE`) with ours: in the timestamped and bilingual transcripts an empty `<span class="speaker" aria-hidden="true">` whose icon comes from CSS (`.speaker::before`: a speech bubble outline as an SVG `mask`, painted with `currentColor`, i.e. the grey `--muted`), and the translation of a cue with a mark gets one at its start too; in the continuous transcript a grey `<span class="speaker">|</span>` between spaces, left out at the start of a part (`at_start=False`). The cue data (and `--save-json`) keep YouTube's text. The lines under the video remove the marks (`textOf()` in `PLAYER_JS`).

## Images

- `fetch_image()` downloads with `urllib` (browser User-Agent, timeout, retries) and returns a `data:` URI; the MIME type comes from the response header or is sniffed from the bytes.
- Avatars are requested small: `=s88-...` in the URL is replaced by `=s{AVATAR_SIZE}`. They are downloaded in parallel (`ThreadPoolExecutor`).
- Each avatar is embedded **once** as a CSS class (`.av12{background-image:url(data:...)}`) and referenced by every comment of that author. The `<style>` in `<head>` is generated after the body, when all classes are known.
- A failed or missing avatar becomes a coloured circle with the first letter of the name (hue from a CRC32 of the name, so it is stable).
- The thumbnail falls back through `thumbnails` from best to worst if `thumbnail` fails.

## Text to HTML

`linkify()` escapes everything and recognises, in one regex pass: URLs (`http(s)://...`, `www....`; trailing punctuation and unbalanced `)`/`]` are cut off), timestamps `M:SS` / `H:MM:SS` (only valid values not longer than the video), `#hashtags` (not purely numeric), `@handles` (not inside e-mail addresses). Newlines are kept via `white-space: pre-wrap`.

## Theme

- All colours are CSS variables on `:root` (`--bg`, `--text`, `--muted`, `--panel`, `--line`, `--link`, `--chip`, `--media-bg`); the dark values are in `CSS`, the light ones (YouTube's light theme) in `LIGHT_VARIABLES`. New styles must use these variables, not fixed colours, or they break one of the themes. Exceptions that work on both: the red heart, the white letter on the coloured avatar circles.
- `theme_css()` adds, after `CSS`: nothing for `dark`; `:root{light variables}` for `light`; the same inside `@media (prefers-color-scheme: light)` for `auto`, so the page follows the device setting and changes with it, without JavaScript. `color-scheme` (CSS property and `<meta name="color-scheme">`) makes the browser draw scrollbars and the like in the matching colours.
- `--theme {auto,dark,light}`, default `auto`: the page itself follows the light/dark setting of whatever device or browser opens it, each time it is opened (in most browsers "device" means the system setting unless the browser's own appearance setting overrides it). Works with `--from-json` too (the theme is not stored in the data).

## Snapshot date

`render_page()` receives the date from `info["yt2html_archived"]` (today for older JSON files without it), so a page rebuilt with `--from-json` keeps the date of the original fetch. The `Snapshot` tile uses the class `fact extra` (grey value); `show_snapshot=False` (`--no-snapshot`) omits it. *About this copy* and the comments note always show the date.

## File names

`ydl.prepare_filename({**info, "ext": "html"}, outtmpl=OUTPUT_TEMPLATE)` — yt-dlp's own sanitizer, so characters forbidden on Windows are replaced exactly as in the video file name (e.g. `:` → `：`).

## Existing pages, `--overwrite`

- **One rule for files with an id** (`files_with_id()` / `file_with_id()`, since 0.7.0): in the output folder (not its subfolders), a file whose name contains `[ID]` anywhere (with the brackets, the whole id) and ends with the right extension, in any case; the rest of the name does not matter. Pages and indexes: `.html` (`existing_page()`), saved data: `.info.json` (`saved_data_path()`), videos: `VIDEO_EXTENSIONS` (`video_files_for()`). Several matches: the first by name, with a note printed once per folder, id and kind (`_SEVERAL_NOTED`). Before 0.7.0 the names had to end with `[ID].html` or `[ID].info.json`, and videos were only tried by exact names.
- A video counts as archived when the output folder holds such a page; pages may be renamed.
- The id is read from the URL without any request (`video_id_from_url()`: yt-dlp's `YoutubeIE.suitable()` / `extract_id()` for `watch?v=`, `youtu.be/`, `shorts/` and bare ids; the `v=` parameter for watch URLs with `&list=`, which yt-dlp gives to its playlist extractor). So a skipped video costs nothing. If the URL shows no id, the check is repeated after fetching, with `info["id"]`.
- Default: skip (`Skipped (page exists): ...`), and `archive()` returns `None`; `main()` counts saved, skipped and failed. `--overwrite` makes the page again; `--from-json` always does (no requests, and re-rendering is its purpose). A replaced page keeps its existing (possibly renamed) file name.
- Pages are written by `write_atomically()`: first `NAME.html.part`, then renamed (`os.replace`). An interrupted run leaves at most a `.part` file, which never counts as a finished page; a video that failed has no page and is tried again on the next run.

## Sources given by page names

`source_from_page_name()` turns a file name with an id in square brackets (`id_in_name()`: only the last path part counts, any extension, the last bracketed part that is an 11-character video id or a playlist id, so `Song [Remix] [ID] (copy).mp4` works and `[Remix]` is never taken for an id; not a URL) into the watch URL or the playlist URL; anything else stays as it is. `collect_sources()` applies it to the command line and to list files before removing duplicates, except with `--from-json` (see below).

## Sources with `--from-json`

- **Patterns:** `expand_pattern()` expands a source with `*` or `?` itself (Windows shells pass patterns on unchanged), sorted, in the command line and in `-a` files. Square brackets are made literal (`[` → `[[]`), since every page and JSON name ends with `[VIDEO_ID]`. A pattern without matches stays and fails as `no file matches ...`.
- **`saved_data_path()`**, in `archive()`: an existing file is used as it is (so nothing that worked before changes); otherwise a video id, or any file name with a video id in square brackets (`id_in_name(…, playlists=False)`), is looked up in the output folder (`-o`) as a `.info.json` file with `[ID]` in its name, where `--save-json` writes it. Failures say what was looked for: `no saved data for ID in FOLDER (looked for *[ID]*.info.json)`, `no such file: ...`.
- An id starting with `-` would be taken for an option; then give the file or page name.
- **Comments at a rebuild** (`narrow_saved_comments()`): `--no-comments` leaves out the saved comments; `--max-comments N` keeps the first N in the saved order (yt-dlp's, replies counted), i.e. the comments a download with that limit gets, and sets the limit, so a cut after `Ctrl+C` counts as limited. A larger limit cannot widen the data: when the saved comments are limited or interrupted, a note says to download again. `--cookies-from-browser` has no effect and gets a note.

## Batch files (`-a`)

- `read_batch_file()` reads the file as UTF-8 (`utf-8-sig`, so a byte order mark from a Windows editor does no harm), strips each line and drops blank lines and lines starting with `#`. A file that cannot be read, is not UTF-8 or lists nothing is an error.
- `collect_sources()`, called from `parse_args()`, builds the final list: the URLs from the command line first, then each `-a` file in the order given. A video is kept once: two URLs are the same video if `video_id_from_url()` gives the same id (`youtu.be/ID`, `watch?v=ID`, a bare id); other lines (playlist URLs, `.info.json` paths with `--from-json`) must match exactly. It logs `Read N URLs from FILE (M duplicates ignored).` per file.
- Errors in the files themselves end the program through `parser.error()` (exit code 2) before anything is fetched. A playlist line is expanded like a playlist in the command (see *Playlists*); a bad line (wrong URL, channel) only fails that line in `main()`, like a bad URL on the command line.
- The file is only read, never changed: what is done follows from the pages in the output folder, so running the same list again archives what is missing and retries what failed.

## Playlists

- `playlist_id_from_url()` recognises `/playlist?list=ID` (also `m.youtube.com`, a trailing `/`) and bare playlist ids (yt-dlp's `YoutubePlaylistIE.suitable()`), without a request; watch URLs with `&list=` stay videos. `is_channel_url()`: anything else that yt-dlp's `YoutubeTabIE` handles (`@handle`, `/channel/`, `/c/`, their tabs); these fail with a hint to use a playlist.
- `fetch_playlist()`: `extract_info(url, download=False, process=False)` with `extract_flat: "in_playlist"` (and the cookies): only the list, no video is extracted; `process=False` avoids yt-dlp's `Downloading item N of M` line per entry. A `url` / `url_transparent` result is followed (up to 3 times); anything but `_type: playlist` is an error. The entries are read inside the `YoutubeDL` block, because yt-dlp fetches further pages (about 100 videos each) lazily.
- `entry_video_id()`: the entry's `id` if it is a video id, else the id in its `url`; nested playlists and channels give `None` and are left out. Private and deleted videos are entries too: they fail when fetched (`FAILED: URL`) and are retried on every run.
- `main()` expands each playlist where it stands among the sources into watch URLs and archives them one by one, exactly like separate URLs (skipping, failures, summary). `seen` holds the video ids handled in this run: a video twice in a playlist, in two playlists or also given separately is archived once (`Playlist "TITLE": N videos (M duplicates ignored).`, or `Ignored duplicate: URL` for a separate URL after the playlist). A playlist that cannot be read counts as one failure. The summary counts videos and is printed whenever a playlist was given.
- Nothing about the run is stored: the next run reads the list again (new episodes are found), skips the videos with a page and tries the rest again.

**Ctrl+C.** yt-dlp catches `Ctrl+C` while it downloads comments: it ends only the comments, keeps those fetched so far and sets `comment_count` to `None` (`comments_interrupted()`; with comments disabled there is no list). `archive()` still makes the page and then raises `CommentsInterrupted` (a `KeyboardInterrupt`), so a run with several videos does not go on with the next one. `main()` catches any `KeyboardInterrupt` between and during videos, prints the summary without a traceback and returns 130. The page made from interrupted comments counts as finished on later runs (`--overwrite` remakes it).

## Info cards

The author's info cards (the "i" teasers during the video) are read from the watch page that `add_watch_page_data()` fetches anyway; the only further requests are their images and one per link card (below). With `--no-cards`, `add_watch_page_data(..., cards=False)` does not touch them (`CARDS_KEY` stays absent), and `archive()` also drops cards kept in a `--from-json` file before images are fetched.

- **Content:** the description panel's `videoDescriptionInfocardsSectionRenderer.infocards[].compactInfocardRenderer.content`, one lockup per card (`CARD_TYPES`, `parse_card()`):
  - `structuredDescriptionVideoLockupRenderer` (video): `title`, `shortBylineText` ("by …", the "by " is removed), `lengthText`, `metadataDetails`, `navigationEndpoint.watchEndpoint.videoId`;
  - `structuredDescriptionPlaylistLockupRenderer` (playlist): `title`, `shortBylineText`, `videoCountShortText`, `watchEndpoint.playlistId` (or a `browseEndpoint` `VL…` id);
  - `structuredDescriptionChannelLockupRenderer` (channel): `title`, `subscriberCountText`, `browseEndpoint.browseId`;
  - `mediaLockupRenderer` (link): `title`, `subtitle`, `endpoint.urlEndpoint.url`, a one-time `youtube.com/redirect?event=infocard&redir_token=…` link without the target address. `resolve_card_links()` opens it once (with yt-dlp's `urlopen`): YouTube answers with its "Are you sure you want to leave YouTube?" page, whose button `<a id="invalid-token-redirect-goto-site-button" href="TARGET">` holds the target (`LEAVE_BUTTON_RE`). The card then links to the target, and its domain (without `www.`) is added to the details. If the request fails or the page has no `http(s)` target, the card keeps the redirect and a message says so; the video is archived as usual. Checked 07.11.2026 on `dkh1A_mAhY8` (targets such as `content.nordvpn.com/…`, `nordpass.com`); the target itself is not followed further.
  - Image: the smallest of `thumbnail.thumbnails` / `thumbnailDetails.thumbnails` at least 160 px wide (they come as 112 and 224 px).
- **Time and teaser:** `ytInitialData["cards"].cardCollectionRenderer.cards[].cardRenderer` (`card_cues()`): `cueRanges[0].startCardActiveMs` and `teaser.simpleCardTeaserRenderer.message`, in the same order as the lockups. They are paired by position only if there are as many cues as lockups; otherwise the cards get no time, never a wrong one. The teaser is kept as `message` only if it differs from the title (e.g. "Check out Nonstop Eurotrip!"). Not `ytInitialPlayerResponse["cards"]`: on all checked videos it held only YouTube's "View corrections" teaser.
- Cards are sorted by time (cards without one last, in YouTube's order).
- **Page** (`render_cards()`): `<section class="desc-extra cards" id="info-cards">` right after the chapters and other timestamp lists (the user's choice; YouTube shows them inside the description panel). One `<li>` per card: time (`time_link()`, empty without a time), image (160×90, the area of a 120×120 podcast cover; channels round 90×90; 112×63 on phones), title linking to the target, grey line `Kind · details · “message”` (`CARD_LABELS`).
- End screens are left out on purpose: their video elements may be picked by YouTube for each viewer, and the data does not say which.
- Checked on 07.11.2026: `KDRJ5aKWicc` (video card), `nMwUppuqDk4` (playlist), `EBQzfbqFKN0` (channel and three videos), `dkh1A_mAhY8` (three links).

## Local video player

The page plays a downloaded copy of the video lying next to it. Parts: `PLAYER_TILE` (a `<button class="fact play">`, first in `.facts`, same size as the other tiles: value `▶ Play` / `✕ Close`, label `Player`), `render_player()` (`<aside class="player">` before `<main>`: `<video preload="metadata">`, a range input with times, buttons `|◀ −10 s ↺ ▶ +10 s ▶|`, speeds `PLAYER_SPEEDS`), `PLAYER_JS` (one inline `<script>` at the end of `<body>`), and the CSS block *Local video player* (colours `--accent`, `--on-accent`; width `--player-w: clamp(320px, 40vw, 720px)`).

- **Finding the file (in the browser, every time the page opens):** a browser cannot list a folder, only try names. First the video files with `[id]` in their name found next to the page when it was built (`video_files_for()`, stored as exact names in `data-files`; so renamed videos work), then the page's own file name without `.html` (from `location.pathname`), then `video_names()` (yt-dlp's default name `Title [id]`, stored in `data-names`, for renamed pages), each with `VIDEO_EXTENSIONS` (`data-extensions`). A video added later is found only under the page's name or yt-dlp's name, otherwise after a rebuild. The candidates are tried one by one as the `<video>` source (`encodeURIComponent`, so `#`, spaces and Unicode work); the first whose metadata loads wins and adds `has-video` to `<body>`, which shows the tile. No file, or no script: the page looks as before.
- **States** (classes on `<body>`): `player-open` (panel `position:fixed` on the left, `.page` gets `margin-left: var(--player-w)` and no max-width, the thumbnail is hidden), `pip` (the video is in the browser's own picture-in-picture window, opened from its context menu or button: panel hidden, page and thumbnail as without the player; there is no button of our own since 0.7.0). Below 900 px the panel is `position:sticky` at the top.
- **Controls:** clicks blur the button, so the keys (handled on `document` only while the player is open and the focus is not in a form control or on a button) act on the video: `Space` play/pause, `←` / `→` ±10 s, `Shift+←` ↺, `Ctrl+←` / `Ctrl+→` start / end (a key map built from `Ctrl+`, `Shift+` and the key). **↺** (`actions.repeat`): the start of the cue before the current one of the *source* transcript; since the jump lands on that cue's start, the next click goes one cue further back without any timer. More than 20 s back, before the second cue, or without a transcript: 5 s back. Media Session `seekbackward` / `seekforward` give ±10 s in the browser's picture-in-picture window. Closing pauses and exits picture-in-picture; the position is kept. *Pop out* was removed in 0.7.0 (unused, took much room); the `pip` state stays for the browser's own window.
- **Timestamps:** every link to a moment of the video is made by `time_link()` (also the times `linkify()` finds in the description, the AI summary and comments): `<a class="t" data-t="SECONDS" href="…&t=…s">`. Transcript cues carry their start as `data-c` (`cue_start()`): `.cue` rows, `.bi-row` rows, and in the continuous transcript one `<span data-c>` per cue (invisible). While the player is open, one click handler on `document` intercepts plain left clicks on `a.t[data-t]` and on continuous-transcript spans (not while text is being selected) and plays the local copy from there; nothing in the page is rewritten, so closing the player needs no restore. Modifier clicks keep the YouTube link. Open adds `title="Play from …"` to the links (removed on close) and CSS turns them `--accent-text`.
- **Lines under the video** (`.now-playing`, hidden while both *Subtitles* and *Chapters* are off): current chapter (from `#chapters li`, shown by *Chapters*), and by *Subtitles* the current cue of the *source* transcript with 0, 1 or 2 cues before and after (`subtitles` 1–3, cycled 0→1→2→3→0; the mode is shown as CSS dots in `.dots`, hollow for 1, filled for 2 and 3, drawn with borders so hollow and filled have the same size; lines `prev2`, `prev`, `current`, `translation`, `next`, `next2`) (the first `.with-timestamps` or `.bilingual` section, else the continuous one), and the translation chosen with the language buttons: the script adds one `button.lang` per bilingual section (`data-lang`, set by `render_bilingual_transcript()`), in page order, into `.langs` next to *Subtitles*. They are `disabled` while *Subtitles* is off; `setLanguage()` is a switch (another language replaces the active one, the active one turns it off); `setSubtitles(0)` resets the language. The translation is the second `.text` of that section at the same time. All lines are 18 px: current bold in the text colour, translation bold grey with a faint touch of the accent (`color-mix()` of 30 % `--accent-text` into `--muted`, weaker than the chapter, so the eye switches easily between original and translation; browsers without `color-mix()` keep the grey), the others grey; the chapter above them is small, in `--accent-text` at 75 % opacity, one line with an ellipsis, and keeps its line (`visibility:hidden`, class `off`) while *Chapters* is off, so the sentences never move; 16 px above it, 7 px below; no line separates them from the buttons. Buttons only when useful: no transcript, no *Follow*, *Subtitles* and language buttons; no chapters, no *Chapters*; neither, no third row. `indexAt()` is a binary search over the cue starts; the lines change only when the cue changes. A click scrolls the transcript on the right to the cue and flashes it (`found`): the followed transcript while Follow is on, otherwise the *source* one. The current chapter gets `now` in the chapter list while the player is open.
- **Follow:** `follow` is the index of the followed transcript section (−1 = off); the button cycles through them and shows `Follow N/M` when there are several; after the last one it is off (and starts again with the first). `lastFollow` keeps the one used last (in memory, until the page is reloaded), so after a scroll *Follow* starts there again. The third row is as high as the speeds; *Follow*, *Chapters* and *Subtitles* are 116 px wide in every state. The current cue of that section gets `now` and is scrolled into view (`scrollIntoView`, smooth) when it changes. A `scroll` event that is not ours (`autoScroll`, cleared on `scrollend` or after 1.5 s) ends Follow, as does closing the player.
- Checked in Chromium (06.11.2026): page and video with the same name, renamed page with the video under yt-dlp's name, no video (no tile, no errors), names with `#` and Polish letters; `#t=` media fragments work only when a file is opened fresh, which is why the script handles timestamps. Checked 07.11.2026: timestamps in chapters, description, comments and the continuous transcript, the lines under the video with a translation, Follow cycling through three transcripts and ending on a manual scroll.

## About this copy

The folded `<details class="copy-info">` at the bottom of every page (it replaced the footer). Folded, it shows `About this copy · Snapshot DD.MM.YYYY · made with yt2html`, the same for every copy; opened, only `About this copy` stays (CSS rule for `details[open]`, no script). Built by `render_about()` from one function per row (`about_*()`).

Rows describe the page as it is, as if it had been made in one go with the Command row's command: a rebuild shows up only in *Page built* (and in Command), never as "downloaded N, shown M". Rules: it describes what makes **this** copy differ from another copy of the same video (settings, moment, failures), and only failures that did not stop the page; **every row is always there**, in a fixed order, one line each, with a short comment when there is nothing to report. Fixed words for states: *left out* (by an option), *none* (the source has none), *could not be read / downloaded* (an error), *may lack* (unknown whether it existed). The Command row is the one deliberate exception to "no option names on the page".

| Row | Content and source |
|---|---|
| Downloaded | time of the download (`epoch`, written by yt-dlp; local time), link to the video, yt2html version at download (`yt2html_download`) and yt-dlp version (`_version`, written by yt-dlp) |
| Page built | `at download`, or with `--from-json` the build time and the yt2html version that built it |
| Playlist | the playlist the video was archived from (`yt2html_playlist`: id, title, position and count on the download day, set by `playlist_context()`); never list files or other playlists; `video N of M in TITLE`, or `none` |
| Command | `about_command()`: the command that makes an equivalent copy today. Each option comes from the step where it acts: download (`--cookies-from-browser` with the browser name only; always the full name, also when `--cookies`, its short name, was used), build (`--no-avatars`, `--theme`, `--no-snapshot`), both, the narrower one winning (`--transcript`: the build's value, or `none` if nothing was downloaded; `--no-cards`: if in either step, i.e. the cards key is absent; `--no-comments` and `--max-comments`: a rebuild may narrow the saved comments, see `narrow_saved_comments()`). Defaults are left out; options without effect on the page (`-o`, `-a`, `--overwrite`, `--index`, `--save-json`, `--from-json`) never appear. The video is given by its id; an id starting with `-` would be taken for an option (and PowerShell 5.1 may drop a `--`), so then the full address |
| Video page | `yt2html_watch_page`: `read`, `partial` (without `ytInitialPlayerResponse`) or `failed`, with what an unread page may cost |
| Markers | (`about_markers()`; was *Chapters* before 0.7.1, renamed because the row covers every timestamp list) chapters and the other lists, each named in lower case (like every row) with its count in brackets: `chapters (47), from the description`, `walkthroughs (11)`; without chapters `no chapters` before the other lists, or on a read page with at least 3 description lines with a time (`CLOCK_RE`) a note that YouTube made no chapters from them (deliberately simple: no check of YouTube's rules); `none` when the page has no list at all; an unread page gives `origin unknown` or `none in the yt-dlp data` |
| Info cards | `left out` (key absent), `unknown` (`None`, page unread), `none`, else `N cards: ` and the kinds in a fixed order (video, playlist, channel, link; the order of `CARD_TYPES`), links with `(all to their websites)` or `(N kept as a YouTube redirect)` (a link whose URL is still `youtube.com/redirect`); `; times not shown` when no card has a time |
| Transcript | `Language (features); N versions: …`: semicolons separate parts, commas list items, parentheses hold features. Versions are the sections shown (the values of `--transcript`), each `bilingual with X (auto-translated)` meaning a downloaded translation; translations kept in a JSON file but not shown are not mentioned. A missing transcript gives the reason kept in `yt2html_transcript_missing`; `original` in the transcript is false when the original language was unknown or differs |
| Comments | from `comment_status()`: downloaded of YouTube's count, threads and replies, `complete` / `limited to N` (by an option, also when the saved comments were cut at a rebuild) / `interrupted (Ctrl+C)`; `no comments`, `turned off or unavailable`, `left out` |
| Images | thumbnail size read from the embedded image (`image_size()`: JPEG, PNG, WebP, GIF), the channel avatar on its own (kept with `--no-avatars`), comment avatars embedded of those wanted (`comment_avatar_urls()`) or `left out`, other images (people, podcast, cards); counted at build time, since the build downloads the images |
| Software, Disclaimer | the same for every copy: licenses of yt2html and yt-dlp (not of the content), rights of the authors, personal use, no warranty |
| Video (experimental) | only with `--video` (a build option, so it works with `--from-json`; it appears in Command); `video_overview()` gathers the data, `about_video()` renders it as three small tables (`formats_table()`: image heights × codecs, audio languages × codecs with the original first, subtitles), then two lines (planned default, Deno); a temporary exception to "one line", to see what `--video` should offer. Size columns are right-aligned and never wrap (`class="size"`), the rest wraps; a table wider than the page scrolls sideways (`.formats`, `dd{min-width:0}`); an empty cell is a dash. From `formats` (`video_formats()`): video-only and audio-only formats without DRC variants; combined formats only when there is nothing else (storyboards drop out, having neither codec). **image:** per height, descending, the best format of each codec (`codec_name()`: avc1→h264, av01→av1, vp9/vp09→vp9, hev1/hvc1→h265; best = highest fps, then size), fps above 30, a dynamic range other than SDR, size with the main audio. Formats for Premium accounts (`is_premium()`: "Premium" in yt-dlp's format note; only when signed in with `--cookies-from-browser`) get their own columns, marked `Premium`, after the ordinary column of the same codec (also in the audio table); the main audio and the planned default never use them. **HLS** formats (`is_hls()`: protocol `m3u8…`, a playlist of short pieces) have no size, only a declared bitrate, which proved far too high (in a test 3262 kbit/s against 276 kbit/s for the same 1080p h264 as one DASH file); they are only named, per height, in a last column *HLS (sizes unknown)* (`h264, vp9, vp9 Premium`), and never used for sizes, the main audio (unless there is no other audio) or the planned default. Signed in (`--cookies-from-browser`), yt-dlp gets no HLS formats at all. **audio:** per language, `(original)` for `language_preference == 10` and `(dubbed)` for the rest when an original is known, aac and opus sizes; the main audio is the original language's aac. **subtitles:** by the author (`subtitles` without `live_chat`), auto-generated (`-orig` tracks), auto-translated (other automatic tracks minus the originals). **planned default:** the highest h264 with the main audio, mp4. A last line always says whether yt-dlp had Deno at download (`yt2html_download.deno`): `yes`, `no` (yt-dlp may then get fewer formats) or `unknown` (data saved before the field existed). Sizes (`format_size()`): `filesize`, else `filesize_approx`, else `tbr` × duration; `human_size()`: `~X.X GB` from 1 GB, else `~N MB` |

## Page metadata and the index (`--index`, `--index-only`)

**Metadata.** Every video page has invisible `<meta name="yt2html:...">` tags in its `<head>`, before the large `<style>` (`page_metadata()`): `video-id`, `title` (the original title), `archived` (ISO date), `comments-status`, `comments-downloaded`, `comments-youtube` (the text of the Comments tile). `comment_status()`: `not-downloaded` (no `comments` key: `--no-comments`), `turned-off` (`comments` is `None`), `interrupted` (`comments_interrupted()`), `limited` (`--max-comments` reached while YouTube shows more), otherwise `complete`. A failed comment download fails the whole video, so it never produces a page. `read_page_metadata()` reads only the first 16 KB of a page.

**Building an index.** `write_indexes()` runs at the end of `main()` (also after `Ctrl+C`; with `--index-only` instead of archiving) for each playlist URL given in the command and each `-a` file (`args.command_sources`, `args.batch_lines`, kept by `collect_sources()`). Nothing is taken from a log; an index is built from:

- the list: a playlist's flat entries read in this run (`Run.playlists`, filled by `read_playlist()`), else the list kept in the earlier index; for a list file its lines (`list_file_sections()`: one section per playlist line, one per run of other lines; a video listed twice appears once);
- the pages in the output folder, found by `existing_page()`, and their metadata (title, date, comments);
- `Run.attempted` and `Run.failures` (video id → `failure_reason()`, e.g. `Sign in to confirm your age`).

`entry_status()`: `archived` (a page exists), `not-a-video` (no video id), `unavailable` (playlist entry titled `[Private video]` / `[Deleted video]` or `availability: private`), `failed` (failed in this run), else the earlier index's `failed`/`unavailable` with its reason if the video was not tried in this run (`with_earlier_status()` merges them into freshly read lists), else `pending` (`Not archived yet`).

**Keeping the list.** The index stores what `--index-only` needs as `data-*` attributes (no JavaScript): `<section data-section data-playlist-id data-title data-url data-fetched data-unread>` and `<tr data-id data-url data-title data-status data-reason>`. `read_index()` (an `html.parser.HTMLParser`) reads them back. With `--index-only` a playlist without an earlier index is read with `fetch_playlist()` (only the list); an unreadable playlist in a list file becomes a section marked `not read yet`.

**Page.** `render_index()`: `<main class="page index">` (max. 960 px), title (playlist title linking to YouTube, or the list file's name), grey `N of M videos archived · Updated DATE`, one `<table class="index-table">` per section without visible lines: position (playlist rows by position in the playlist, other rows numbered across the list file), title (link `urllib.parse.quote(page.name)`, so `#`, spaces and Unicode work; grey without a page) with the comments line (`comments_summary()`), date or grey reason (`title` attribute with the failure message), YouTube link. Same CSS and themes as video pages. File names: playlist `output_path({"title", "id": PLAYLIST_ID})`, i.e. `Title [PLAYLIST_ID].html`, or the existing file ending with `[PLAYLIST_ID].html`; list file `STEM.html`. Written atomically.

## Testing

**Run the tests after every change**, in the project folder:

```powershell
uv run pytest
```

The first run installs pytest into the project's `.venv` (the `dev` dependency group in `pyproject.toml`; it is not part of the installed program). A run takes about a second, needs no network and ends with a line such as `347 passed, 7 skipped`. `uv run pytest -q` prints less, `uv run pytest -x` stops at the first failure, `uv run pytest tests/test_chapters.py` or `-k bilingual` runs a part.

**What is covered (offline):**

| File | Area |
|---|---|
| `test_formatting.py` | numbers (`1.23M`), durations, dates, file names, `linkify()` (URLs, timestamps, hashtags, handles, e-mails, HTML escaping) |
| `test_comments.py` | comment order (oldest first, pinned, equal estimated times, nested replies), avatars embedded once, the comments note, `No comments.` rules |
| `test_chapters.py` | description chapter check, chapter origin (description / auto / uncertain), Walkthroughs, fallback to yt-dlp chapters |
| `test_sections.py` | summary, People mentioned (only web links clickable), podcast, license, channel avatar (watch page, comment fallback, small download, `--no-avatars`), comment count, Snapshot tile, section order, themes, no fixed colours outside the CSS variables, player tile, panel, script and original video name, `data-t` on every timestamp, `data-c` on transcript cues, player buttons (↺, no Pop out), keys, ↺ rule, Follow cycle, Subtitles cycle with dots, Chapters toggle with a reserved line, channel avatar kept with `--no-avatars` |
| `test_transcript.py` | choosing the caption track (incl. auto-dubbed videos), json3 captions, translations (alignment, HTTP 429 retries, errors), `--transcript` parsing and rendering |
| `test_cli.py` | `--version`, defaults, `--from-json` (also patterns with literal brackets, in `-a` files, ids and page names looked up in `-o`, the error messages, an existing file winning), saving in the current folder, `-o`, bilingual errors save nothing, self-contained page, one failure does not stop the others |
| `test_existing.py` | video id from all URL forms, recognising (renamed) pages (id anywhere in the name, any case of the extension, a note for several matches), ids in file names as sources (last valid id, folders ignored), skipping without fetching, `--overwrite` keeping the name, `--from-json` replacing, interrupted writes, channel URLs rejected without fetching |
| `test_cards.py` | info cards of every kind, pairing with times only when the counts match, sorting, messages, placement after the chapters, images, resolving link cards (and keeping the redirect when that fails), `--no-cards` reading and requesting nothing and hiding saved cards |
| `test_playlist.py` | playlist and channel URLs, expanding in order, duplicates (in a playlist, across playlists, separate URLs), skipping and rerunning, failed videos and playlists, `Ctrl+C` stopping the run, empty playlists, playlists in batch files, `fetch_playlist()` reading only the list |
| `test_index.py` | page metadata and comment status, comments line, playlist and list file indexes (renamed pages and index, original titles, reasons and their survival, `Ctrl+C`, numbering, unreadable playlists), `--index-only` without downloads, option errors |
| `test_about.py` | *About this copy*: folded and open summary, every row always there, each state of each row, the Command rules (defaults, both steps, an id starting with `-`, `--from-json`), rows describing the page and not the saved data, comments narrowed at a rebuild (first N, after `Ctrl+C`, no widening, `--no-comments`), playlist context, download settings (also whether Deno was there), the `--cookies-from-browser` text split into yt-dlp's tuple (`cookies_spec()`) and checked when the command is read, transcript reasons and original language, image sizes, no option names outside the Command row, the experimental *Video* row (only with `--video`, image, audio, subtitles, planned default, size sources, Premium formats marked and never in the default, HLS formats named without sizes, the Deno line in its three states, with `--from-json`) |
| `test_batch.py` | `-a` files: comments, blank lines, byte order mark, order, duplicates in other URL forms, unusable files stop before fetching, bad lines fail alone, rerunning a list, the file is not changed, `--from-json` paths |

The offline tests use only synthetic data shaped like the real structures (`conftest.py`): builders such as `marker_panel()`, `player_bar()`, `people_item()`, a full `info` fixture, and `FakeYDL`, which answers caption and translation requests (also with HTTP 429 or empty answers). Image downloads and `time.sleep` are switched off for them, so no test waits or touches the network.

**Online checks** (`test_online.py`, marked `online`, skipped by default) download the reference videos and check what the program reads from YouTube's pages: description chapters, AI summary, podcast, the original-language transcript of an auto-dubbed video and the data behind *About this copy* (`epoch`, yt-dlp's `_version`, the video page status, the thumbnail size; `Z8lf5oWKfj0`), auto chapters (`qyqCTMirNWg`), Walkthroughs and a Polish transcript (`hz6-3-7GGyI`), People mentioned (`9uSXOr-AdAU`), license (`KZtvWKpXPro`), info cards and the channel avatar (`EBQzfbqFKN0`), resolved link cards (`dkh1A_mAhY8`):

```powershell
uv run pytest --online -m online
```

They take a minute or two. Run them now and then, and whenever real pages look wrong: a failure usually means YouTube changed its page data or yt-dlp needs an update. If a reference video disappears or changes, replace it in `test_online.py`.

**When changing the program:** add a test for each new feature or fixed bug (usually a new builder in `conftest.py` with data copied from the real page, then a test of the parser and of the rendered page). `--from-json` with a file saved by `--save-json` remains handy for checking the look of a page in a browser.
