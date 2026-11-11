""""About this copy": the folded block at the bottom of the page, row by row."""

import base64
import datetime as dt
import json
import re

import pytest

import yt2html
from conftest import caption_formats, watch_page

DAY = dt.date(2026, 11, 5)
BUILT = dt.datetime(2026, 11, 8, 15, 40)
ROWS = ["Downloaded", "Page built", "Playlist", "Command", "Video page", "Markers",
        "Info cards", "Transcript", "Comments", "Images", "Video file", "Software", "Disclaimer"]


def about(html: str) -> str:
    start = html.index('<details class="copy-info">')
    return html[start:html.index("</details>", start)]


def row(html: str, name: str) -> str:
    """The text of one row (tags removed, entities kept)."""
    match = re.search(rf"<dt>{name}</dt><dd>(.*?)</dd>", html)
    assert match, f"no row {name}"
    return re.sub(r"<[^>]+>", "", match.group(1))


def render(info, **options) -> str:
    options.setdefault("built", BUILT)
    return yt2html.render_page(info, options.pop("thumbnail", None), options.pop("avatars", {}),
                               DAY, **options)


# --------------------------------------------------------------------------
# The block itself
# --------------------------------------------------------------------------

def test_folded_block_replaces_the_footer(info):
    html = render(info)
    assert '<footer class="footer">' not in html
    block = about(html)
    assert block.startswith('<details class="copy-info"><summary>About this copy'
                            '<span class="rest"> · Snapshot 05.11.2026 · made with yt2html</span>'
                            '</summary>')
    assert ".copy-info[open] summary .rest{display:none}" in html   # open: only the title


def test_every_row_is_always_there_in_order(info):
    minimal = {"id": "MINIMALVID1", "title": "x"}
    for data in (info, minimal):
        names = re.findall(r"<dt>(.*?)</dt>", about(render(data)))
        assert names == ROWS


# --------------------------------------------------------------------------
# Downloaded, Page built, Playlist
# --------------------------------------------------------------------------

def test_downloaded_when_where_and_with_what(info):
    epoch = int(dt.datetime(2026, 11, 6, 14, 32).timestamp())
    info.update(epoch=epoch, _version={"version": "2026.10.26"})
    info[yt2html.DOWNLOAD_KEY] = {"yt2html": "0.6.0", "cookies_from_browser": None}
    html = render(info)
    assert ('<dd>06.11.2026 14:32 from <a href="https://www.youtube.com/watch?v=TESTVIDEO01">'
            'youtube.com/watch?v=TESTVIDEO01</a> with yt2html 0.6.0 and yt-dlp 2026.10.26</dd>'
            in html)


def test_downloaded_without_the_time_uses_the_snapshot_date(info):
    assert row(render(info), "Downloaded") == "05.11.2026 from youtube.com/watch?v=TESTVIDEO01"


def test_page_built(info):
    assert row(render(info), "Page built") == "at download"
    assert row(render(info, from_json=True), "Page built") == \
        f"08.11.2026 15:40 from the saved data, with yt2html {yt2html.__version__}"


def test_playlist_row(info):
    assert row(render(info), "Playlist") == "none"
    info[yt2html.PLAYLIST_KEY] = {"id": "PLx", "title": "Season <2>", "position": 3, "count": 15}
    html = render(info)
    assert ('<dd>video 3 of 15 in <a href="https://www.youtube.com/playlist?list=PLx">Season &lt;2&gt;</a>'
            '</dd>' in html)


def test_playlist_context_is_the_playlist_used(tmp_path, monkeypatch, info):
    playlist = "https://www.youtube.com/playlist?list=PLtest1234567890"
    entries = [{"_type": "url", "id": f"TESTVIDEO0{n}", "title": f"Episode {n}",
                "url": f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"} for n in (1, 2, 3)]
    monkeypatch.setattr(yt2html, "fetch_playlist", lambda source, args: ("Test playlist", entries))
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda source, args: {**info, "id": yt2html.video_id_from_url(source)})
    assert yt2html.main([playlist, "-o", str(tmp_path), "--save-json"]) == 0
    saved = json.loads(next(tmp_path.glob("*TESTVIDEO02*.info.json")).read_text("utf-8"))
    assert saved[yt2html.PLAYLIST_KEY] == {"id": "PLtest1234567890", "title": "Test playlist",
                                           "position": 2, "count": 3}
    html = next(tmp_path.glob("*TESTVIDEO02*.html")).read_text("utf-8")
    assert row(html, "Playlist") == "video 2 of 3 in Test playlist"
    # a single video given separately is not "from a playlist"
    monkeypatch.setattr(yt2html, "fetch_info", lambda source, args: {**info, "id": "TESTVIDEO09"})
    yt2html.main(["https://www.youtube.com/watch?v=TESTVIDEO09", "-o", str(tmp_path)])
    html = next(tmp_path.glob("*TESTVIDEO09*.html")).read_text("utf-8")
    assert row(html, "Playlist") == "none"


def test_fetch_info_keeps_the_download_settings(monkeypatch, info):
    monkeypatch.setattr(yt2html, "add_watch_page_data", lambda ydl, i, cards=True: None)

    class Fake:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download): return dict(info)
        def sanitize_info(self, i): return i

    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", Fake)
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: None)  # whatever this computer has
    args = yt2html.parse_args(["u", "--transcript", "none",
                               "--cookies-from-browser", "firefox:/home/me/profile"])
    fetched = yt2html.fetch_info("u", args)
    assert fetched[yt2html.DOWNLOAD_KEY] == {"yt2html": yt2html.__version__,
                                             "cookies_from_browser": "firefox",
                                             "deno": False}
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: name == "deno" and "/bin/deno")
    assert yt2html.fetch_info("u", args)[yt2html.DOWNLOAD_KEY]["deno"] is True


# --------------------------------------------------------------------------
# Command
# --------------------------------------------------------------------------

def command(html: str) -> str:
    return re.search(r'<code class="command">(.*?)</code>', html).group(1)


def test_command_with_defaults_is_the_id_alone(info):
    info[yt2html.CARDS_KEY] = []
    assert command(render(info)) == "yt2html TESTVIDEO01"


def test_command_has_the_settings_that_shaped_the_page(info):
    info[yt2html.LIMIT_KEY] = 500
    info[yt2html.DOWNLOAD_KEY] = {"yt2html": "0.6.0", "cookies_from_browser": "firefox"}
    html = render(info, transcript_spec=[("timestamps", None), ("bilingual", "pl")],
                  theme="light", show_snapshot=False, no_avatars=True)
    assert command(html) == ("yt2html TESTVIDEO01 --max-comments 500 --no-cards --no-avatars "
                             "--transcript timestamps,bilingual:pl --theme light --no-snapshot "
                             "--cookies-from-browser firefox")


def test_command_left_out_comments_and_transcript(info):
    info[yt2html.CARDS_KEY] = []
    del info["comments"]
    del info[yt2html.TRANSCRIPT_KEY]  # --transcript none at download
    assert command(render(info)) == "yt2html TESTVIDEO01 --no-comments --transcript none"
    info[yt2html.TRANSCRIPT_KEY] = None  # tried, but the video has none
    assert command(render(info)) == "yt2html TESTVIDEO01 --no-comments"


def test_command_with_an_id_starting_with_a_dash_uses_the_address(info):
    info.update(id="-Tk4nW8sYq0")
    info[yt2html.CARDS_KEY] = []
    assert command(render(info)) == "yt2html https://www.youtube.com/watch?v=-Tk4nW8sYq0"


def test_command_of_a_page_built_from_saved_data(capsys, tmp_path, info):
    info[yt2html.LIMIT_KEY] = 50
    info[yt2html.CARDS_KEY] = []
    saved = tmp_path / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    yt2html.main(["--from-json", str(saved), "-o", str(tmp_path / "p"), "--transcript",
                  "timestamps", "--theme", "dark", "--no-cards"])
    html = next((tmp_path / "p").glob("*.html")).read_text("utf-8")
    assert command(html) == ("yt2html TESTVIDEO01 --max-comments 50 --no-cards "
                             "--transcript timestamps --theme dark")
    assert row(html, "Page built").endswith(f"from the saved data, with yt2html {yt2html.__version__}")
    assert "--from-json" not in html and str(tmp_path) not in html


@pytest.mark.parametrize("spec, expected", [
    ("firefox", ("firefox", None, None, None)),
    ("Firefox", ("firefox", None, None, None)),
    ("firefox:fTW1hk7Y.Profile 1", ("firefox", "fTW1hk7Y.Profile 1", None, None)),
    (r"firefox:C:\Users\me\Profiles\x.default", ("firefox", r"C:\Users\me\Profiles\x.default", None, None)),
    ("firefox::Personal", ("firefox", None, None, "Personal")),
    ("chrome+gnomekeyring:Profile 2", ("chrome", "Profile 2", "GNOMEKEYRING", None)),
])
def test_cookies_spec_is_split_like_yt_dlp_does(spec, expected):
    assert yt2html.cookies_spec(spec) == expected


def test_cookies_spec_errors_come_before_any_download(capsys):
    for bad in ("netscape", "firefox+wallet", ":x"):
        with pytest.raises(SystemExit):
            yt2html.parse_args(["u", "--cookies-from-browser", bad])
    assert "unsupported browser 'netscape'" in capsys.readouterr().err


