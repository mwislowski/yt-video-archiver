# Changelog

All notable changes to this project. Versions: the second number for new features or visible changes, the third for fixes (see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#version-packaging-releases)).

## [Unreleased]

### Changed
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

[Unreleased]: https://github.com/mwislowski/yt-video-archiver/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mwislowski/yt-video-archiver/releases/tag/v0.1.0
