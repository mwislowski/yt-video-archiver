# Changelog

All notable changes to this project. Versions: the second number for new features or visible changes, the third for fixes (see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#version-packaging-releases)).

## [Unreleased]

### Changed

- **The page's options are one: `--page`**, with comma-separated parameters: `ovr` (make the page again even if the video has one; was `--overwrite`), `comments:no|0|N|all` (was `--no-comments` and `--max-comments N`), `snap:yes|no` (was `--no-snapshot`), `cards:yes|no` (was `--no-cards`), `avatars:yes|no` (was `--no-avatars`) and `theme:auto|light|dark` (was `--theme`), e.g. `--page ovr,comments:200,theme:dark`. The old options are gone, not kept as other names. `--page` is given once (a second one is an error, not a silent replacement); `--page ovr` with `--load-json`, `--update-json` or `--refresh-json` is an error (everything from YouTube against the saved data); `--index-only` and `--video-formats` refuse `--page`. The *Command* row of new pages writes `--page` with what differs from its defaults.
- **The saved data's options are one: `--json`**, with one parameter: `write` (was `--write-json` and `--update-json`), `refresh` (was `--refresh-json`), `load` (was `--load-json`) and `merge` (was `--merge-json`); the old options are gone. `--json write` saves the data with a new page; where page and data are there it adds only what is missing (as `--update-json` did), and with nothing missing it leaves the page and the data as they are (with `--video`, unless files are added); data without a page get the page from the data and what is missing (no longer downloaded again over the saved data). With `--page ovr` everything is downloaded again and replaces the saved data in their file, and the console names what only they had (translations of one's own, comments no longer on YouTube, YouTube's translations, the record of downloaded files). `--json merge` only merges the translation and makes no page (with `--transcript` or `--page` it is an error; the console gives the `--json load` command that shows it). Without `--json` saved data are not read at all: a page made again describes the files next to it as found there.
- **Transcript versions renamed and in any language:** `plain`, `timed` (was `timestamps`) and `dual:CODE` (was `bilingual:CODE`); every version may take a language code (`plain:pl`, `timed:de`: the translation on its own), a version without one is the original, also later in the list (`plain,timed` gives both in each video's own language on a playlist of several). A code in the original's language is the original itself: `dual:en` of an English video is shown as `timed`, and a version that turns out the same as an earlier one is shown once, both with a line in the console and in the *Transcript* row. The change of speaker (`>>`) of the original goes into every translation that lost it, in every version. The lines under the video always come from the original (a page with only translations has the original hidden for them); a language button for every translation on the page, once per language. `--transcript ovr` (with `--json write` or `--json refresh`) downloads the saved transcript again; translations of one's own that still fit it are kept, the others dropped with a message.
- **`--olang CODE`:** the video's original language, when you know it better than YouTube shows it: the transcript is in it (none, if it has no captions in it), the video's main audio and the *Languages* table follow it, and the page no longer warns that the original is uncertain; the warning itself now names `--olang`. With saved data it is recorded (a transcript in another language stays until `--transcript ovr`, with a warning); the *Command* row writes it.
- **Subtitles as SRT files come from the transcript: `--transcript srt`** (was `--video sub:CODE`, gone). Each version the page shows gives its track once, next to the page: the original (`Title [ID].en.auto-generated.srt` or `.en.author.srt`; `dual:CODE` brings it too) and YouTube's translations (`.pl.translated.srt`); a translation of one's own is not written (its `.own.srt` file is the source). The files are made with the page and from its data, so `--json load` makes them without YouTube; one already there (also renamed, by the id and its ending) is written over only when its text differs. They have the transcript's lines and times (a line break inside a line is a space). A page that is skipped makes none (the console says so); with `--json write` a missing SRT file counts as missing. The *Transcript* row names them.
- **`--video`: `ovr` instead of `again`**; only the video and its audio tracks (quality, `audio:CODE`, `ovr`). When it adds files to a page that is left as it is, the console warns that the page's *Video file* row does not list them (and the player has no switch for a new audio track) and how to add them.
- **A video's page, data and files stay in one folder:** a file given by its name (a page, saved data, an SRT file, a pattern) is taken in the output folder (`-o`), one given with a path elsewhere is an error before anything is read or written (it was read where it was while the page went to `-o`, so a second page could appear). Saved data are only files ending in `.json`.
- **Comments: at most 1,000 by default** (was all of them); `--page comments:all` takes all. With saved data only a limit given on purpose narrows the saved comments; `--refresh-json` says when the default limit cut the refreshed ones.
- **The index's theme comes from the list file**, `!theme: auto|light|dark` (default auto), no longer from the command; `--page theme:` sets the video pages' theme only. Settings of a list file may also stand several on a line (`!numbers: no !layout: cards`), as the `=` details do.

### Fixed

- The podcast a video belongs to shows again when YouTube uses its newer podcast card under the description (seen since 23.11.2026) instead of the "Explore the podcast" section: the section is headed "Podcast" and shows the podcast's title, episode count and square cover, linked to its playlist; the card names no author.
- The index of a list file spaces its headings evenly: the same gap under the list's title and under every chapter, whether a note or a video comes next (a chapter's note sat nearly glued to its heading, and the first video or note to the title), and with the player open the title's top is level with the player's.

## [0.10.8] - 2026-11-23

### Added

- `--merge-json` merges a translation of your own into the saved data, for when YouTube refuses its machine translations (`HTTP 429`): translate the subtitles in the video's original language yourself and give the SRT file, named `Title [id].pl.own.srt`. It must have the transcript's lines (the same number, each at the same moment); nothing is asked of YouTube. With `--transcript plain,bilingual:pl` the page is made from the data at once and shows the translation in the second column, headed `Polish, own translation`; *About this copy* names it in the *Transcript* row, *Video formats* lists it in yellow under the languages table, and the *Command* row leaves it out. An own translation can be replaced by a corrected one; YouTube's is never replaced.

### Fixed

- Videos with YouTube's automatic dubbing: when YouTube sent no audio tracks (for example after asking to confirm you're not a bot), the transcript, the page's language and the index row took the language of a dub. The original language is now also read from the video's YouTube page, which names the original audio track; when it still cannot be told, the page says the original language is uncertain and the console warns, with the advice to make the page again later (with `--cookies` if YouTube asked to confirm you're not a bot).

## [0.10.7] - 2026-11-22

### Added

- In a list file, details of your own for the video or file below, on `=` lines like its title: `= !date: 03.08.2019` and `= !lang: pl`, also together (`= !date: 2019 !lang: pl`). They replace the publication date and language from the page and give your own files, and videos without a page, a date and a language in the index. Dates: `DD.MM.YYYY`, `DD.MM.YY`, `MM.YYYY` or `YYYY`.
- In the notes of a list file, `((text))` shows the text in grey, the grey the index uses for what the program adds; it goes with bold, italic and links.

### Changed

- In the index of a list file, a playlist's description from YouTube shows each of its lines as a paragraph, as the notes do, without its blank lines and the spaces at the ends of lines (it was one block with the blank lines kept). Make existing indexes again with `--index-only`.

### Fixed

- On Windows, with the output sent to a file or a pipe (`> log.txt`, `| Tee-Object`), a title with a character the system's code page lacks (often `｜` or `：`, which yt-dlp puts in file names for `|` and `:`) stopped the video after saving its page (`'charmap' codec can't encode …`), so `--video` was not reached. Such characters now show as `?` in the output; the files keep their names.

## [0.10.6] - 2026-11-21

### Added

- In the index of a list file, a video's publication date opens the line under its title (`03.05.2024 · pl · MP4 · YouTube`), when its page is downloaded. A new setting, `!dates: no`, leaves out the dates, in the rows and in the playlists' notes.

### Changed

- The first line of a playlist's notes in the index reads *Playlist* and its title, the number of videos, *Channel* and its name (was: title · Playlist · channel · count). After the number, in grey, the span of the videos' publication dates, oldest to newest, as the downloaded pages tell them: `(03.05.2024–12.11.2026)`, one date for one day, `(known range …)` or `(known …)` when some videos are not downloaded. Make existing indexes again with `--index-only`.

## [0.10.5] - 2026-11-21

### Fixed

- A video nobody has liked yet shows `0` likes instead of `—` (yt-dlp gives no count then; the program takes it from the video page). `—` stays for a like count the author hid (and when the video page could not be read). Pages get it when made again with a download (`--overwrite`) or after `--refresh-json`.
- In the index of a list file, videos right under the title start at the title's edge, and a chapter's videos are indented by exactly the indent (20 px); a leftover 8 px padding pushed every row to the right. Make existing indexes again with `--index-only`.

## [0.10.4] - 2026-11-20

### Changed

- Links in the notes of a list file are written in simplified Markdown, `[text](https://address)`, and `[](https://address)` shows the address itself; a bare address is now plain text. A playlist's description from YouTube keeps its addresses as links.

## [0.10.3] - 2026-11-20

### Fixed

- The cover in *Explore this course* (and other sections of that kind) is no longer cut to a square: 160 px wide, as tall as its shape (a course's 16:9, a podcast's square).
- The transcript of a video with an automatic dub could be the dub's captions instead of the original's (a Polish video got an English transcript labelled as its original language), because YouTube named the dub's captions the default for yt-dlp's English request. The audio marked as the original now decides. A page that already has the dub's transcript gets the right one when made again with the transcript downloaded again (`--overwrite --transcript`); saved data keep the transcript they hold.

### Changed

- The pinned comment stands first, as on YouTube, instead of in its place in time (often last); the rest stay oldest first. A pinned comment YouTube no longer sends (after `--refresh-json`) keeps its place in time and its mark. Pages made again (also from saved data, `--load-json`) get the new order.
- `--video` with `audio:` or `sub:` no longer asks YouTube for the list of formats, nor waits before the next video, when the page is left as it is and every file asked for is already next to it, so running the same command again to fill the gaps of a playlist asks only about what is missing. Subtitles of a kind the video got later are then not looked for (`again` or `sub:CODE.KIND` asks).

## [0.10.2] - 2026-11-19

### Changed

- The numbers in the index stand before the titles instead of in a column of their own, so an index with numbers is laid out as one without them; a long title wraps under its first letter, with the number left of it. The notes of a chapter stand at its indent (its videos and chapters one step in), and the indent is a little smaller (20 px).

## [0.10.1] - 2026-11-19

### Added

- Three layouts for the index, chosen in the list file: `!layout: plain` (the default: a video's note under its thumbnail, across the whole width), `!layout: compact` (beside the thumbnail, under the title) and `!layout: cards` (the title, its line and the note in one rounded frame beside the thumbnail, for every row). The notes of chapters and the list follow the layout: without frames in `plain` and `compact`, framed in `cards`.

### Changed

- The settings of a list file are written `!key: value`: `!numbers: no` and `!thumbnails: no` instead of `!no-numbers` and `!no-thumbnails` (both `yes` by default; any case, the space after `:` optional). An unknown setting or value stops the run and names the allowed ones; the old forms are now unknown settings.
- The row playing in the index is marked by a ring round its thumbnail and its title in the accent colour, no longer by a background over the whole row (which also lit up its notes).

## [0.10.0] - 2026-11-19

### Changed

- The links to channels go to the channel's lasting address (`/channel/UC…`), not to its handle (`/@name`): the channel under the video, the channel of a playlist in the index and the authors of comments. A handle can be changed by its owner and, 14 days later, taken by someone else, so in an old copy it could lead to another channel. Without the id (older data) the handle stays. Pages get the new links when they are made again, e.g. with `--load-json`; the ids are already in the saved data.

## [0.9.4] - 2026-11-19

### Added

- The index has its own **player**: a panel on the left, always there, built like the player of a video's page. A thumbnail (or the round button with `!no-thumbnails`) plays its file; one row of eight equal buttons (previous file, to the start, 10 s back, play/pause, 10 s forward, to the end, next file, *Autoplay*), the speeds and the title of the file playing; greyed until the first play. The previous and next file (also `Shift`+`P` / `Shift`+`N`) skip rows without a file and keep playing, or wait at the start when the player was stopped; *Autoplay* plays the next file when one ends. A row out of sight scrolls into view. A file that cannot be played says so on the screen, its thumbnail dims and it is skipped until the page is reloaded. Without JavaScript the index stays a plain list.
- A new index, made from a **list file**: `--index FILE` archives what the list names and the folder lacks, then makes the index page (`series.txt` → `series.html`, in the output folder); `--index-only FILE` only makes the index, downloading nothing. The list is plain text close to Markdown: videos (addresses, ids, page or video file names with `[VIDEO_ID]`), playlists, video files of your own (`mp4`, `webm`, `m4v`, `mkv`), a title (`# Title`), chapters on three levels (`##` to `####`), notes (`> text`, with `**bold**`, `*italic*` and links), a title of your own above a line (`= Title`), comments (`// text`) and the settings `!no-numbers` and `!no-thumbnails`. Every line is checked before anything is downloaded; a line that is not understood stops the run with its number. A playlist that is the only element of its level puts its videos in its place, next to others it becomes a chapter; it gets YouTube's title, channel, count and description as notes. The index is always built from what is in the folder (pages, only their metadata, and video files), so `--index`, `--index-only` and `--load-json` give the same result: dense rows with the thumbnail and duration, the title linking to the page, the language, the file's format and *YouTube*; what is missing is said in the row (*only the page downloaded*, *not downloaded*, *file not found*, *no longer on YouTube*) and in the console with the list's line. Playlists are read from YouTube each time (only their list); the index keeps their lists for when YouTube cannot be reached. The duration of your own files is read from their headers (no ffmpeg). The new module `yt2html_index.py` holds the index. README: *List files and their index*.
- `--refresh-json` records a video that is no longer on YouTube (removed, its account terminated, made private), instead of refreshing it: the saved data stay as they were, with YouTube's status and words added, and the page is made again from them. The *Refreshed* tile shows the day of the check in a muted red (also on the copy's day), *Downloaded* names each check (`no longer on YouTube 03.12.2026 14:05`) with YouTube's words after the last one, and the page's metadata note it for the index (`yt2html:unavailable`). When the video is back, the next refresh is an ordinary one. Other refusals (`not a bot`, `try again later`) fail the video and change nothing.
- Pages carry more metadata for the coming index, invisibly in their header: the time of the download (`yt2html:archived` now with the time, e.g. `2026-11-17T14:05`), the last refresh, the publication day, the duration in seconds, the original language and YouTube's small cover (320×180, embedded; about 15–20 KB more per page). A value that is not known is left out, never written empty; `yt2html:metadata` (`2`) marks pages with this set. A cover YouTube no longer gives is kept from the page being made again.

### Changed

- `--index` and `--index-only` take the list file (`--index FILE`) and work only with it: a URL in the same command is an error, and so is `--index-only` with an option that downloads or shapes the pages (`--video`, `--transcript`, the JSON options, `--overwrite`, `--no-comments`, `--max-comments`, `--no-cards`, `--no-avatars`, `--no-snapshot`) instead of being ignored. `--index` now works with `--load-json` (pages made again from saved data, a video without saved data skipped with a note) as with `--update-json` and `--refresh-json`.
- Removed: `-a` / `--batch-file` (a list file is archived with `--index FILE`, which always makes its index), the index of a playlist given in the command (put the playlist into a list file) and the old index with its remembered reasons (why a download failed is only in the console). In a list file `#` no longer starts a comment: comments start with `//`.

### Fixed

- A private or removed video (also in a playlist) no longer gives an empty page titled `youtube video #ID` (yt-dlp only warns about it): it fails with YouTube's words, e.g. `FAILED: ID: Video unavailable. This video is no longer available because the YouTube account associated with this video has been terminated.`, and nothing is saved. Before, `--refresh-json` of such a video would also have replaced the saved title, cleared the timestamp lists and marked all comments as no longer on YouTube.
- A page made again keeps its thumbnail when YouTube no longer gives one (its thumbnails go with a video that is gone), instead of showing *Thumbnail not available*.
- With saved comments that are limited or interrupted, the note at a rebuild says that `--refresh-json` without `--max-comments` adds the rest; it told to download again without `--load-json`, also when the command had no `--load-json`. README: more comments come with `--refresh-json`, not `--update-json`.
- The placeholder of an avatar shows the first letter or digit of the name, no longer a sign such as `(` or `@`; an unknown channel or comment author gets a placeholder without a letter.

## [0.9.3] - 2026-11-15

### Fixed

- `--update-json` or `--refresh-json` with `--no-cards` and `--video` removed the info cards from the saved data (the data are saved again after the files); `--no-cards` now leaves out the cards only on the page, like `--max-comments` and `--no-comments`, and their images are not downloaded.
- Saved data (`.info.json`) and SRT files are written like pages, through a temporary file: an interruption while saving can no longer leave a cut-off data file (with `--update-json` and `--refresh-json` the only copy) or a cut-off subtitles file that the next run would skip as existing.
- `--update-json` with saved data outside the output folder: the files already next to the page lost their download details (day, variant) in the data and the *Video file* row; they now come from the data that were read.
- `--update-json` or `--refresh-json` with `--video` and a saved data file as the source (a path or a pattern) asked yt-dlp for the formats of the file name instead of the video; the formats are now fetched by the video's id.
- `--update-json` or `--refresh-json` with saved data files as sources (also through a list file): each file now counts as its video, so the same video given also as an address or in a playlist is done once, and the index of a list file shows such a file as its video (before: "Not a video").
- After `Ctrl+C` during `--refresh-json` the message advised `--overwrite`, which would replace the saved comments; it now advises `--refresh-json` again.

## [0.9.2] - 2026-11-15

### Added

- `--refresh-json`: as `--update-json`, and also refreshes what changes over time: the counts (views, likes, subscribers, YouTube's comment count), the title, the description and the timestamp lists (the earlier title and description kept in the data), and the comments, merged with the saved ones by their id. New comments are added, the others taken as they are now (an earlier text stays in the data), and those YouTube no longer sends stay with a grey tag `not on YouTube on DD.MM.YYYY` (only after a complete download of the comments; one that comes back loses it, the data keep when it was missing). Comments turned off on YouTube after the download stay too, with a note above them (`As of 03.12.2026, comments on this video are turned off on YouTube; the ones below can no longer be seen there.`). The comments line and *About this copy* count the missing ones; *Downloaded* names each refresh (`refreshed 03.12.2026 18:40`); a grey *Refreshed* tile with the last refresh stands next to *Snapshot* when it is another day. The copy's date stays.
- `--update-json`: adds to saved data what the options ask for and they lack, without downloading them again: the transcript, translations (also those YouTube refused before; one it does not offer is not asked for again), and the files of `--video`, which are then recorded in the data (*Video file* row, yellow marks, extra audio switches). YouTube is asked only for the video's data without comments and for what is missing, nothing at all when nothing is. The comments, counts and the copy's date stay; *About this copy* adds the run to *Downloaded* (`updated 20.11.2026 10:12`; what it added shows in the other rows, the details stay in the data). The data go back into their file and the page is made again. Without saved data: a full download as with `--write-json`, or, when the page exists, a skip with a hint. It takes the same sources as `--load-json` plus addresses and playlists, and cannot be combined with `--write-json`, `--load-json` or `--overwrite`. README: *Saved data* compares the JSON options.

### Changed

- `--save-json` is now `--write-json` and `--from-json` is now `--load-json` (the old names are gone). They cannot be combined: `--load-json` only reads saved data. README has a new section, *Saved data*, on what each one does.
- The saved data keep each translation left out as `{reason, refused, date}` (before: the reason only); *About this copy* gives the day of a refusal.
- A translation that YouTube refuses to send (`HTTP 429`, frequent since mid-November 2026, also for yt-dlp alone and with cookies) or whose download fails no longer stops the video: like a translation YouTube does not offer, it is left out with a note (*About this copy*: `not available: bilingual:pl (YouTube refused to send it (HTTP 429 Too Many Requests))`), the page is made and counts as finished; `--update-json` or `--overwrite` adds it later. Only a failed transcript still saves nothing.
- Each translation is asked for once, a random 70 to 100 seconds after the video's data was fetched, without retries (before: at least 65 s, then retries after 60 and 120 s). The same for `sub:` translations; a refused subtitle file still counts as failed, and the next run with the same `--video` tries again.
- Each caption track is downloaded once per run, as json3, for the transcript and the `sub:` files alike: `--transcript bilingual:pl` with `--video sub:pl` asks YouTube for the Polish translation once, and a refusal is not asked again. The SRT files are made from it with the same cues as YouTube's own SRT (`test_online.py` compares them).

## [0.9.1] - 2026-11-13

### Changed

- Comments: when YouTube shows a message in place of the comments (read from the video page), the section gives it in YouTube's words (`Comments are turned off.`), else `Comments are unavailable.`; *About this copy* and the index say `turned off` or `unavailable` accordingly (before: `turned off or unavailable` in every case). A section with nothing to count (turned off, unavailable, no comments) has no line of numbers; when no comment came for another reason (a limit, `Ctrl+C`, comments YouTube counts but does not send), the line keeps only the numbers (`0 downloaded (YouTube shows 120)`), without the order and the dates.
- `--transcript` works like `--video`: without it the page has no transcript (none is downloaded, also none shown with `--from-json`); the option alone means `plain`; `none` is gone (like any unknown word, `'none' is not a parameter (use plain, timestamps or bilingual:CODE)`, as `--video` says `is not a parameter`). Write the address first: `--transcript URL` is an error that says so. `bilingual:CODE` in the help and README. A code that is no language code (`bilingual:jp`) stops at once, before anything is downloaded. What YouTube does not have is left out with a note and the page is made: a translation it does not offer (the message lists the languages it does; *About this copy* says `not available: bilingual:fi (…)`), a video without captions. A failed download (the transcript or a translation, e.g. after repeated `HTTP 429`) saves nothing, so the next run tries again; before, a missing translation stopped the video and a failed transcript left the page without one.
- `--video`: the parameter `overwrite` is now `again` (no mix-up with `--overwrite`, which makes the page again). Each item once: the same item twice, or a language both alone and with a role or kind (`audio:pl,audio:pl.dubbed`), is an error before anything is downloaded, as is a code that is no language code (`audio:jp`, `sub:jp`); two codes that give one track (`audio:de,audio:de-DE`) are taken once, with a message.
- *About this copy*, *Command* row: `--video` right after the address, with the parameters of the download but without `again` and without the default `1080p`, as `--transcript` alone stands for `plain` (the files belong to the copy; before, `--video` was left out).
- Player, *Other audio*: each track's file is looked for as soon as the video is found, so a track whose file is missing is greyed out at once, not only after a click.

## [0.9.0] - 2026-11-13

### Added

- `--video`: after the page, also download the video itself as an mp4 named like the page, so the page plays it (needs ffmpeg). By default the best quality up to 1080p in AV1, else H.264, with the original audio. Parameters follow `--video`, comma-separated: a quality as an upper limit (`720p`; `720p30` limits the frame rate too), `audio:CODE`, `sub:CODE` (both below) and `overwrite`. `yt2html --video URL` (the address taken as parameters) is an error that says to put the address first; not with `--from-json` or `--index-only`.
  - The order of work: the page first, then a fresh list of formats and the download, then the page again. Its *Video file* row says what was downloaded (`downloaded DD.MM.YYYY: 1080p, 30 fps av1 + en-US aac, mp4, 293 MB; extra audio m4a: pl (dubbed), 69 MB`) and *Video formats* marks it in yellow (the planned default stays blue when it differs); with `--save-json` the data gets the fresh formats and the result (`yt2html_video`).
  - A file already next to the page is kept, each on its own (`overwrite` downloads it again, into that file): the video found by the id in its name, a track or subtitles by the id and their ending (`.pl.dubbed.m4a`), so renamed files count too; for an existing page only the files are downloaded. A page made again (`--overwrite --video …`) describes the files that are there: with what the saved data (`--save-json`) of their download say about the same file (same size: the day, the variant), else only as found next to the page; the *Video file* row lists them by day of download, then `next to the page: video …; extra audio m4a: …; subtitles srt: …` (tracks and subtitles only found are yellow in *Video formats*, a video only found is not).
  - Checks before the download: ffmpeg (else the video is skipped, with the install command), Deno and the age of yt-dlp (notes). In a playlist, a pause of 5–15 seconds before each next video that needed a download.
- `audio:CODE`: an extra audio track as a separate m4a (`Title [id].pl.dubbed.m4a`). `audio:pl` takes a dub by the author first, then YouTube's automatic one; `audio:pl.auto-dubbed` exactly that version. Tracks that cannot be added are reported with the ones that can.
- `sub:CODE`: subtitles as YouTube's own SRT files (`Title [id].pl.translated.srt`). `sub:pl` takes the author's, else the auto-generated ones, else a machine translation of the transcript's subtitles; `sub:pl.author`, `.auto-generated`, `.translated` exactly that kind. Translations wait and retry like bilingual transcripts, with the same hint when YouTube keeps refusing. Subtitles need no ffmpeg.
- Player, *Other audio* (a working version): extra tracks downloaded with the page get a last row of switches (their codes, the original first and on). With a track on, the video plays muted and the track follows it: the gap is measured every 250 ms and averaged over a second; the track plays 3 % faster or slower from 80 ms until below 20 ms and is set to the video's time only when more than 1 s off, so that speed changes and jumps, which can click, are rare. Renamed tracks play too: the file with the video's id and the track's ending found when the page was made, then the page's own name with that ending, then the name the track was saved under; a track none of these finds is greyed out.

### Changed

- Player: speeds from 0.25× (language learning) to 2× (lectures, podcasts); buttons that show symbols or codes have a spoken name for screen readers and voice control (`aria-label`, invisible).
- *Video formats*: a single audio track without a language in the data takes the original language the transcript uses (the watch page's default subtitles for it), else shows as `unknown`; language names come from the audio format, else from the author's subtitles (`Portuguese (Brazil)`), else from YouTube's list.
- `--video-formats`: also prints the subtitles a download can take (`subtitles (srt):` by the author, auto-generated, or `subtitles: none`).

## [0.8.0] - 2026-11-12

### Added

- *Video formats*, a folded section above *About this copy* on every page: the formats YouTube offers for the video, with a short summary when folded (top quality, language of the original audio) and two small tables: image by quality (YouTube's name, as in its quality menu, and the frame rate; the actual size in a resolution column; with the parameter that will choose it when a download can take it, up to 1080p in AV1 or H.264, e.g. `1080p, 30 fps (1080p30)`) and codec (h264, av1, vp9, h265; HDR; formats for YouTube Premium accounts in their own columns) with approximate sizes including the audio; HLS formats (a playlist of short pieces) only named, since their declared bitrate gives no reliable size; languages: one row per audio track with code, English language name and role (original, dubbed by the author, auto-dubbed by YouTube, audio description; a single track counts as the original; aac and opus) and the subtitles in that language (by the author; auto-generated from that track's speech), subtitles without such audio in rows of their own; the original's language first, then the other languages with audio, then those without, alphabetically; a line on machine translation of subtitles (`machine-translated subtitles: possible in 155 other languages for each row with subtitles`; else `none (no subtitles to translate)`, `unknown (YouTube listed no translation languages)` or `unknown (not read from YouTube; make the page again later)`). The planned default for a later video download (the best quality up to 1080p in AV1, else H.264, with the original audio, mp4) is shown in light blue (darker blue in the light theme; yellow is kept for the choice of a later download); the table captions carry no colour legend, README explains the colours. Sizes below 1 MB show as `<1 MB` (Shorts); a track without a language in the data shows as `unknown`. Without video and audio formats in the data (YouTube sometimes sends only the storyboard images for the seek bar's preview, usually for a short while, e.g. after several requests for the same video) the section says so in one line (`no video or audio formats in the data: …; make the page again later`), and so does `--video-formats`. Works with `--from-json`, since the formats are in the saved data.
- `--video-formats`: prints, for each video, the qualities a download can take (one per line: quality, the parameter that will choose it, codec, size with the original audio, the default marked) and the audio: the track in the video and, by role, the tracks a download can add; no page is made, only the video's data is fetched (no comments), or read from saved data with `--from-json`. It cannot be combined with the options that shape a page.
- *About this copy*: a *Video file* row: a video file found next to the page (name and size), else not downloaded (the video itself is not downloaded yet; the planned default shows in *Video formats*, not here); whether a JavaScript runtime (Deno) was there at download (yes, no, or unknown for data saved by an older version) and whether the data was fetched signed in (then without HLS formats).
- The download settings in the data note whether Deno was available (`yt2html_download.deno`).
- `--cookies`: a short name for `--cookies-from-browser`, which stays (the Command row in *About this copy* keeps the full name).

### Changed

- Option values ignore the case of letters: `--transcript Plain,bilingual:PL`, `--theme Dark` (the browser in `--cookies-from-browser` already did). A language code is written the usual way, as YouTube does (`bilingual:PT-br` → `pt-BR`), so it matches YouTube's list and the Command row in *About this copy* shows it that way; before, `bilingual:PL` stopped with "no translation into 'PL'".
- Player: the tile names the video file under *Play* instead of `Player`: its quality as YouTube names it and the language of its audio (`1080p · en-US`; a code that does not fit is shortened to the language, `en`). The label never widens the tile.

### Fixed

- Making a page no longer prints yt-dlp's warning `ffmpeg not found. The downloaded format may not be the best available`: a page downloads no video, so it needs no ffmpeg.
- `--cookies-from-browser` with a profile, keyring or container (`"firefox:PROFILE_FOLDER"`, the syntax of yt-dlp's command line) failed with `unsupported browser`: the whole text went to yt-dlp as the browser name. It is now split as yt-dlp does, and a wrong browser or keyring is reported before anything is downloaded.

## [0.7.1] - 2026-11-10

### Changed
- *About this copy*: the *Chapters* row is now *Markers*, since it covers every timestamp list YouTube shows, not only chapters; each list is named, in lower case, with its count in brackets (`chapters (47), from the description; walkthroughs (11)`, `no chapters; walkthroughs (11)`).

## [0.7.0] - 2026-11-09

### Added
- Player: **↺** goes back to the start of the previous sentence of the transcript (for repeating a phrase); each further click one sentence more; 5 seconds back when that would be more than 20 seconds or there is no transcript. Keys `Shift+←` for it and `Ctrl+←` / `Ctrl+→` for the start and the end.
- Player: *Chapters* button that shows the current chapter above the subtitles, on one line with its place reserved, so the subtitles never move.

### Changed
- Player: *Subtitles* cycles through the sentence only, with one and with two sentences before and after, and off (shown as dots on the button).
- Player: *Follow* is one button: through the transcripts, then off; *Stop following* is gone.
- Player: the translation under the video has a faint touch of turquoise, so it stands out from the original lines.
- Player: no *Pop out* button; the browser's own picture-in-picture still works (right-click the video in Chrome or Edge, Firefox's own button).
- `--no-avatars` leaves out only the avatars of comment authors; the channel's avatar stays.
- Files are found by the id in square brackets anywhere in their name, whatever the rest (before: the name had to end with `[ID].html` or `[ID].info.json`): pages, indexes and saved data in the output folder, and file names given as sources, also of videos (`"Title [ID].mp4"`). Video files next to the page with the id in their name are found when the page is made, so the player plays renamed videos too. Several matching files: the first by name, with a note.
- *About this copy*: shorter states (`at download`, `none`, `video 3 of 15 in …`) and the channel avatar apart from the comment avatars.

## [0.6.0] - 2026-11-08

### Added
- A player for a downloaded copy of the video: if a video file with the page's name (or yt-dlp's original name) lies next to the page, a turquoise *Play* tile opens a player pinned on the left while the page scrolls on the right. Buttons for start (stops the video), ±10 seconds, play/pause, end, a separate window (*Pop out*) and speeds 0.5×–1.5×; keys `Space`, `←`, `→`. The video is found each time the page is opened, so it may be downloaded later.
- While the player is open, timestamps play the local copy instead of opening YouTube: in chapters and other lists, info cards, transcripts, the description, the AI summary and comments; in the continuous transcript a click on a sentence. *Subtitles* shows under the video the current chapter and the current sentence with the ones before and after it; small language buttons (one per bilingual transcript) add a translation. *Follow* marks the current sentence in a transcript on the page and keeps it in view (pressed again: the next transcript; after *Stop following* or scrolling, which end it, it starts again with the transcript used last).
- Speaker changes in YouTube's automatic captions (`>>`) are shown as a grey mark in the transcripts (a grey ` | ` in the continuous one) and left out in the lines under the video.
- The author's info cards (the "i" teasers linking to a video, playlist, channel or website) in a section after the chapters: time, image, title linking to the target, kind, details and the author's message. Link cards point to the real website instead of YouTube's one-time redirect, and show its domain. `--no-cards` leaves them out without reading or requesting anything for them.

- *About this copy*, folded at the bottom of every page instead of the footer: when the data was downloaded and with which versions, when the page was built (also later from saved data), the playlist the video came from (position and length), the command that makes an equivalent copy, whether the video page could be read, and for chapters, info cards, transcript, comments and images what is there, what was left out and what failed; then the licenses of the programs and a disclaimer. Every row is always there.
- `--from-json` also takes patterns with `*` and `?` (expanded by the program, so they work in PowerShell and cmd; square brackets count as part of the name) and video ids or page names, whose saved data is looked up in the output folder (`-o`): `yt2html --from-json VIDEO_ID -o FOLDER`. A missing file is reported with what was looked for. With `--from-json`, `--max-comments N` keeps the first N saved comments (those a download with that limit gets) and `--no-comments` leaves them out; before, both were ignored.

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

[Unreleased]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.8...HEAD
[0.10.8]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.7...v0.10.8
[0.10.7]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.6...v0.10.7
[0.10.6]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.5...v0.10.6
[0.10.5]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.4...v0.10.5
[0.10.4]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.3...v0.10.4
[0.10.3]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.2...v0.10.3
[0.10.2]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.1...v0.10.2
[0.10.1]: https://github.com/mwislowski/yt-video-archiver/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.9.4...v0.10.0
[0.9.4]: https://github.com/mwislowski/yt-video-archiver/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/mwislowski/yt-video-archiver/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/mwislowski/yt-video-archiver/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/mwislowski/yt-video-archiver/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/mwislowski/yt-video-archiver/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mwislowski/yt-video-archiver/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mwislowski/yt-video-archiver/releases/tag/v0.1.0