def test_fetch_info_gives_yt_dlp_the_split_spec(monkeypatch, info):
    monkeypatch.setattr(yt2html, "add_watch_page_data", lambda ydl, i, cards=True: None)
    seen = {}

    class Fake:
        def __init__(self, opts): seen.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download): return dict(info)
        def sanitize_info(self, i): return i

    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", Fake)
    yt2html.fetch_info("u", yt2html.parse_args(["u", "--cookies-from-browser",
                                                 "firefox:fTW1hk7Y.Profile 1"]))
    assert seen["cookiesfrombrowser"] == ("firefox", "fTW1hk7Y.Profile 1", None, None)


def test_cookies_is_the_short_name(info, capsys):
    # a real second name, not only argparse's prefix matching
    with pytest.raises(SystemExit):
        yt2html.parse_args(["--help"])
    assert "--cookies-from-browser, --cookies BROWSER" in capsys.readouterr().out
    long = yt2html.parse_args(["u", "--cookies-from-browser", "firefox:fTW1hk7Y.Profile 1"])
    short = yt2html.parse_args(["u", "--cookies", "firefox:fTW1hk7Y.Profile 1"])
    assert short.cookies_from_browser == long.cookies_from_browser == "firefox:fTW1hk7Y.Profile 1"
    with pytest.raises(SystemExit):
        yt2html.parse_args(["u", "--cookies", "netscape"])
    # the Command row keeps the full name, the same for old and new pages
    info[yt2html.DOWNLOAD_KEY] = {"cookies_from_browser": yt2html.browser_name("firefox:x")}
    assert command(render(info)).endswith("--cookies-from-browser firefox")


def test_browser_name_has_no_profile():
    assert yt2html.browser_name("firefox") == "firefox"
    assert yt2html.browser_name("chrome:Profile 1") == "chrome"
    assert yt2html.browser_name("Firefox :fTW1hk7Y.Profile 1") == "firefox"
    assert yt2html.browser_name("firefox+gnomekeyring:/p::c") == "firefox"
    assert yt2html.browser_name(None) is None


# --------------------------------------------------------------------------
# Video page, Chapters, Info cards
# --------------------------------------------------------------------------

def test_video_page_status(info, fake_ydl):
    yt2html.add_watch_page_data(fake_ydl(webpage=watch_page({}, {})), info)
    assert info[yt2html.WATCH_PAGE_KEY] == "read"
    assert row(render(info), "Video page") == "read"
    yt2html.add_watch_page_data(fake_ydl(webpage=watch_page({})), info)
    assert info[yt2html.WATCH_PAGE_KEY] == "partial"
    assert row(render(info), "Video page") == \
        "read, without the player data (transcript language guessed)"

    class Broken(fake_ydl):
        def urlopen(self, url):
            raise OSError("offline")

    yt2html.add_watch_page_data(Broken(), info)
    assert info[yt2html.WATCH_PAGE_KEY] == "failed"
    text = row(render(info), "Video page")
    assert text.startswith("the video page could not be read: this copy may lack the AI summary")
    assert "the channel avatar only from the author's comments" in text


def test_markers_from_the_page(info):
    assert row(render(info), "Markers") == "chapters (3), from the description; walkthroughs (1)"
    info[yt2html.WATCH_PAGE_KEY] = "partial"  # only the player data is missing
    assert row(render(info), "Markers") == "chapters (3), from the description; walkthroughs (1)"
    info[yt2html.MARKERS_KEY][0]["source"] = "auto"
    assert row(render(info), "Markers").startswith("chapters (3), auto-generated by YouTube;")
    info[yt2html.MARKERS_KEY][0]["source"] = "uncertain"
    assert row(render(info), "Markers").startswith("chapters (3), origin unclear;")


def test_markers_without_chapters(info):
    info[yt2html.MARKERS_KEY] = info[yt2html.MARKERS_KEY][1:]          # Walkthroughs only
    info["description"] = "No times here."
    assert row(render(info), "Markers") == "no chapters; walkthroughs (1)"
    info[yt2html.MARKERS_KEY] = []
    info["description"] = "Plan:\n1:00 one\n2:00 two\n3:00 three\nFollow me at 12:00"
    assert row(render(info), "Markers") == \
        "no chapters; 4 lines of the description contain times, but YouTube made no chapters from them"
    info["description"] = "Lunch at 12:00\nDinner at 19:30"
    assert row(render(info), "Markers") == "none"                      # no list at all


def test_markers_when_the_video_page_could_not_be_read(info):
    info.update(dict.fromkeys(yt2html.PAGE_KEYS))
    info[yt2html.WATCH_PAGE_KEY] = "failed"
    info["chapters"] = [{"start_time": 0, "title": "A"}, {"start_time": 60, "title": "B"}]
    assert row(render(info), "Markers") == \
        "chapters (2), origin unknown (the video page could not be read)"
    info["chapters"] = []
    info["description"] = "1:00 a\n2:00 b\n3:00 c"
    assert row(render(info), "Markers") == \
        "none in the yt-dlp data (the video page could not be read)"


def card(kind, url="https://www.youtube.com/watch?v=x", start=5.0):
    return {"type": kind, "title": kind, "url": url, "start": start}


REDIRECT = "https://www.youtube.com/redirect?event=infocard&redir_token=x"


def test_info_cards_row(info):
    assert row(render(info), "Info cards") == "left out"
    info[yt2html.CARDS_KEY] = None
    assert row(render(info), "Info cards") == "unknown (the video page could not be read)"
    info[yt2html.CARDS_KEY] = []
    assert row(render(info), "Info cards") == "none"
    info[yt2html.CARDS_KEY] = [card("link", "https://example.org/")] * 3
    assert row(render(info), "Info cards") == "3 cards: 3 links (all to their websites)"
    info[yt2html.CARDS_KEY] = [card("channel"), card("video"), card("video"), card("video")]
    assert row(render(info), "Info cards") == "4 cards: 3 videos, 1 channel"  # fixed order


def test_info_cards_kinds_in_a_fixed_order_and_redirects(info):
    info[yt2html.CARDS_KEY] = [card("link", REDIRECT), card("video"), card("playlist"),
                               card("link", "https://example.org/"), card("video")]
    assert row(render(info), "Info cards") == \
        "5 cards: 2 videos, 1 playlist, 2 links (1 kept as a YouTube redirect)"
    info[yt2html.CARDS_KEY] = [card("link", REDIRECT), card("link", REDIRECT)]
    assert row(render(info), "Info cards") == "2 cards: 2 links (2 kept as YouTube redirects)"
    info[yt2html.CARDS_KEY] = [card("video")]
    assert row(render(info), "Info cards") == "1 card: 1 video"


def test_info_cards_without_times(info):
    info[yt2html.CARDS_KEY] = [card("channel", start=None), card("video")]
    assert row(render(info), "Info cards") == "2 cards: 1 video, 1 channel"  # one has a time
    info[yt2html.CARDS_KEY] = [card("channel", start=None), card("video", start=None)]
    assert row(render(info), "Info cards") == \
        "2 cards: 1 video, 1 channel; times not shown (YouTube's data did not match)"


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------

def test_transcript_left_out_or_missing(info):
    assert row(render(info, transcript_spec=[]), "Transcript") == "left out"
    del info[yt2html.TRANSCRIPT_KEY]
    assert row(render(info), "Transcript") == "left out"
    info[yt2html.TRANSCRIPT_KEY] = None
    info[yt2html.TRANSCRIPT_MISSING_KEY] = "could not be downloaded (HTTP Error 429)"
    assert row(render(info), "Transcript") == "none: could not be downloaded (HTTP Error 429)"


def test_transcript_language_features_and_versions(info):
    saved = info[yt2html.TRANSCRIPT_KEY].pop("translations")
    assert row(render(info), "Transcript") == \
        "English (original language, auto-generated); 1 version: plain"
    info[yt2html.TRANSCRIPT_KEY]["translations"] = saved
    info[yt2html.TRANSCRIPT_KEY]["translations"]["de"] = {"language": "de", "name": "German",
                                                          "cues": []}
    spec = [("timestamps", None), ("bilingual", "pl"), ("bilingual", "de")]
    assert row(render(info, transcript_spec=spec), "Transcript") == (
        "English (original language, auto-generated); 3 versions: timestamps, "
        "bilingual with Polish (auto-translated), bilingual with German (auto-translated)")


def test_the_row_describes_the_page_not_the_saved_data(info):
    # a translation kept in the saved data but not shown is not mentioned
    assert row(render(info, transcript_spec=[("timestamps", None)]), "Transcript") == \
        "English (original language, auto-generated); 1 version: timestamps"


def test_author_subtitles_of_an_unknown_original_language(info):
    info[yt2html.TRANSCRIPT_KEY].update(name="German", language="de", kind="manual",
                                        original=False, translations={})
    assert row(render(info), "Transcript") == \
        "German (by the author; original language unknown); 1 version: plain"


def test_fetch_transcript_keeps_the_reason_and_the_original_language(fake_ydl):
    info = {}
    assert yt2html.fetch_transcript(fake_ydl(), info) is None
    assert info[yt2html.TRANSCRIPT_MISSING_KEY] == "the video has no captions"
    english = {"automatic_captions": {"en-orig": caption_formats("English (Original)")}}
    assert yt2html.fetch_transcript(fake_ydl(), english)["original"] is True
    german = {"subtitles": {"de": caption_formats("German")}}  # original language unknown
    assert yt2html.fetch_transcript(fake_ydl(), german)["original"] is False
    other = {"subtitles": {"de": caption_formats("German")}, "language": "en"}
    assert yt2html.fetch_transcript(fake_ydl(), other)["original"] is False


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------

