# Changelog

All notable changes to this project. Versions: the second number for new features or visible changes, the third for fixes (see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#version-packaging-releases)).

## [Unreleased]

### Added
- A player for a downloaded copy of the video: if a video file with the page's name (or yt-dlp's original name) lies next to the page, a turquoise *Play* tile opens a player pinned on the left while the page scrolls on the right. Buttons for start (stops the video), ±10 seconds, play/pause, end, a separate window (*Pop out*) and speeds 0.5×–1.5×; keys `Space`, `←`, `→`. The video is found each time the page is opened, so it may be downloaded later.
- While the player is open, timestamps play the local copy instead of opening YouTube: in chapters and other lists, info cards, transcripts, the description, the AI summary and comments; in the continuous transcript a click on a sentence. *Subtitles* shows under the video the current chapter and the current sentence with the ones before and after it; small language buttons (one per bilingual transcript) add a translation. *Follow* marks the current sentence in a transcript on the page and keeps it in view (pressed again: the next transcript; after *Stop following* or scrolling, which end it, it starts again with the transcript used last).
- Speaker changes in YouTube's automatic captions (`>>`) are shown as a grey mark in the transcripts (a grey ` | ` in the continuous one) and left out in the lines under the video.
- The author's info cards (the "i" teasers linking to a video, playlist, channel or website) in a section after the chapters: time, image, title linking to the target, kind, details and the author's message. Link cards point to the real website instead of YouTube's one-time redirect, and show its domain. `--no-cards` leaves them out without reading or requesting anything for them.

### Changed
- Pages contain one small built-in script (for the player); without it they work as before.
- The channel avatar is taken from the video page, so it shows also when the author has not commented under the video (before, it came only from the author's comments, which remain the fallback).

## [0.5.0] - 2026-11-06

### Added
- Playlists: a playlist URL (`/playlist?list=...`), in the command or in a list file, archives all its videos in playlist order. Videos with a page are skipped, a video in the playlist twice or also given separately is archived once, and a video that fails (e.g. private) does not stop the others.
- `--index`: an index page for each playlist and list file: every video with its position, original title linking to its page (also after renaming pages), date, link to YouTube and a line about its comments (complete, limited or interrupted), and the reason for videos without a page (unavailable, failed, not archived yet). Made at the end of every run, also after `Ctrl+C`.
- `--index-only` rebuilds the index pages from the pages in the folder without downloading anything.
- A video or playlist can also be given by the name of its page or index, with or without `.html` (`"Title [VIDEO_ID].html"`, `"Title [PLAYLIST_ID]"`), in the command and in list files.
- Video pages keep their id, original title, date and comment download status as invisible `<meta>` data, read by the index.

### Changed
- Channel URLs fail with a hint to use a playlist URL instead.
- `Ctrl+C` stops a run with several videos: the page of the current video is still made from the comments fetched so far, then the summary is shown (before, only the comments of that video were stopped and the run went on with the next video).
- The channel name next to its avatar is bold, as on YouTube.
- Comment authors, names in People mentioned and timestamps in chapters and the transcript are semibold; before, they looked like plain text on Windows, whose Segoe UI has no medium weight.

## [0.4.0] - 2026-11-06

### Added
- `-a FILE` / `--batch-file FILE` reads URLs from a text file, one per line; blank lines and lines starting with `#` are ignored. Can be given several times and combined with URLs in the command. A video listed more than once is archived once.

### Changed
- Documentation split into `README.md` (for users), `docs/DEVELOPMENT.md` (how the program works, tests, releases) and this changelog.

### Fixed
- A playlist or channel URL fails at once, without requests. Before, yt-dlp first extracted every video of the playlist, comments included, only for the URL to be rejected afterwards.

## [0.3.0] - 2026-11-06

### Added
- Videos that already have a page in the output folder are skipped, so running the same command again only archives what is missing. A page is recognised by `[VIDEO_ID].html` at the end of its file name, so pages may be renamed.
- `--overwrite` makes a page again and keeps its current file name.
- Automated tests (`uv run pytest`), including optional checks against real YouTube pages (`--online`).

### Changed
- `--from-json` always replaces the page.
- Pages are written atomically: an interrupted run never leaves a cut-off page.
- The summary after several URLs counts skipped videos: `Done: X saved, Y skipped (page exists), Z failed.`

## [0.2.0] - 2026-11-05

### Added
- Installation as the command `yt2html` (`uv tool install git+https://github.com/mwislowski/yt-video-archiver`).
- `--version` shows the program and yt-dlp versions.
- MIT license.

### Changed
- Pages are saved in the current folder by default (before: an `output` folder); `-o FOLDER` saves them elsewhere.

## [0.1.0] - 2026-11-05

First release.

- One self-contained offline HTML page per video: thumbnail, title, channel, facts with the grey Snapshot date, full description with clickable links, AI-generated video summary, chapters with their origin, Walkthroughs and other timestamp lists, People mentioned, the podcast the video belongs to, transcript, license, all comments with expanded threads (oldest first).
- Transcript versions with `--transcript`: `plain`, `timestamps`, `bilingual:LANG` (YouTube's translation in a second column), `none`.
- Themes with `--theme`: `auto` (follows the device), `dark`, `light`.
- `--save-json` and `--from-json` to keep the data and rebuild pages without downloading.

[Unreleased]: https://github.com/mwislowski/yt-video-archiver/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mwislowski/yt-video-archiver/releases/tag/v0.1.0
