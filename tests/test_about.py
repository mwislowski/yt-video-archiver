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
        "Info cards", "Transcript", "Comments", "Images", "Software", "Disclaimer"]


def about(html: str) -> str:
    return html[html.index('<details class="copy-info">'):html.index("</details>")]


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
    html = render(info, theme=theme, no_avatars=True, show_snapshot=False, video=True)
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
# Video (experimental, --video): the formats YouTube offers
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


def video_row(info, **options):
    return row(render(info, video=True, **options), r"Video \(experimental\)")


def test_video_row_only_with_the_option(info):
    info["formats"] = video_formats_example()
    assert "Video (experimental)" not in render(info)
    html = render(info, video=True)
    names = re.findall(r"<dt>(.*?)</dt>", about(html))
    assert names == ROWS + ["Video (experimental)"]
    assert command(html).endswith(" --video")
    assert command(render(info, video=True, no_avatars=True)).endswith("--video --no-avatars")


def test_video_row_describes_image_audio_subtitles_and_the_default(info):
    info["formats"] = video_formats_example()
    info["subtitles"] = {"en": [{"ext": "vtt"}], "live_chat": [{"ext": "json"}], "de": []}
    info["automatic_captions"] = {"en-orig": [{}], "en": [{}], "pl": [{}], "fr": [{}]}
    html = render(info, video=True)
    lines = re.search(r"<dt>Video \(experimental\)</dt><dd>(.*?)</dd>", html).group(1).split("<br>")
    assert lines == [
        "image: 2160p vp9 HDR10 ~2.5 GB · 1080p h264 50 fps ~609 MB, av1 ~309 MB,"
        " vp9 Premium ~909 MB · 360p h264 ~29 MB, vp9 ~24 MB",
        "audio: en (original) aac ~9 MB, opus ~8 MB · pl (dubbed) aac ~9 MB;"
        " sizes of the image include this audio: en",
        "subtitles: by the author: en · auto-generated: en · auto-translated: 2 languages",
        "planned default: 1080p h264 + en aac, mp4, ~609 MB",
        "Deno at download: unknown (data saved by an older version)",
    ]


def test_video_row_without_an_original_language_or_runtime(info):
    info["formats"] = [vfmt("137", 1080, "avc1.640028", None, tbr=1000),   # size from bitrate
                       afmt("140", "mp4a.40.2", 5 * MB, language=None, preference=None)]
    info["duration"] = 600   # 1000 kbit/s * 600 s = 75 MB
    info[yt2html.DOWNLOAD_KEY] = {"deno": False}
    lines = video_row(info)
    assert "image: 1080p h264 ~80 MB" in lines
    assert "audio: ? aac ~5 MB" in lines
    assert "(original)" not in lines and "(dubbed)" not in lines
    assert "subtitles: none by the author" in lines
    assert "planned default: 1080p h264 + ? aac, mp4, ~80 MB" in lines
    assert lines.endswith("Deno at download: no, so YouTube may have offered more formats "
                          "than listed")
    info[yt2html.DOWNLOAD_KEY] = {"deno": True}
    assert video_row(info).endswith("Deno at download: yes")
    info[yt2html.DOWNLOAD_KEY] = {"yt2html": "0.6.0"}   # saved before the field existed
    assert video_row(info).endswith("Deno at download: unknown (data saved by an older version)")
    del info[yt2html.DOWNLOAD_KEY]
    assert video_row(info).endswith("Deno at download: unknown (data saved by an older version)")
    info["automatic_captions"] = {"en-orig": [{}], "en": [{}]}   # en = en-orig, no translation
    assert "subtitles: none by the author · auto-generated: en<br>" in render(info, video=True)


def test_video_row_edge_cases(info):
    info.pop("formats", None)
    assert video_row(info) == "no formats in the data"
    # only combined formats (old videos): they count; no audio-only, no default
    info["formats"] = [vfmt("18", 360, "avc1.42001E", 30 * MB, acodec="mp4a.40.2")]
    lines = video_row(info)
    assert lines.startswith("image: 360p h264 ~30 MB")
    assert "audio: none" in lines
    assert "planned default: nothing (no h264 image with audio)" in lines
    # unknown size and an unknown codec
    info["formats"] = [vfmt("1", 720, "xyz1.2", None), afmt("2", "opus", None)]
    info.pop("duration", None)
    lines = video_row(info)
    assert "720p xyz1 size unknown" in lines
    assert "en (original) opus size unknown" in lines
    assert "planned default: nothing" in lines


def test_sizes_and_codec_names():
    assert yt2html.human_size(None) == "size unknown"
    assert yt2html.human_size(999_999_999) == "~1000 MB"
    assert yt2html.human_size(1_000_000_000) == "~1.0 GB"
    assert yt2html.codec_name("hvc1.2.4", yt2html.VIDEO_CODECS) == "h265"
    assert yt2html.codec_name("none", yt2html.VIDEO_CODECS) is None
    assert yt2html.codec_name(None, yt2html.AUDIO_CODECS) is None
    assert yt2html.format_size({}, {"filesize_approx": 7}) == 7
    assert yt2html.format_size({"duration": 8}, {"filesize": 5, "tbr": 1}) == 5
    assert yt2html.format_size({"duration": 8}, {"tbr": 1}) == 1000


def test_video_option_with_saved_data(capsys, tmp_path, info):
    info["formats"] = video_formats_example()
    code, out, html = rebuild(capsys, tmp_path, info, "--video")
    assert code == 0
    assert "planned default: 1080p h264" in html
    assert command(html).endswith("--video")


def test_video_row_marks_premium_formats(info):
    """Signed in with YouTube Premium, yt-dlp gets formats marked "Premium":
    shown after the ordinary format of the same codec, never in the default."""
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
    lines = video_row(info)
    assert ("image: 1080p h264 ~409 MB, h264 Premium ~809 MB, vp9 ~309 MB, vp9 Premium ~909 MB"
            " · 720p h264 Premium ~109 MB") in lines
    assert "audio: en (original) aac ~9 MB, aac Premium ~18 MB, opus Premium ~17 MB;" in lines
    assert "planned default: 1080p h264 + en aac, mp4, ~409 MB" in lines
    # with Premium audio only, the main audio is still an ordinary one
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 400 * MB),
                       afmt("141", "mp4a.40.2", 18 * MB, format_note="Premium"),
                       afmt("251", "opus", 8 * MB)]
    lines = video_row(info)
    assert "image: 1080p h264 ~408 MB" in lines
    assert "planned default: 1080p h264 + en opus, mp4, ~408 MB" in lines


def test_video_row_names_hls_formats_without_sizes(info):
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
    lines = video_row(info)
    assert lines.startswith("image: 2160p HLS: vp9 60 fps · 1080p h264 ~216 MB; "
                            "HLS: h264, vp9, vp9 Premium (HLS: sizes unknown)audio:")
    assert "audio: en (original) aac ~69 MB;" in lines
    assert "planned default: 1080p h264 + en aac, mp4, ~216 MB" in lines
    # no HLS: no note
    info["formats"] = info["formats"][:1] + info["formats"][5:6]
    assert video_row(info).startswith("image: 1080p h264 ~216 MBaudio:")
    # only HLS audio: still listed
    info["formats"] = [vfmt("137", 1080, "avc1.640028", 147 * MB),
                       afmt("234-24", "mp4a.40.2", 9 * MB, **hls)]
    assert "audio: en (original) aac ~9 MB;" in video_row(info)
    assert yt2html.is_hls({"protocol": "m3u8"}) and not yt2html.is_hls({})
