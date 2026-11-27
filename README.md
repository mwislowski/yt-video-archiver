# yt-video-archiver

`yt2html` saves a YouTube video page as **one self-contained HTML file** for offline use, in the spirit of the SingleFile browser extension: description, chapters, comments and, on request, the transcript, readable without YouTube and without an internet connection. It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp); no AI, no browser, no API key.

It can also download the video itself next to the page, with extra audio tracks (e.g. a dub) and subtitles as SRT files, and the page then plays it, with subtitles and transcript following along: handy for learning a language. A **list file** of videos and playlists gets an **index page** with chapters, notes and its own player, for watching a whole series.

This is the short guide for everyday use. Every option in detail: [docs/USAGE.md](docs/USAGE.md).

## What the page contains

- **Thumbnail, title, channel** with its avatar and subscriber count.
- **Facts:** duration, publication date, who could watch the video when it was not plainly public (unlisted, private, members, Premium, 18+), views, likes, comment count, and in grey the date of the copy.
- **Full description** with clickable links, timestamps, `#hashtags` and `@handles`, and YouTube's **AI-generated summary**, if it offers one.
- **Chapters** and other timestamp lists such as **Walkthroughs**, **info cards**, **People mentioned**, the video's **podcast** or course.
- **Transcript** (with `--transcript`), split by chapters: as continuous text, with timestamps, or side by side with a translation.
- **Comments**, the pinned one first, then oldest first, every thread expanded; up to 1,000 by default.
- **A player for your downloaded copy of the video**, if it lies next to the page.
- Folded at the bottom: **Video formats** (what YouTube offered) and **About this copy** (how this copy was made and what it lacks).

Images are embedded in the file. The page works without JavaScript (a small script only adds the player) and follows the light or dark colours of the device it is opened on.

## Installation