def test_comments_row(info):
    assert row(render(info), "Comments") == "7 of 9; 4 threads, 3 replies; complete"
    info[yt2html.LIMIT_KEY] = 7
    assert row(render(info), "Comments") == "7 of 9; 4 threads, 3 replies; limited to 7"
    info.update(comment_count=None)  # Ctrl+C
    info[yt2html.YT_COUNT_KEY] = None
    assert row(render(info), "Comments") == \
        "7 downloaded, YouTube's count unknown; 4 threads, 3 replies; interrupted (Ctrl+C)"


def test_comments_left_out_turned_off_or_none(info):
    info["comments"] = []
    info[yt2html.YT_COUNT_KEY] = "0"
    assert row(render(info), "Comments") == "no comments"
    info["comments"] = None
    assert row(render(info), "Comments") == "turned off or unavailable"
    del info["comments"]
    assert row(render(info), "Comments") == "left out"


# --------------------------------------------------------------------------
# Images, Software, Disclaimer
# --------------------------------------------------------------------------

def data_uri(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00\x04\x00\x00"                                   # a table, skipped
        b"\xff\xc0\x00\x11\x08\x02\xd0\x05\x00\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
        b"\xff\xd9")
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (480).to_bytes(4, "big") + (360).to_bytes(4, "big")
GIF = b"GIF89a" + (32).to_bytes(2, "little") + (16).to_bytes(2, "little")
WEBP_X = b"RIFF\x00\x00\x00\x00WEBPVP8X" + bytes(8) + (1279).to_bytes(3, "little") + (719).to_bytes(3, "little")
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + bytes(10) + (640).to_bytes(2, "little") + (480).to_bytes(2, "little")


def test_image_size():
    assert yt2html.image_size(data_uri(JPEG)) == (1280, 720)
    assert yt2html.image_size(data_uri(PNG, "image/png")) == (480, 360)
    assert yt2html.image_size(data_uri(GIF, "image/gif")) == (32, 16)
    assert yt2html.image_size(data_uri(WEBP_X, "image/webp")) == (1280, 720)
    assert yt2html.image_size(data_uri(WEBP, "image/webp")) == (640, 480)
    assert yt2html.image_size(data_uri(b"not an image")) is None
    assert yt2html.image_size("garbage") is None


def test_images_row(info):
    avatars = {c["author_thumbnail"]: "data:image/png;base64,AAAA"
               for c in info["comments"] if c.get("author_thumbnail")}
    html = render(info, thumbnail=data_uri(JPEG), avatars=avatars)
    assert row(html, "Images") == \
        "thumbnail 1280×720; channel avatar; comment avatars 5 of 5; no other images"
    del avatars["https://yt3.ggpht.test/@newest=s88-c-k-c0x00ffffff-no-rj"]
    info[yt2html.CARDS_KEY] = [{"type": "video", "image": "https://img.test/a"},
                               {"type": "video", "image": "https://img.test/b"}]
    html = render(info, avatars=avatars, extra_images={"https://img.test/a": "data:,x"})
    assert row(html, "Images") == \
        "no thumbnail; channel avatar; comment avatars 4 of 5 (1 shown as letters); other images 1 of 2"
    text = row(render(info, no_avatars=True), "Images")
    assert "channel avatar shown as a letter; comment avatars left out" in text
    info["comments"] = []                      # no comments, no channel avatar known
    assert row(render(info), "Images") == \
        "no thumbnail; no channel avatar; no comment avatars; other images 0 of 2"


def test_software_and_disclaimer(info):
    html = render(info)
    assert row(html, "Software") == (
        f"yt2html {yt2html.__version__}, © 2026 Mateusz Wisłowski, MIT License · yt-dlp "
        f"{yt2html.yt_dlp.version.__version__}, Unlicense (public domain); "
        "these licenses cover the programs, not the content of this copy.")
    assert 'href="https://github.com/mwislowski/yt-video-archiver"' in html
    assert row(html, "Disclaimer").startswith("The video, its texts and images belong")
    assert "responsible for complying with YouTube&#x27;s terms and the law." in html


@pytest.mark.parametrize("theme", ["auto", "dark", "light"])
def test_no_option_names_outside_the_command(info, theme):
    info[yt2html.LIMIT_KEY] = 5
    info["formats"] = video_formats_example()
    html = render(info, theme=theme, no_avatars=True, show_snapshot=False)
    rest = re.sub(r'<code class="command">.*?</code>', "", html)
    rest = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", rest, flags=re.S)
    rest = re.sub(r'style="[^"]*"', "", rest)  # CSS variables such as --hue
    assert not re.search(r"--[a-z]", rest)


# --------------------------------------------------------------------------
# Comments narrowed when building from saved data: the rows show the result
# --------------------------------------------------------------------------

def rebuild(capsys, tmp_path, info, *options):
    saved = tmp_path / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    out_dir = tmp_path / f"p{len(list(tmp_path.glob('p*')))}"
    code = yt2html.main(["--from-json", str(saved), "-o", str(out_dir), *options])
    out = capsys.readouterr().out
    return code, out, next(out_dir.glob("*.html")).read_text("utf-8")


def test_max_comments_keeps_the_first_saved_ones(capsys, tmp_path, info):
    info[yt2html.CARDS_KEY] = []
    code, out, html = rebuild(capsys, tmp_path, info, "--max-comments", "3")
    assert code == 0
    assert row(html, "Comments") == "3 of 9; 3 threads, 0 replies; limited to 3"
    assert command(html) == "yt2html TESTVIDEO01 --max-comments 3"
    # the first three in yt-dlp's order: pinned, newest, middle (without its replies)
    assert "Pinned by the creator" in html and "Middle comment" in html
    assert "Oldest comment" not in html and "@middle first reply" not in html


def test_a_cut_after_ctrl_c_counts_as_limited(capsys, tmp_path, info):
    info.update(comment_count=None)  # interrupted download
    info[yt2html.LIMIT_KEY] = None
    _, _, html = rebuild(capsys, tmp_path, info, "--max-comments", "5")
    assert row(html, "Comments").endswith("; limited to 5")
    code, out, html = rebuild(capsys, tmp_path, info, "--max-comments", "100")
    assert row(html, "Comments").endswith("; interrupted (Ctrl+C)")
    assert "The saved data has only 7 comments; download again" in out


def test_a_larger_limit_cannot_widen_the_saved_data(capsys, tmp_path, info):
    info["comments"] = info["comments"][:5]
    info[yt2html.LIMIT_KEY] = 5
    _, out, html = rebuild(capsys, tmp_path, info, "--max-comments", "50")
    assert "The saved data has only 5 comments; download again" in out
    assert row(html, "Comments").endswith("; limited to 5")
    assert "--max-comments 5" in command(html)
    _, out, _ = rebuild(capsys, tmp_path, dict(info, comments=info["comments"], **{
        yt2html.LIMIT_KEY: None}), "--max-comments", "50")  # complete: nothing to say
    assert "saved data has only" not in out


def test_no_comments_leaves_out_the_saved_ones(capsys, tmp_path, info):
    info[yt2html.CARDS_KEY] = []
    _, _, html = rebuild(capsys, tmp_path, info, "--no-comments")
    assert "<h2>Comments</h2>" not in html
    assert row(html, "Comments") == "left out"
    assert command(html) == "yt2html TESTVIDEO01 --no-comments"


def test_cookies_have_no_effect_when_building(capsys, tmp_path, info):
    _, out, _ = rebuild(capsys, tmp_path, info, "--cookies-from-browser", "firefox")
    assert "Note: --cookies-from-browser has no effect with --from-json" in out



# --------------------------------------------------------------------------
# Video formats (the section above About this copy) and the Video file row
# --------------------------------------------------------------------------

MB = 1_000_000


def vfmt(fid, height, vcodec, size, fps=25, **extra):
    return {"format_id": fid, "ext": "mp4", "height": height, "vcodec": vcodec,
            "acodec": "none", "fps": fps, "filesize": size, **extra}


def afmt(fid, acodec, size, language="en", preference=10, **extra):
    return {"format_id": fid, "ext": "m4a" if acodec.startswith("mp4a") else "webm",
            "vcodec": "none", "acodec": acodec, "filesize": size, "language": language,
            "language_preference": preference, **extra}


def video_formats_example():
    return [
        {"format_id": "sb0", "ext": "mhtml", "format_note": "storyboard", "vcodec": "none",
         "acodec": "none", "height": 90},
        vfmt("18", 360, "avc1.42001E", 30 * MB, acodec="mp4a.40.2"),   # combined, old
        vfmt("134", 360, "avc1.4D401E", 20 * MB),
        vfmt("243", 360, "vp9", 15 * MB),
        vfmt("137", 1080, "avc1.640028", 400 * MB),
        vfmt("299", 1080, "avc1.64002A", 600 * MB, fps=50),
        vfmt("399", 1080, "av01.0.08M.08", 300 * MB),
        vfmt("616", 1080, "vp09.00.40.08", 900 * MB, format_note="Premium"),
        vfmt("337", 2160, "vp09.02.51.10", 2_500 * MB, dynamic_range="HDR10"),
        afmt("140", "mp4a.40.2", 9 * MB),
        afmt("251", "opus", 8 * MB),
        afmt("251-drc", "opus", 12 * MB),
        afmt("140-1", "mp4a.40.2", 9 * MB, language="pl", preference=-1),
    ]


def formats_section(html):
    """(summary text, inner HTML) of the Video formats section, or None."""
    match = re.search(r'<details class="formats-info"><summary>(.*?)</summary>(.*?)</details>',
                      html, re.S)
    return (re.sub(r"<[^>]+>", "", match.group(1)), match.group(2)) if match else None