Requires [uv](https://docs.astral.sh/uv/) (it also installs a suitable Python by itself).

```powershell
uv tool install git+https://github.com/mwislowski/yt-video-archiver
uv tool update-shell
```

`uv tool update-shell` adds uv's command folder to `PATH` (once; open a new terminal afterwards). Check with `yt2html --version`. Update the program and yt-dlp with `uv tool upgrade yt-video-archiver`; remove it with `uv tool uninstall yt-video-archiver`.

- **For downloading the video** (`--video`): [ffmpeg](https://ffmpeg.org/) (`winget install --id Gyan.FFmpeg -e`; macOS `brew install ffmpeg`; Linux e.g. `sudo apt install ffmpeg`). Pages need no ffmpeg.
- **Optional:** Deno (`winget install DenoLand.Deno`; macOS `brew install deno`), a JavaScript runtime yt-dlp recommends for YouTube. Without it yt-dlp prints warnings and YouTube may offer fewer video formats; the page is not affected.
- **macOS and Linux:** install uv with `brew install uv` (macOS) or `curl -LsSf https://astral.sh/uv/install.sh | sh`, then the two commands above. Put page names in single quotes there (`'Title [ID].html'`).

## Usage

```powershell
yt2html "https://www.youtube.com/watch?v=..."
```

The page is saved in the current folder as `Title [video id].html` (the name yt-dlp gives the video); `-o FOLDER` saves it elsewhere. Always put the URL in quotes. Instead of a URL you can give a video or playlist id (`Z8lf5oWKfj0`), or the name of a page or video file with the id in square brackets (`"My title [Z8lf5oWKfj0].html"`).

### How the options work

Four options say what to make, one for each kind of file next to the page:

| Option | Its files | Without it |
|---|---|---|
| `--page` | the page (`Title [id].html`): what it shows and how | the page with the defaults |
| `--transcript` | the transcript on the page, and its SRT files | no transcript |
| `--video` | the video (`Title [id].mp4`) and extra audio tracks (`.m4a`) | no video |
| `--json` | the saved data the page is made from (`Title [id].info.json`) | no saved data |

- **One value, parameters separated by commas, no spaces:** `--page comments:200,theme:dark`, `--transcript plain,dual:pl`, `--video 720p,audio:pl`.
- **The address first,** before the options: `yt2html "URL" --transcript`.
- **Files already there are skipped,** so the same command again does only what is missing, and an interrupted run continues where it stopped. **`ovr`** makes a file again: `--page ovr`, `--video ovr`, `--transcript ovr`.
- **Mistakes stop the program before anything is downloaded:** an unknown value, or options that contradict each other.
- **At most 100 videos from YouTube per run;** `--take all` for more.

### Common tasks

**What to archive**

| Task | Command |
|---|---|
| One video | `yt2html "URL"` |
| Several videos at once | `yt2html "URL1" "URL2" "URL3"` |
| All videos of a playlist | `yt2html "https://www.youtube.com/playlist?list=..."` |
| Videos and playlists of a list file, with its index page | `yt2html --index series.txt` |
| Save into another folder | `yt2html "URL" -o "D:\Videos\Channel"` |
| Make a page again although the video already has one | `yt2html "URL" --page ovr` |
| More than 100 videos in one run | `yt2html "PLAYLIST URL" --take all` |

**The page and its transcript**

| Task | Command |
|---|---|
| Fewer comments (quick test), all of them, none | `--page comments:200`, `--page comments:all`, `--page comments:no` |
| The transcript, as continuous text | `yt2html "URL" --transcript` |
| The transcript with timestamps, and side by side with its Polish translation | `yt2html "URL" --transcript timed,dual:pl` |
| The transcript in Polish only | `yt2html "URL" --transcript plain:pl` |
| The transcript's subtitles also as SRT files | `yt2html "URL" --transcript timed,srt` |
| Page always dark, without info cards | `yt2html "URL" --page theme:dark,cards:no` |

**Video, saved data and the rest**

| Task | Command |
|---|---|
| Also download the video (an mp4 the page plays; needs ffmpeg) | `yt2html "URL" --video` |
| The same, at most 720p at 30 frames per second, with the Polish dub as an extra track | `yt2html "URL" --video 720p30,audio:pl` |
| List the qualities and tracks a download can take, without making a page | `yt2html "URL" --video-formats` |
| Also save the data the page is made from | `yt2html "URL" --json write` |
| Add to saved data what is missing, e.g. a translation YouTube refused before | `yt2html VIDEO_ID --json write --transcript dual:pl` |
| Refresh counts and comments in the saved data | `yt2html VIDEO_ID --json refresh --transcript` |
| Make a page again from saved data only, offline | `yt2html VIDEO_ID --json load --transcript` |
| Merge a translation of your own into the saved data | `yt2html --json merge "Title [id].pl.own.srt"` |
| See what the saved data hold and what they lack, offline | `yt2html "*.info.json" --json show` |
| When YouTube asks to sign in or to confirm you are not a bot | `yt2html "URL" --cookies firefox` |
| Versions of the program and yt-dlp; all options | `yt2html --version`; `yt2html --help` |

Options combine, e.g. `yt2html --index series.txt -o "D:\Videos" --page comments:500 --transcript timed`.

### The page: `--page`

| Parameter | Meaning |
|---|---|
| `ovr` | make the page again although the video already has one: everything is downloaded again from YouTube |
| `comments:N`, `comments:all` | at most N comments, replies counted (default 1000); all of them (10,000 take a few minutes) |
| `comments:0`, `comments:no` | no comments, but the section with YouTube's count; no comments section at all |
| `snap:no`, `cards:no`, `avatars:no` | without the grey date tile; without info cards; letters instead of commenters' avatars (a smaller file) |
| `theme:dark`, `theme:light` | the page always dark or always light (default: it follows the device) |

`Ctrl+C` while the comments download stops it and still makes the page from what has come.

### The transcript: `--transcript`

`--transcript` alone adds the transcript as continuous text in the video's original language. Versions, in the order you give them:

| Version | Result |
|---|---|
| `plain` | continuous text, without timestamps |
| `timed` | one line per caption, each with its timestamp |
| `dual:CODE` | like `timed`, with the translation into that language (e.g. `dual:pl`) in a second column |
| `plain:pl`, `timed:de` | the transcript in another language only |
| `srt` | each version also as an SRT file next to the page, for a player such as VLC |

Another language comes from your own translation (see *Saved data*), else YouTube's machine translation, else the author's own subtitles in that language. **YouTube often refuses machine translations** (`HTTP 429`): the program waits 70 to 100 seconds before asking (the wait is shown), and a translation refused even then is left out with a note; the page is made all the same. Add it later with `--json write` and the same `--transcript`, or translate the subtitles yourself and merge them. If YouTube shows the original language wrongly (it happens with automatically dubbed videos), set it: `--olang CODE`.

### Downloading the video: `--video`

`yt2html "URL" --video` makes the page, then downloads the video as an mp4 named like the page, so the page plays it.

| Parameter | Meaning |
|---|---|
| (none) | the best quality up to 1080p, in AV1 else H.264, with the original audio |
| `720p`, `480p`, … / `720p30`, … | a quality as an upper limit / with the frame rate as a limit too |
| `audio:pl`, `audio:de`, … | an extra audio track in that language as a separate m4a (a dub; repeat for more); `audio:pl.auto-dubbed` exactly that kind |
| `ovr` | download again the files this command makes that are already there |

A file already next to the page is kept, also when renamed (it is found by the id in square brackets), so the same command over a playlist downloads only what is missing. An interrupted download resumes when you run the command again. `--video-formats` shows what a download can take.

### Saved data: `--json`

The data a page is made from can be saved next to it as `Title [id].info.json`. They let you make the page again later without YouTube (a newer look after an update), add what is missing, and refresh what changes. They keep no download addresses and no IP address.

| Parameter | What it does | Asks YouTube |
|---|---|---|
| `write` | saves the data with the page; where they are already there, adds only what the options ask for and they lack | for what is new or missing |
| `refresh` | as `write`, and refreshes counts, title, description and comments (new ones added, removed ones kept and marked) | yes |
| `load` | makes the page again from saved data only | no |
| `merge` | adds a translation or corrections of your own (an SRT file) to the saved data | no |
| `show` | lists what the saved data hold and what they lack, changes nothing (`show:brief`: one line per video) | no |

What you save is not lost by accident: a refresh keeps comments YouTube no longer shows, earlier titles and descriptions, and your own translations. Only `--page ovr --json write` starts again from YouTube, and the console names what it replaces.

**A translation of your own**, when YouTube refuses its own:

1. `yt2html "URL" --json write --transcript srt` saves the data and the original's subtitles, e.g. `Title [id].en.auto-generated.srt`.
2. Translate that file (by hand or with a tool), keeping its lines and times, and save it as `Title [id].pl.own.srt`.
3. `yt2html --json merge "Title [id].pl.own.srt"` adds it to the data.
4. `yt2html VIDEO_ID --json load --transcript plain,dual:pl` shows it on the page.

Corrections of the original's text go the same way, as `Title [id].en.fixed.srt`.

### Many videos and list files

A **playlist** URL or id archives all its videos in order; videos that already have a page in the folder are skipped (a page is found by the id in square brackets, so you may rename pages freely). Channel URLs are not supported: use the channel's playlist.

A **list file** is a plain text file (UTF-8) with one video or playlist per line, organised in a way close to Markdown:

```text
!layout: cards
# Railways of the world
> My collection. **Japan** first; sources: [example.org](https://example.org)
## Japan
Z8lf5oWKfj0
> The best introduction, start here.
= Rail in numbers
The Largest Rail System In the World [qyqCTMirNWg].html
### My own recordings
Holiday in the Tatras.mp4
## A course for dessert
PLeBgIYTbh-CwGJwFnluRTgBkvcM2q6jOO
```

| Line | Meaning |
|---|---|
| an address or id | a video, or a playlist (its videos, read from YouTube each time) |
| `Holiday.mp4` | a video file of your own in the output folder |
| `# Title`, `## Chapter`, `### …` | the index's title, chapters on three levels |
| `> text` | a note, with `**bold**`, `*italic*`, `((grey))` and `[links](https://example.org)` |
| `= Title` | your own title for the next line; `= !date: 03.08.2019`, `= !lang: pl` your own date and language |
| `// text` | a comment, ignored |
| `!numbers: no`, `!thumbnails: no`, `!dates: no`, `!layout: plain`/`compact`/`cards`, `!theme: dark` | settings for the whole list, at the top |

`yt2html --index series.txt` downloads what is missing, then makes the index page `series.html` next to the pages; `yt2html --index-only series.txt` only makes the index, from what is in the folder. The index always shows what is in the folder: each video with its thumbnail, title (linking to its page) and date, and in grey what is missing. A click on a thumbnail plays the file in the index's player on the left (previous and next file, *Autoplay*, speeds). The program never changes the list file.

### Watching a downloaded video on the page

When the video lies next to the page, the page shows a turquoise **▶ Play** tile. It opens a player pinned on the left, while you scroll the description, transcript and comments on the right.

- **Buttons:** to the start, 10 seconds back, **↺ previous sentence** (to repeat a phrase), play/pause, 10 seconds forward, to the end, speeds from 0.25× to 2×, and a switch for each extra audio track.
- **Keys:** `Space` play/pause, `←` / `→` 10 seconds, `Shift`+`←` previous sentence, `Ctrl`+`←` / `Ctrl`+`→` start / end.
- **Timestamps play your copy:** chapters, cards, the transcript, the description and comments; `Ctrl`+click opens YouTube.
- **With a transcript:** *Subtitles* shows the current sentence under the video (with one or two sentences of context, and a translation by its language button), *Chapters* the current chapter, and *Follow* keeps the current sentence in view in the transcript.

Chrome, Edge and Firefox play the mp4 that `--video` downloads (Safari plays its AV1 only with a hardware decoder); `.webm`, `.m4v` and `.mkv` work as far as the browser plays them.

### Video formats and About this copy

**Video formats** lists what YouTube offered: qualities with sizes, audio tracks (original, dubs, audio description) and subtitles; what `--video` takes by default is in blue, what it downloaded in yellow. **About this copy** says, row by row, when and how this copy was made, the command that makes an equivalent copy again, and what is missing and why; two copies of one video can be compared that way.

## Good to know

- Keep the pages themselves, not only the saved data: a page holds its images, which YouTube may remove over the years.
- Comment times are as YouTube showed them on the download day (`3 years ago`); hover to see the estimated date.
- An SRT file is written over when its transcript changes: correct your own translation in its `.own.srt` file and merge it again.
- Typical page size: a few hundred KB, up to a few MB with thousands of comments.

## Troubleshooting

- **Fetching fails after it used to work:** YouTube changes often and yt-dlp follows within days; update with `uv tool upgrade yt-video-archiver`.
- **`Sign in to confirm you're not a bot`, an age-restricted video, a private playlist:** add `--cookies firefox` (the browser's sign-in; Firefox works best on Windows).
- **A translation fails with `HTTP 429`:** see *The transcript* above.
- **Anything else:** the console says what happened, and running the same command again continues. Messages and what to do about them: [docs/USAGE.md](docs/USAGE.md#troubleshooting).

## More

- [docs/USAGE.md](docs/USAGE.md): the full guide, every option and message in detail.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): how the program works, tests and releases.
- [CHANGELOG.md](CHANGELOG.md): what changed in each version.

## License

yt2html is released under the [MIT License](LICENSE) (© 2026 Mateusz Wisłowski): you may use, copy, modify and share it freely, provided the copyright and license notice are kept. It relies on [yt-dlp](https://github.com/yt-dlp/yt-dlp), released into the public domain under the [Unlicense](https://unlicense.org). These licenses cover the programs, not what they save.

## Disclaimer

The archived pages contain material of the video's authors and of the commenters (texts, thumbnails, avatars, photos), which remains theirs. The program is meant for personal archiving, e.g. keeping your own offline copy next to a downloaded video; do not publish or distribute archived pages without the rights to their content.

**Please support the authors.** An offline copy is not a view and earns them nothing. If a video is worth keeping, it is fair to watch it on YouTube, like it, subscribe, or support the channel in the ways it offers.

The software comes without any warranty. It relies on data that YouTube does not officially document and may stop working at any time when YouTube changes its pages. You are responsible for complying with YouTube's Terms of Service and the law.