def video_tables(info, **options):
    """The tables of the Video formats section as {name: (caption, rows of
    cell texts)}, and the text of the Video file row."""
    html = render(info, **options)
    section = formats_section(html)
    tables = {}
    for caption, body in re.findall(r"<caption>(.*?)</caption>(.*?)</table>",
                                    section[1] if section else ""):
        rows = [[re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr)]
                for tr in re.findall(r"<tr>(.*?)</tr>", body)]
        tables[re.split(r" \(|;", caption)[0]] = (caption, rows)
    return tables, row(html, "Video file")


def summary(info):
    return formats_section(render(info))[0]


def translation(info):
    return re.search(r'<div class="translation">(.*?)</div>', render(info)).group(1)


SUBS = ["subtitles by the author", "auto-generated subtitles"]


def picked(info, **options):
    """The texts of the marked cells, per table."""
    html = render(info, **options)
    found = {}
    for caption, body in re.findall(r"<caption>(.*?)</caption>(.*?)</table>", html):
        cells = re.findall(r'<td class="[^"]*\bpick\b[^"]*">(.*?)</td>', body)
        if cells:
            found[caption.split(" (")[0].split(";")[0]] = cells
    return found


def test_video_formats_section_above_about_and_the_video_file_row(info):
    info["formats"] = video_formats_example()
    html = render(info)
    assert html.index('<details class="formats-info">') < html.index('<details class="copy-info">')
    assert re.findall(r"<dt>(.*?)</dt>", about(html)) == ROWS
    assert "--video" not in command(html) and "experimental" not in html
    # no formats: no section, the row says so
    del info["formats"]
    html = render(info)
    assert formats_section(html) is None and 'class="formats-info"' not in html
    assert row(html, "Video file") == "not downloaded; no formats in the data"


def test_video_formats_describe_image_audio_subtitles_and_the_default(info):
    info["formats"] = video_formats_example()
    info["subtitles"] = {"en": [{"ext": "vtt"}], "live_chat": [{"ext": "json"}], "de": []}
    info["automatic_captions"] = {"en-orig": [{}], "en": [{}], "pl": [{}], "fr": [{}]}
    tables, rest = video_tables(info)
    assert list(tables) == ["image", "languages"]
    assert tables["image"] == ("image (sizes include the audio: en aac); "
                               "marked: the planned default", [
        ["quality", "resolution", "h264", "av1", "vp9", "vp9 Premium"],
        ["2160p, 25 fps", "–", "–", "–", "~2.5 GB, HDR10", "–"],
        ["1080p, 50 fps (1080p50)", "–", "~609 MB", "–", "–", "–"],    # one row per frame rate
        ["1080p, 25 fps (1080p25)", "–", "~409 MB", "~309 MB", "–", "~909 MB"],
        ["360p, 25 fps (360p25)", "–", "~29 MB", "–", "~24 MB", "–"],
    ])
    # audio and subtitles in one table; the author's "de" has no formats: left out
    assert tables["languages"] == ("languages; marked: the planned default and the "
                                   "transcript's subtitles", [
        ["code", "language", "audio", "aac", "opus", *SUBS],
        ["en", "en", "original", "~9 MB", "~8 MB", "en", "en"],
        ["pl", "pl", "dubbed", "~9 MB", "–", "–", "–"]])
    # the transcript takes the author's English: marked in its cell
    assert '<td><span class="pick">en</span></td><td>en</td>' in render(info)
    assert translation(info) == "machine translation into 2 languages"   # pl, fr
    # the best quality up to 1080p first (here 50 fps, only in h264), then its codec
    assert rest == ("not downloaded; planned default: 1080p, 50 fps (1080p50) h264 + en aac, "
                    "mp4, ~609 MB; Deno at download: unknown (data saved by an older version)")
    assert picked(info) == {"image": ["~609 MB"], "languages": ["~9 MB"]}
    assert summary(info) == ("Video formats · up to 2160p, 25 fps · audio: en original + "
                             "1 dubbed · subtitles: en by the author + en auto-generated + "
                             "machine translation into 2 languages")
    html = render(info)   # sizes right-aligned, the rest wraps
    assert '<th class="size">h264</th>' in html and "<th>quality</th>" in html
    assert '<td class="size">~909 MB</td>' in html and "<td>dubbed</td>" in html
    assert '<td class="size pick">~609 MB</td>' in html and ".pick{color:var(--pick)}" in html


def test_video_formats_without_an_original_language_or_runtime(info):
    info["formats"] = [vfmt("137", 1080, "avc1.640028", None, tbr=1000),   # size from bitrate
                       afmt("140", "mp4a.40.2", 5 * MB, language=None, preference=None)]
    info["duration"] = 600   # 1000 kbit/s * 600 s = 75 MB
    info[yt2html.DOWNLOAD_KEY] = {"deno": False}
    tables, rest = video_tables(info)
    assert tables["image"] == ("image (sizes include the audio: aac); "   # no language
                               "marked: the planned default",
                               [["quality", "resolution", "h264"], ["1080p, 25 fps (1080p25)", "–", "~80 MB"]])
    # no language in the data: no code, "unknown"; a single track: the original
    assert tables["languages"][1][1] == ["–", "unknown", "original", "~5 MB", "–", "–"]
    assert translation(info) == "machine translation: none"
    assert summary(info) == "Video formats · up to 1080p, 25 fps · audio: original · no subtitles"
    assert rest == ("not downloaded; planned default: 1080p, 25 fps (1080p25) h264 + aac, "
                    "mp4, ~80 MB; Deno at download: "
                    "no, so YouTube may have offered more formats than listed")
    info[yt2html.DOWNLOAD_KEY] = {"deno": True}
    assert video_tables(info)[1].endswith("Deno at download: yes")
    info[yt2html.DOWNLOAD_KEY] = {"deno": True, "cookies_from_browser": "firefox"}
    assert video_tables(info)[1].endswith(
        "Deno at download: yes; signed in, so YouTube offered no HLS formats")
    info[yt2html.DOWNLOAD_KEY] = {"yt2html": "0.6.0"}   # saved before the field existed
    assert video_tables(info)[1].endswith(
        "Deno at download: unknown (data saved by an older version)")
    del info[yt2html.DOWNLOAD_KEY]
    assert video_tables(info)[1].endswith(
        "Deno at download: unknown (data saved by an older version)")
    info["automatic_captions"] = {"en-orig": [{}], "en": [{}]}   # en = en-orig, no translation
    # automatic captions of the only audio track, of an unknown language: its row
    assert video_tables(info)[0]["languages"][1][1][-1] == "en"
    assert translation(info) == "machine translation: none"
    # many languages: counted in the summary
    info["subtitles"] = {lang: [{}] for lang in ("en", "de", "fr", "it")}
    info["formats"].append(afmt("140-1", "mp4a.40.2", 5 * MB, language="pl", preference=None))
    assert summary(info).endswith(
        "2 audio tracks · subtitles: 4 by the author + en auto-generated")
    info["subtitles"] = {lang: [{}] for lang in ("en", "de", "fr")}   # up to three: named
    assert summary(info).endswith("subtitles: en, de, fr by the author + en auto-generated")


def kind(acont, lang="en"):
    """A format address with YouTube's kind of audio track, as in real data."""
    return {"url": "https://rr3.googlevideo.com/videoplayback?expire=1&itag=140"
                   f"&xtags=acont%3D{acont}%3Alang%3D{lang}&mime=audio%2Fmp4"}


def test_video_formats_order_rows_by_language_then_role(info):
    """Roles from YouTube's kind of track: original, dubbed (by the author),
    auto-dubbed (YouTube's automatic dubbing), descriptive (audio
    description), then kinds YouTube may add, as named. Rows: the original's
    language first, then the other languages alphabetically; within a
    language by role. One language may have two tracks."""
    info["formats"] = [
        vfmt("137", 1080, "avc1.640028", 100 * MB),
        afmt("140-0", "mp4a.40.2", 9 * MB, "pl", -1, **kind("dubbed-auto", "pl")),
        afmt("140-1", "mp4a.40.2", 9 * MB, "es", -1, **kind("dubbed", "es")),
        afmt("140-2", "mp4a.40.2", 12 * MB, "en", -1, **kind("descriptive")),
        afmt("140-3", "mp4a.40.2", 9 * MB, "ja", -1, **kind("commentary", "ja")),
        afmt("140-8", "mp4a.40.2", 9 * MB, "ab", -1, **kind("voice%3Cover", "ab")),
        afmt("140-4", "mp4a.40.2", 9 * MB, "ar", -1, **kind("dubbed-auto", "ar")),
        afmt("140-5", "mp4a.40.2", 9 * MB, "de", -1, **kind("dubbed", "de")),
        afmt("140-6", "mp4a.40.2", 9 * MB, "it", -1),        # no kind: dubbed (an original is known)
        afmt("140-7", "mp4a.40.2", 9 * MB, "de", -10),       # no kind, yt-dlp's -10: a description
        # the original known only from its kind (yt-dlp gave no preference)
        afmt("140", "mp4a.40.2", 7 * MB, preference=-1, **kind("original")),
        afmt("251", "opus", 8 * MB, preference=-1, **kind("original")),   # larger, still aac
    ]
    tables, rest = video_tables(info)
    assert [[r[0], r[2], r[3]] for r in tables["languages"][1]] == [
        ["code", "audio", "aac"], ["en", "original", "~7 MB"], ["en", "descriptive", "~12 MB"],
        ["ab", "voice&lt;over", "~9 MB"], ["ar", "auto-dubbed", "~9 MB"],
        ["de", "dubbed", "~9 MB"], ["de", "descriptive", "~9 MB"], ["es", "dubbed", "~9 MB"],
        ["it", "dubbed", "~9 MB"], ["ja", "commentary", "~9 MB"], ["pl", "auto-dubbed", "~9 MB"]]
    # the main audio is the original in aac, not the (larger) English description
    assert picked(info)["languages"] == ["~7 MB"]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + en aac, mp4, ~107 MB" in rest
    assert summary(info) == ("Video formats · up to 1080p, 25 fps · audio: en original + "
                             "3 dubbed + 2 auto-dubbed + 2 descriptive + 1 commentary + "
                             "1 voice&lt;over · no subtitles")
    # no original known: by language; the mark follows its row
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 100 * MB),
                       afmt("140-0", "mp4a.40.2", 5 * MB, "pl", -1),
                       afmt("140-1", "mp4a.40.2", 9 * MB, "es", -1, **kind("secondary", "es")),
                       afmt("140-2", "mp4a.40.2", 8 * MB, "de", -1, **kind("dubbed", "de"))]
    rows = video_tables(info)[0]["languages"][1]
    assert [[r[0], r[2], r[3]] for r in rows[1:]] == [["de", "dubbed", "~8 MB"], ["es", "secondary", "~9 MB"],
                        ["pl", "–", "~5 MB"]]
    assert picked(info)["languages"] == ["~5 MB"]           # the first track in the data
    assert "· 3 audio tracks ·" in summary(info)
    # a single track of another kind: not the original
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 100 * MB),
                       afmt("140", "mp4a.40.2", 5 * MB, "pl", -1, **kind("secondary", "pl"))]
    assert "· 1 audio track ·" in summary(info)


def test_video_formats_main_audio_avoids_audio_descriptions(info):
    """Without an ordinary original, the main audio is an ordinary track; an
    audio description only when there is nothing else."""
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 100 * MB),
                       afmt("141", "mp4a.40.2", 18 * MB, format_note="Premium", **kind("original")),
                       afmt("140-1", "mp4a.40.2", 12 * MB, "en", -1, **kind("descriptive")),
                       afmt("140-2", "mp4a.40.2", 9 * MB, "pl", -1, **kind("dubbed", "pl"))]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + pl aac, mp4, ~109 MB" in (
        video_tables(info)[1])
    info["formats"] = info["formats"][:3]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + en aac, mp4, ~112 MB" in (
        video_tables(info)[1])


def test_audio_kind_from_the_format_address():
    assert yt2html.audio_kind(kind("dubbed-auto", "pl")) == "dubbed-auto"
    assert yt2html.audio_kind(   # HLS: a path part of the playlist address
        {"url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/itag/234/"
                "xtags/acont%3Doriginal%3Alang%3Den-US/index.m3u8"}) == "original"
    assert yt2html.audio_kind(
        {"manifest_url": "https://x/hls_variant/xtags/acont%3Ddescriptive/file/index.m3u8"}
    ) == "descriptive"
    assert yt2html.audio_kind({"url": "https://x/videoplayback?xtags=drc%3D1&itag=251"}) is None
    assert yt2html.audio_kind({"url": None}) is None and yt2html.audio_kind({}) is None


def test_video_formats_edge_cases(info):
    # only combined formats (old videos): they count; no audio-only, nothing marked
    info["formats"] = [vfmt("18", 360, "avc1.42001E", 30 * MB, acodec="mp4a.40.2")]
    tables, rest = video_tables(info)
    assert tables["image"] == ("image", [["quality", "resolution", "h264"], ["360p, 25 fps (360p25)", "–", "~30 MB"]])
    assert "audio" not in tables and picked(info) == {}
    assert rest.startswith("not downloaded; planned default: nothing (no av1 or h264 image "
                           "up to 1080p with audio); ")
    assert summary(info) == "Video formats · up to 360p, 25 fps · no subtitles"
    # no image at all
    info["formats"] = [afmt("140", "mp4a.40.2", 9 * MB)]
    tables, rest = video_tables(info)
    assert list(tables) == ["languages"]
    assert summary(info) == "Video formats · audio: en original · no subtitles"
    # unknown size and an unknown codec
    info["formats"] = [vfmt("1", 720, "xyz1.2", None), afmt("2", "opus", None)]
    info.pop("duration", None)
    tables, rest = video_tables(info)
    assert tables["image"][1] == [["quality", "resolution", "xyz1"], ["720p, 25 fps", "–", "size unknown"]]
    assert tables["languages"][1][1] == ["en", "en", "original", "size unknown", "–", "–"]
    assert "planned default: nothing" in rest
    # no frame rate in the data: the height alone
    info["formats"] = [vfmt("1", 720, "avc1.4d401f", 10 * MB, fps=None)]
    assert video_tables(info)[0]["image"][1][1] == ["720p (720p)", "–", "~10 MB"]
    assert summary(info) == "Video formats · up to 720p · no subtitles"


def test_sizes_and_codec_names():
    assert yt2html.human_size(None) == "size unknown"
    assert yt2html.human_size(999_999) == "<1 MB" and yt2html.human_size(1_000_000) == "~1 MB"


def test_small_sizes_are_escaped_on_the_page(info):
    """"<1 MB" is text: an entity on the page, plain on the screen."""
    info["formats"] = [vfmt("160", 144, "avc1.4d400c", 300_000),
                       afmt("140", "mp4a.40.2", 400_000)]
    html = render(info, video_files=["x [TESTVIDEO01].mp4"], video_size=500_000)
    section = formats_section(html)[1]
    assert section.count("&lt;1 MB") == 2 and "<1 MB" not in section   # image and audio cells
    assert "x [TESTVIDEO01].mp4, &lt;1 MB;" in html
    assert "h264 + en aac, mp4, &lt;1 MB;" in render(info)   # the planned default in Video file
    assert yt2html.format_list(info)[2].endswith("h264  <1 MB  (default)")
    assert yt2html.human_size(999_999_999) == "~1000 MB"
    assert yt2html.human_size(1_000_000_000) == "~1.0 GB"
    assert yt2html.codec_name("hvc1.2.4", yt2html.VIDEO_CODECS) == "h265"
    assert yt2html.codec_name("none", yt2html.VIDEO_CODECS) is None
    assert yt2html.codec_name(None, yt2html.AUDIO_CODECS) is None
    assert yt2html.format_size({}, {"filesize_approx": 7}) == 7
    assert yt2html.format_size({"duration": 8}, {"filesize": 5, "tbr": 1}) == 5
    assert yt2html.format_size({"duration": 8}, {"tbr": 1}) == 1000


def test_video_formats_with_saved_data(capsys, tmp_path, info):
    info["formats"] = video_formats_example()
    code, out, html = rebuild(capsys, tmp_path, info)
    assert code == 0 and formats_section(html)
    assert "planned default: 1080p, 50 fps (1080p50) h264" in row(html, "Video file")


def test_video_formats_mark_premium_formats(info):
    """Signed in with YouTube Premium, yt-dlp gets formats marked "Premium":
    own columns after the ordinary ones of the same codec, never in the default."""
    info["formats"] = [
        vfmt("137", 1080, "avc1.640028", 400 * MB),
        vfmt("9137", 1080, "avc1.640028", 800 * MB, format_note="1080p Premium"),
        vfmt("248", 1080, "vp9", 300 * MB),
        vfmt("616", 1080, "vp09.00.40.08", 900 * MB, format_note="1080p Premium"),
        vfmt("9720", 720, "avc1.4d401f", 100 * MB, format_note="Premium"),   # only Premium
        afmt("140", "mp4a.40.2", 9 * MB),
        afmt("141", "mp4a.40.2", 18 * MB, format_note="English original (default), Premium"),
        afmt("774", "opus", 17 * MB, format_note="English original (default), Premium"),
    ]
    tables, rest = video_tables(info)
    assert tables["image"][1] == [["quality", "resolution", "h264", "h264 Premium", "vp9", "vp9 Premium"],
                                  ["1080p, 25 fps (1080p25)", "–", "~409 MB", "~809 MB", "~309 MB",
                                   "~909 MB"],
                                  ["720p, 25 fps", "–", "–", "~109 MB", "–", "–"]]
    assert tables["languages"][1] == [["code", "language", "audio", "aac", "aac Premium", "opus Premium",
                                       *SUBS],
                                  # the name from yt-dlp's note of the Premium formats
                                  ["en", "English", "original", "~9 MB", "~18 MB", "~17 MB", "–", "–"]]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + en aac, mp4, ~409 MB" in rest
    # with Premium audio only, the main audio is still an ordinary one
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 400 * MB),
                       afmt("141", "mp4a.40.2", 18 * MB, format_note="Premium"),
                       afmt("251", "opus", 8 * MB)]
    tables, rest = video_tables(info)
    assert tables["image"][1][1] == ["1080p, 25 fps (1080p25)", "–", "~408 MB"]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + en opus, mp4, ~408 MB" in rest
    # the original only in Premium: an ordinary dub is the main audio, never Premium
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 400 * MB),
                       afmt("141", "mp4a.40.2", 18 * MB, format_note="Premium"),
                       afmt("140-1", "mp4a.40.2", 9 * MB, language="pl", preference=-1)]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + pl aac, mp4, ~409 MB" in (
        video_tables(info)[1])


def test_video_formats_name_hls_formats_without_sizes(info):
    """HLS gives only a declared bitrate (far too high in practice), so its
    formats are named, never sized, and never used for sizes or the default."""
    hls = {"protocol": "m3u8_native"}
    info["duration"] = 4269
    info["formats"] = [
        vfmt("137", 1080, "avc1.640028", 147 * MB, protocol="https"),
        vfmt("270", 1080, "avc1.640028", None, tbr=3262, **hls),
        vfmt("614", 1080, "vp09.00.40.08", None, tbr=3189, **hls),
        vfmt("616", 1080, "vp09.00.40.08", None, tbr=5126, format_note="Premium", **hls),
        vfmt("625", 2160, "vp09.00.50.08", None, tbr=12000, fps=60, **hls),   # only in HLS
        afmt("140", "mp4a.40.2", 69 * MB, protocol="https"),
        afmt("234-24", "mp4a.40.2", None, tbr=192, **hls),
    ]
    tables, rest = video_tables(info)
    assert tables["image"][1] == [["quality", "resolution", "h264", "HLS (sizes unknown)"],
                                  ["2160p", "–", "–", "vp9 60 fps"],   # only HLS: no parameter
                                  ["1080p, 25 fps (1080p25)", "–", "~216 MB",
                                   "h264, vp9, vp9 Premium"]]
    assert tables["languages"][1][1] == ["en", "en", "original", "~69 MB", "–", "–"]
    assert "planned default: 1080p, 25 fps (1080p25) h264 + en aac, mp4, ~216 MB" in rest
    assert summary(info).startswith("Video formats · up to 2160p, 60 fps · ")   # HLS counts
    html = render(info)   # the HLS column is text: not right-aligned
    assert "<th>HLS (sizes unknown)</th>" in html and "<td>vp9 60 fps</td>" in html
    # no HLS: no column
    info["formats"] = info["formats"][:1] + info["formats"][5:6]
    assert video_tables(info)[0]["image"][1] == [["quality", "resolution", "h264"],
                                                 ["1080p, 25 fps (1080p25)", "–", "~216 MB"]]
    # HLS heights without one at some height: an empty cell
    info["formats"].append(vfmt("229", 360, "avc1.4d401e", None, **hls))
    info["formats"].append(vfmt("134", 360, "avc1.4d401e", 20 * MB))
    info["formats"].append(vfmt("135", 480, "avc1.4d401f", 30 * MB))
    assert video_tables(info)[0]["image"][1][1:] == [["1080p, 25 fps (1080p25)", "–", "~216 MB", "–"],
                                                     ["480p, 25 fps (480p25)", "–", "~99 MB", "–"],
                                                     ["360p, 25 fps (360p25)", "–", "~89 MB", "h264"]]
    # two frame rates at one height: HLS only in the first row
    info["formats"].append(vfmt("298", 360, "avc1.4d401f", 40 * MB, fps=50))
    assert video_tables(info)[0]["image"][1][3:] == [["360p, 50 fps (360p50)", "–", "~109 MB", "h264"],
                                                     ["360p, 25 fps (360p25)", "–", "~89 MB", "–"]]
    # only HLS image: no sizes, nothing marked, but the summary has its quality
    info["formats"] = [vfmt("270", 1080, "avc1.640028", None, tbr=3262, **hls),
                       afmt("140", "mp4a.40.2", 69 * MB)]
    assert summary(info).startswith("Video formats · up to 1080p, 25 fps · ")
    # only HLS audio: still listed
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 147 * MB),
                       afmt("234-24", "mp4a.40.2", 9 * MB, **hls)]
    assert video_tables(info)[0]["languages"][1][1] == ["en", "en", "original", "~9 MB", "–", "–"]
    assert yt2html.is_hls({"protocol": "m3u8"}) and not yt2html.is_hls({})


def test_video_formats_take_the_larger_of_two_alike_formats(info):
    """Two formats of one codec, height and frame rate (e.g. from two YouTube
    clients): the larger, i.e. higher-bitrate one, stands for them."""
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 100 * MB),
                       vfmt("137-1", 1080, "avc1.640028", 150 * MB),
                       afmt("140", "mp4a.40.2", 5 * MB), afmt("140-1", "mp4a.40.2", 9 * MB)]
    tables, rest = video_tables(info)
    assert tables["image"][1][1] == ["1080p, 25 fps (1080p25)", "–", "~159 MB"]
    assert tables["languages"][1][1] == ["en", "en", "original", "~9 MB", "–", "–"]


def test_video_formats_mark_the_planned_default(info):
    """The planned default: the best quality up to 1080p that has AV1 or H.264,
    then AV1 there, else H.264 (never vp9 or Premium); one cell marked in the
    image table, the main audio (original, aac) in the audio table."""
    info["formats"] = [
        vfmt("401", 2160, "av01.0.12M.08", 900 * MB),
        vfmt("137", 1080, "avc1.640028", 400 * MB),
        vfmt("399", 1080, "av01.0.08M.08", 200 * MB),
        vfmt("9399", 1080, "av01.0.08M.08", 600 * MB, format_note="Premium"),
        vfmt("136", 720, "avc1.4d401f", 150 * MB),
        vfmt("398", 720, "av01.0.05M.08", 90 * MB),
        vfmt("247", 720, "vp9", 100 * MB),
        afmt("251", "opus", 8 * MB),
        afmt("140", "mp4a.40.2", 9 * MB),
        afmt("140-1", "mp4a.40.2", 9 * MB, language="pl", preference=-1),
    ]
    assert picked(info) == {"image": ["~209 MB"], "languages": ["~9 MB"]}
    assert ("planned default: 1080p, 25 fps (1080p25) av1 + en aac, mp4, ~209 MB"
            in video_tables(info)[1])
    # the original without aac: its opus, not a dub's aac
    info["formats"] = [f for f in info["formats"] if f["format_id"] != "140"]
    assert picked(info)["languages"] == ["~8 MB"]
    assert "1080p, 25 fps (1080p25) av1 + en opus, mp4, ~208 MB" in video_tables(info)[1]
    # an audio codec without a column (e.g. Dolby ec-3): nothing marked there
    info["formats"] = [vfmt("399", 1080, "av01.0.08M.08", 200 * MB),
                       afmt("380", "ec-3", 9 * MB)]
    assert picked(info) == {"image": ["~209 MB"]}
    assert video_tables(info)[0]["languages"][0] == "languages"   # nothing marked: no note
    # the quality first: 1080p only in H.264 beats 720p in AV1
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 400 * MB),
                       vfmt("398", 720, "av01.0.05M.08", 90 * MB), afmt("140", "mp4a.40.2", 9 * MB)]
    assert picked(info)["image"] == ["~409 MB"]
    # AV1 only above 1080p: not taken
    info["formats"] = [vfmt("401", 2160, "av01.0.12M.08", 900 * MB),
                       vfmt("264", 1440, "avc1.640032", 700 * MB),
                       vfmt("137", 1080, "avc1.640028", 400 * MB), afmt("140", "mp4a.40.2", 9 * MB)]
    assert ("planned default: 1080p, 25 fps (1080p25) h264 + en aac, mp4, ~409 MB"
            in video_tables(info)[1])
    assert [r[0] for r in video_tables(info)[0]["image"][1][1:]] == [   # above 1080p: no parameter
        "2160p, 25 fps", "1440p, 25 fps", "1080p, 25 fps (1080p25)"]
    # a quality with only vp9 is skipped; nothing up to 1080p: nothing marked
    info["formats"] = [vfmt("248", 1080, "vp9", 300 * MB), vfmt("135", 480, "avc1.4d401f", 70 * MB),
                       afmt("140", "mp4a.40.2", 9 * MB)]
    assert picked(info)["image"] == ["~79 MB"]
    assert [r[0] for r in video_tables(info)[0]["image"][1][1:]] == [   # vp9 only: no parameter
        "1080p, 25 fps", "480p, 25 fps (480p25)"]
    info["formats"] = [vfmt("401", 2160, "av01.0.12M.08", 900 * MB),
                       vfmt("244", 480, "vp9", 50 * MB), afmt("251", "opus", 8 * MB)]
    tables, rest = video_tables(info)
    assert tables["image"][0] == "image (sizes include the audio: en opus)"
    assert tables["languages"][0] == "languages; marked: the planned default"
    assert "planned default: nothing (no av1 or h264 image up to 1080p with audio)" in rest
    assert picked(info) == {"languages": ["~8 MB"]}


def test_video_formats_name_the_audio_languages(info):
    """The language column: yt-dlp's English note of a single-file format,
    else YouTube's list of translation languages, else the code."""
    hls = {"protocol": "m3u8_native"}
    info[yt2html.TRANSLATIONS_KEY] = {"pt-BR": "Portuguese (Brazil)", "de": "German",
                                      "iw": "Hebrew", "fr": "French"}
    info["formats"] = [
        vfmt("137", 1080, "avc1.640028", 100 * MB),
        afmt("140", "mp4a.40.2", 9 * MB, "en-US", format_note="English (US) original (default), medium"),
        afmt("140-1", "mp4a.40.2", 9 * MB, "ar", -1, format_note="Arabic, low"),
        afmt("140-2", "mp4a.40.2", 9 * MB, "pt-BR", -1, format_note="medium"),   # no name in it
        afmt("140-3", "mp4a.40.2", 9 * MB, "de-DE", -1),                         # the language alone
        afmt("140-4", "mp4a.40.2", 9 * MB, "iw", -1, format_note="iw, low"),     # just the code
        afmt("140-5", "mp4a.40.2", 9 * MB, "xx", -1),                            # nothing: the code
        afmt("140-6", "mp4a.40.2", 9 * MB, "ko", -1, format_note="K<o>rean, low"),
        afmt("140-7", "mp4a.40.2", 9 * MB, "en", -10, format_note="English descriptive, low"),
    ]
    rows = video_tables(info)[0]["languages"][1]
    assert [r[:3] for r in rows] == [
        ["code", "language", "audio"], ["en-US", "English (US)", "original"],
        ["en", "English", "descriptive"],   # the original's language first
        ["ar", "Arabic", "dubbed"], ["de-DE", "German", "dubbed"], ["iw", "Hebrew", "dubbed"],
        ["ko", "K&lt;o&gt;rean", "dubbed"], ["pt-BR", "Portuguese (Brazil)", "dubbed"],
        ["xx", "xx", "dubbed"]]
    # HLS notes are in the track's own language: not used
    assert yt2html.language_name(info, [afmt("234", "mp4a.40.2", None, "fr", format_note=
                                             "Français - dubbed-auto", **hls)], "fr") == "French"
    assert yt2html.language_name({}, [], "pl") == "pl"


def test_video_file_row_with_a_video_next_to_the_page(capsys, tmp_path, info):
    info["formats"] = video_formats_example()
    html = render(info, video_files=["My title [TESTVIDEO01].mp4"], video_size=293_000_000)
    assert row(html, "Video file") == ("a video file next to the page: My title [TESTVIDEO01].mp4, "
                                       "293 MB; Deno at download: unknown (data saved by an older "
                                       "version)")
    del info["formats"]
    assert row(render(info, video_files=["a&b [TESTVIDEO01].webm"]), "Video file") == (
        "a video file next to the page: a&amp;b [TESTVIDEO01].webm; no formats in the data")
    # when the page is made, the file is found by the id and measured
    info["formats"] = video_formats_example()
    saved = tmp_path / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    (tmp_path / "Renamed [TESTVIDEO01].mp4").write_bytes(b"x" * 2_500_000)
    assert yt2html.main(["--from-json", str(saved), "-o", str(tmp_path)]) == 0
    html = next(tmp_path.glob("*.html")).read_text("utf-8")
    assert row(html, "Video file").startswith(
        "a video file next to the page: Renamed [TESTVIDEO01].mp4, 2 MB; Deno at download:")


# --------------------------------------------------------------------------
# --video-formats: the list on the screen, no page
# --------------------------------------------------------------------------

def test_format_list_of_a_video(info):
    info["formats"] = video_formats_example()
    assert yt2html.format_list(info) == [
        'Test: "video" | part 1 / <2>? [TESTVIDEO01]',
        "image (mp4, AV1 else H.264; sizes with the original audio):",
        "  1080p, 50 fps  (1080p50)  h264  ~609 MB  (default)",
        "  1080p, 25 fps  (1080p25)  av1   ~309 MB",
        "  360p, 25 fps   (360p25)   h264   ~29 MB",
        "audio (mp4, aac; extra tracks m4a, aac, ~9 MB each):",
        "  in the video: en (original)",
        "  dubbed: pl"]
    assert yt2html.format_list(info, saved=True)[0].endswith(" · saved data of 05.11.2026")


def test_format_list_audio_lines(info):
    base = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB)]
    info["formats"] = base + [
        afmt("140", "mp4a.40.2", 9 * MB, **kind("original")),
        afmt("140-1", "mp4a.40.2", 60 * MB, "pl", -1, **kind("dubbed-auto", "pl")),
        afmt("140-2", "mp4a.40.2", 70 * MB, "de", -1, **kind("dubbed", "de")),
        afmt("251-3", "opus", 8 * MB, "fr", -1, **kind("dubbed", "fr")),     # no aac: not offered
        afmt("140-4", "mp4a.40.2", 65 * MB, "en", -1, **kind("descriptive")),
        afmt("140-5", "mp4a.40.2", None, "ja", -1, **kind("dubbed", "ja"))]
    assert yt2html.format_list(info)[3:] == [
        "audio (mp4, aac; extra tracks m4a, aac, ~60–70 MB each):",
        "  in the video: en (original)",
        "  dubbed: de, ja", "  auto-dubbed: pl", "  descriptive: en"]
    info.pop("duration", None)
    info["formats"] = base + [afmt("140", "mp4a.40.2", 9 * MB),
                              afmt("140-1", "mp4a.40.2", None, "pl", -1)]
    assert yt2html.format_list(info)[3] == "audio (mp4, aac; extra tracks m4a, aac):"   # sizes unknown
    info["formats"] = base + [afmt("140", "mp4a.40.2", 9 * MB),
                              afmt("140-1", "mp4a.40.2", 900 * MB, "pl", -1),
                              afmt("140-2", "mp4a.40.2", 1_100 * MB, "de", -1)]
    assert yt2html.format_list(info)[3] == "audio (mp4, aac; extra tracks m4a, aac, ~900 MB–1.1 GB each):"
    info["formats"] = base + [afmt("140", "mp4a.40.2", 9 * MB)]
    assert yt2html.format_list(info)[3:] == ["audio (mp4, aac):", "  in the video: en (original)"]
    # the original without aac: its own codec in the video, the extra tracks still aac
    info["formats"] = base + [afmt("251", "opus", 8 * MB), afmt("140-1", "mp4a.40.2", 9 * MB, "pl", -1)]
    assert yt2html.format_list(info)[3] == "audio (mp4, opus; extra tracks m4a, aac, ~9 MB each):"
    info["formats"] = base + [afmt("140", "mp4a.40.2", 5 * MB, "pl", -1),
                              afmt("140-1", "mp4a.40.2", 5 * MB, "de", -1)]   # no original known
    assert yt2html.format_list(info)[3:] == ["audio (mp4, aac; extra tracks m4a, aac, ~5 MB each):",
                                             "  in the video: pl",   # the first: the main audio
                                             "  tracks: de"]
    info["formats"] = base + [afmt("140", "mp4a.40.2", 5 * MB, None, -1),
                              afmt("140-1", "mp4a.40.2", 5 * MB, "de", -1, **kind("dubbed", "de"))]
    assert yt2html.format_list(info)[4] == "  in the video: unknown language"   # no role: none marked
    info["formats"] = base + [afmt("140", "mp4a.40.2", 5 * MB),
                              afmt("140-1", "mp4a.40.2", 5 * MB, None, -1)]
    assert yt2html.format_list(info)[5] == "  dubbed: unknown language"
    # a single track has nothing to mark it by: the original
    info["formats"] = base + [afmt("140", "mp4a.40.2", 5 * MB, "pl", -1)]
    assert yt2html.format_list(info)[3:] == ["audio (mp4, aac):", "  in the video: pl (original)"]
    info["formats"] = base
    assert yt2html.format_list(info)[1:] == [
        "image (mp4, AV1 else H.264; sizes with the original audio):",
        "  1080p, 25 fps  (1080p25)  av1  ~300 MB", "audio: none"]   # no audio: no default
    info["formats"] = [vfmt("244", 480, "vp9", 50 * MB)]
    assert yt2html.format_list(info)[1] == ("image: nothing a download can take "
                                            "(no av1 or h264 up to 1080p)")
    del info["formats"]
    assert yt2html.format_list(info)[1:] == ["no formats in the data"]


def test_video_formats_option_prints_and_makes_no_page(capsys, tmp_path, info):
    info["formats"] = video_formats_example()
    saved = tmp_path / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    code = yt2html.main(["--video-formats", "--from-json", str(saved), str(saved),
                         "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 0 and not (tmp_path / "out").exists()
    assert out.count("  1080p, 50 fps  (1080p50)  h264  ~609 MB  (default)") == 1  # one source
    code = yt2html.main(["--video-formats", "--from-json", str(saved), str(saved.with_name("y.json")),
                         "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 1 and "  dubbed: pl\n\n[yt2html] FAILED: " in out   # a blank line between videos
    assert "Deno" not in out   # saved data: nothing fetched


def test_video_formats_option_excludes_page_options(capsys):
    with pytest.raises(SystemExit):
        yt2html.main(["u", "--video-formats", "--theme", "dark", "--no-cards"])
    assert ("--video-formats makes no page, so it cannot be used with --no-cards, --theme"
            in capsys.readouterr().err)
    for option in (["--overwrite"], ["--index"], ["--index-only"], ["--save-json"],
                   ["--no-comments"], ["--max-comments", "5"], ["--no-avatars"],
                   ["--transcript", "timestamps"], ["--no-snapshot"]):
        with pytest.raises(SystemExit):
            yt2html.parse_args(["u", "--video-formats", *option])
        assert option[0] in capsys.readouterr().err
    assert yt2html.parse_args(["u", "--video-formats", "--cookies", "firefox", "-o", "x"])


def test_video_formats_option_fetches_only_the_video_data(capsys, monkeypatch, info):
    info["formats"] = video_formats_example()
    seen = {}

    class Fake:
        def __init__(self, opts): seen["opts"] = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download):
            if url == "TESTVIDEO02":
                raise yt2html.yt_dlp.utils.DownloadError("Video unavailable")
            return dict(info)
        def sanitize_info(self, i): return i

    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", Fake)
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: None)
    code = yt2html.main(["--video-formats", "TESTVIDEO02", "TESTVIDEO01",
                         "PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa", "--cookies", "firefox"])
    out = capsys.readouterr().out
    assert code == 1
    assert "getcomments" not in seen["opts"] and seen["opts"]["cookiesfrombrowser"][0] == "firefox"
    assert "+" not in seen["opts"]["format"]   # one format, no merging: no warning about ffmpeg
    assert "FAILED: TESTVIDEO02" in out and "  dubbed: pl" in out
    assert "FAILED: PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa: --video-formats lists single videos" in out
    assert "Note: no JavaScript runtime (Deno) found" in out
    assert yt2html.main(["--video-formats", "TESTVIDEO02"]) == 1   # only the failed one
    capsys.readouterr()
    # yt-dlp answers with a playlist: not listed either
    monkeypatch.setattr(Fake, "extract_info", lambda self, url, download: {"_type": "playlist"})
    assert yt2html.main(["--video-formats", "TESTVIDEO01"]) == 1
    assert "lists single videos, not playlists" in capsys.readouterr().out


def test_video_formats_name_qualities_as_youtube(info):
    """Quality is YouTube's name: from the format note, else from a format of
    the same size, else from the size; the resolution column has the size."""
    hls = {"protocol": "m3u8_native"}
    info["formats"] = [
        vfmt("137", 1920, "avc1.640028", 100 * MB, width=1080, format_note="1080p"),  # a Short
        vfmt("136", 1280, "avc1.4d401f", 50 * MB, width=720),                         # no note
        vfmt("270", 1920, "avc1.640028", None, width=1080, **hls),
        afmt("140", "mp4a.40.2", 9 * MB)]
    tables, rest = video_tables(info)
    assert tables["image"][1] == [["quality", "resolution", "h264", "HLS (sizes unknown)"],
                                  ["1080p, 25 fps (1080p25)", "1080×1920", "~109 MB", "h264"],
                                  ["720p, 25 fps (720p25)", "720×1280", "~59 MB", "–"]]
    assert "planned default: 1080p, 25 fps (1080p25) h264" in rest   # a Short can be taken
    assert summary(info).startswith("Video formats · up to 1080p, 25 fps · ")
    # a wide frame: named after its width; two sizes in one row
    info["formats"] = [vfmt("137", 804, "avc1.640028", 100 * MB, width=1920),
                       vfmt("399", 800, "av01.0.08M.08", 90 * MB, width=1920)]
    assert video_tables(info)[0]["image"][1][1][:2] == ["1080p, 25 fps (1080p25)",
                                                        "1920×804, 1920×800"]
    # only HLS at a quality: its size
    info["formats"] = [vfmt("625", 2160, "vp09.00.50.08", None, width=3840, fps=60, **hls),
                       vfmt("137", 1080, "avc1.640028", 100 * MB, width=1920)]
    assert video_tables(info)[0]["image"][1][1][:2] == ["2160p", "3840×2160"]


def test_youtube_quality_names():
    q = yt2html.quality_from_size
    assert [q(1920, 1080), q(1920, 804), q(1080, 1920), q(3440, 1440), q(1440, 1080),
            q(640, 360), q(256, 144), q(1080, 1350)] == [1080, 1080, 1080, 2160, 1080, 360,
                                                         144, 1080]
    formats = [{"width": 1920, "height": 1080, "format_note": "1080p60"},
               {"width": 1920, "height": 1080, "format_note": "Premium"},   # the same size
               {"width": 1920, "height": 1080},
               {"width": 2560, "height": 1072},                             # from the size
               {"height": 480}]                                             # no size: the height
    assert list(yt2html.youtube_qualities(formats).values()) == [1080, 1080, 1080, 1440, 480]
    # YouTube's name wins over the size rule (which would give 720p for 1000x640),
    # also for a format of the same size without a name
    formats = [{"width": 1000, "height": 640, "format_note": "480p"}, {"width": 1000, "height": 640}]
    assert list(yt2html.youtube_qualities(formats).values()) == [480, 480]
    assert yt2html.quality_from_size(1000, 640) == 720


def test_languages_table_joins_subtitles_to_audio(info):
    """The author's subtitles: the row of the same code, else the only row of
    the language (not an audio description); automatic ones: the row of their
    audio's language. Otherwise rows of their own, in their language's place
    when it has audio, else at the end, alphabetically."""
    info[yt2html.TRANSLATIONS_KEY] = {code: name for code, name in (
        ("en", "English"), ("pl", "Polish"), ("de", "German"), ("fr", "French"),
        ("es", "Spanish"), ("es-MX", "Spanish (Mexico)"))}
    info["formats"] = [
        vfmt("137", 1080, "avc1.640028", 100 * MB),
        afmt("140", "mp4a.40.2", 9 * MB, "en-US", format_note="English (US) original (default)"),
        afmt("140-1", "mp4a.40.2", 9 * MB, "en-GB", -1, **kind("dubbed", "en-GB")),
        afmt("140-2", "mp4a.40.2", 9 * MB, "fr-FR", -1, **kind("dubbed-auto", "fr-FR")),
        afmt("140-3", "mp4a.40.2", 9 * MB, "es", -1, **kind("dubbed", "es")),
        afmt("140-4", "mp4a.40.2", 9 * MB, "es-US", -1, **kind("dubbed-auto", "es-US")),
        afmt("140-5", "mp4a.40.2", 9 * MB, "es", -10)]   # a Spanish audio description
    info["subtitles"] = {code: [{}] for code in ("pl", "en", "fr-CA", "es-US", "es", "es-MX", "de")}
    info["automatic_captions"] = {"en-orig": [{}], "fr-orig": [{}], "es-orig": [{}],
                                  **{code: [{}] for code in info[yt2html.TRANSLATIONS_KEY]}}
    rows = video_tables(info)[0]["languages"][1]
    assert [[r[0], r[1], r[2], r[-2], r[-1]] for r in rows] == [
        ["code", "language", "audio", *SUBS],
        # two English audio tracks: "en" fits neither, so a row of its own in the group
        ["en-US", "English (US)", "original", "–", "–"],
        ["en-GB", "English", "dubbed", "–", "–"],   # the name from the list, by the language
        ["en", "English", "–", "en", "en"],
        # "es" and "es-US" by their codes; "es-MX" fits two rows: its own; the description
        # never takes subtitles meant for a track of the same language; by role within it
        ["es", "Spanish", "dubbed", "es", "es"],
        ["es-US", "Spanish", "auto-dubbed", "es-US", "–"],
        ["es", "Spanish", "descriptive", "–", "–"],
        ["es-MX", "Spanish (Mexico)", "–", "es-MX", "–"],
        # "fr-CA" and "fr" to the only French row
        ["fr-FR", "French", "auto-dubbed", "fr-CA", "fr"],
        # languages without audio at the end
        ["de", "German", "–", "de", "–"],
        ["pl", "Polish", "–", "pl", "–"]]
    assert rows[0][3] == "aac" and all(r[3] == "–" for r in rows if r[2] == "–")   # no size
    assert translation(info) == "machine translation into 5 languages"
    # automatic captions as the transcript's track: marked in their cell
    info[yt2html.CAPTION_KEY] = {"language": "en", "kind": "asr"}
    assert '<td><span class="pick">en</span></td></tr>' in render(info)
    # only the author's subtitles: still translated
    del info["automatic_captions"]
    assert translation(info) == "machine translation into 5 languages"
    info[yt2html.TRANSLATIONS_KEY] = {}
    assert translation(info) == "machine translation: none"


def test_languages_table_edge_cases(info):
    def rows():
        return [[r[0], r[2], r[-2], r[-1]] for r in video_tables(info)[0]["languages"][1][1:]]
    video = [vfmt("137", 1080, "avc1.640028", 100 * MB)]
    # automatic captions join the only audio track of an unknown language (made from
    # its speech), the author's subtitles do not (they belong to the video)
    info["formats"] = video + [afmt("140", "mp4a.40.2", 5 * MB, None, -1)]
    info["subtitles"] = {"en": [{}]}
    info["automatic_captions"] = {"en-orig": [{}]}
    assert rows() == [["–", "original", "–", "en"], ["en", "–", "en", "–"]]
    # ... not when there are more tracks, nor a known language
    info["formats"].append(afmt("140-1", "mp4a.40.2", 5 * MB, "pl", -1))
    info["subtitles"] = {}
    assert rows() == [["–", "–", "–", "–"], ["pl", "–", "–", "–"], ["en", "–", "–", "en"]]
    info["formats"] = video + [afmt("140", "mp4a.40.2", 5 * MB, "pl")]
    assert rows() == [["pl", "original", "–", "–"], ["en", "–", "–", "en"]]
    # only an audio description of a language: it takes that language's subtitles
    info["formats"] = video + [afmt("140", "mp4a.40.2", 5 * MB, "en"),
                               afmt("140-1", "mp4a.40.2", 5 * MB, "pl", -10)]
    info["subtitles"] = {"pl": [{}]}
    info["automatic_captions"] = {}
    assert rows() == [["en", "original", "–", "–"], ["pl", "descriptive", "pl", "–"]]
    # within a language: known roles, then other kinds, then no role
    info["formats"] = video + [afmt("140", "mp4a.40.2", 5 * MB, "pl", -1),
                               afmt("140-1", "mp4a.40.2", 6 * MB, "pl", -1,
                                    **kind("commentary", "pl")),
                               afmt("140-2", "mp4a.40.2", 5 * MB, "de", -1)]
    info["subtitles"] = {}
    assert [r[:2] for r in rows()] == [["de", "–"], ["pl", "commentary"], ["pl", "–"]]
    info["formats"][2]["language"] = "pl-PL"   # by role first, then by code
    assert [r[:2] for r in rows()] == [["de", "–"], ["pl-PL", "commentary"], ["pl", "–"]]
    # the mark follows the main audio's row (a dub: the original only for Premium)
    info["formats"] = video + [
        afmt("141", "mp4a.40.2", 18 * MB, format_note="Premium"),
        afmt("140-1", "mp4a.40.2", 12 * MB, "de", -1, **kind("dubbed", "de")),
        afmt("140-2", "mp4a.40.2", 9 * MB, "ar", -1, **kind("dubbed-auto", "ar"))]
    assert [r[:2] for r in rows()] == [["en", "original"], ["ar", "auto-dubbed"], ["de", "dubbed"]]
    assert picked(info)["languages"] == ["~12 MB"]

