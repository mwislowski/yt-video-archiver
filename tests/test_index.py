"""Index pages (--index, --index-only) and the metadata of video pages."""

import datetime as dt

import pytest
import yt_dlp

import yt2html

PLAYLIST_ID = "PLtest1234567890"
PLAYLIST = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}"
CHANNEL = "https://www.youtube.com/@channel"


def url(n: int) -> str:
    return f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"


def entry(n: int, title: str | None = None) -> dict:
    return {"_type": "url", "ie_key": "Youtube", "id": f"TESTVIDEO0{n}", "url": url(n),
            "title": title or f"Episode {n}"}


@pytest.fixture
def youtube(monkeypatch, info):
    """Fake YouTube: a playlist of four videos; per video id, `failing` gives
    an error message and `interrupt` stops the run like Ctrl+C."""

    class FakeYouTube:
        entries = [entry(1), entry(2), entry(3, "[Private video]"), entry(4)]
        failing = {"TESTVIDEO03": "ERROR: [youtube] TESTVIDEO03: Private video. Sign in"}
        interrupt = set()
        fetched = []
        lists_read = []

    def fetch_info(source, args):
        FakeYouTube.fetched.append(source)
        video_id = yt2html.video_id_from_url(source)
        if video_id in FakeYouTube.interrupt:
            raise KeyboardInterrupt
        if video_id in FakeYouTube.failing:
            raise yt_dlp.utils.DownloadError(FakeYouTube.failing[video_id])
        n = int(video_id[-1])
        return {**info, "id": video_id, "title": f"Original title {n}"}

    def fetch_playlist(source, args):
        FakeYouTube.lists_read.append(source)
        return "Test playlist", FakeYouTube.entries

    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)
    monkeypatch.setattr(yt2html, "fetch_playlist", fetch_playlist)
    return FakeYouTube


def index_file(folder):
    found = list(folder.glob(f"*[[]{PLAYLIST_ID}[]].html"))
    assert len(found) == 1
    return found[0]


def rows(path) -> list[dict]:
    """The entries the index keeps (also what --index-only reads)."""
    return [e for s in yt2html.read_index(path) for e in s["entries"]]


# Metadata of video pages ----------------------------------------------------

def render(info):
    return yt2html.render_page(info, None, {}, dt.date(2026, 11, 6))


@pytest.mark.parametrize("change, status", [
    ({}, "complete"),
    ({"comment_count": None}, "interrupted"),                 # Ctrl+C in yt-dlp
    ({yt2html.LIMIT_KEY: 2, yt2html.YT_COUNT_KEY: "50"}, "limited"),
    ({yt2html.LIMIT_KEY: 200}, "complete"),                   # limit not reached
    ({"comments": None, "comment_count": None}, "turned-off"),
])
def test_comment_status(info, change, status):
    assert yt2html.comment_status({**info, **change}) == status


def test_comment_status_without_comments(info):
    info.pop("comments")
    assert yt2html.comment_status(info) == "not-downloaded"


def test_metadata_is_written_and_read_back(info, tmp_path):
    page = tmp_path / "page.html"
    page.write_text(render(info), encoding="utf-8")
    meta = yt2html.read_page_metadata(page)
    assert meta["yt2html:video-id"] == "TESTVIDEO01"
    assert meta["yt2html:title"] == info["title"]           # quotes, <, > and | survive
    assert meta["yt2html:archived"] == "2026-11-06"
    assert meta["yt2html:comments-status"] == "complete"
    assert meta["yt2html:comments-downloaded"] == str(len(info["comments"]))


def test_metadata_of_a_page_not_made_by_yt2html(tmp_path):
    other = tmp_path / "other.html"
    other.write_text("<html><head><title>x</title></head></html>", encoding="utf-8")
    assert yt2html.read_page_metadata(other) == {}
    assert yt2html.read_page_metadata(tmp_path / "missing.html") == {}


@pytest.mark.parametrize("meta, text", [
    ({"yt2html:comments-status": "complete", "yt2html:comments-downloaded": "523",
      "yt2html:comments-youtube": "530"}, "523 comments · complete (YouTube: 530)"),
    ({"yt2html:comments-status": "complete", "yt2html:comments-downloaded": "1",
      "yt2html:comments-youtube": "1"}, "1 comment · complete"),
    ({"yt2html:comments-status": "limited", "yt2html:comments-downloaded": "200",
      "yt2html:comments-youtube": "1.2K"}, "200 of 1.2K comments · limited"),
    ({"yt2html:comments-status": "interrupted", "yt2html:comments-downloaded": "312",
      "yt2html:comments-youtube": ""}, "312 comments · interrupted"),
    ({"yt2html:comments-status": "not-downloaded"}, "Comments not downloaded"),
    ({"yt2html:comments-status": "turned-off"}, "Comments turned off"),
    ({}, ""),
])
def test_comments_summary(meta, text):
    assert yt2html.comments_summary(meta) == text


def test_failure_reason():
    error = yt_dlp.utils.DownloadError("ERROR: [youtube] TESTVIDEO03: Private video. Sign in\nmore")
    assert yt2html.failure_reason(error) == "Private video. Sign in"


# Playlist index -------------------------------------------------------------

def test_no_index_without_the_option(tmp_path, youtube):
    yt2html.main([PLAYLIST, "-o", str(tmp_path)])
    assert not list(tmp_path.glob(f"*{PLAYLIST_ID}*"))


def test_playlist_index(tmp_path, capsys, youtube):
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    path = index_file(tmp_path)
    assert path.name == f"Test playlist [{PLAYLIST_ID}].html"
    html = path.read_text(encoding="utf-8")
    assert "<script" not in html and 'src="http' not in html
    assert "3 of 4 videos archived" in html
    assert f'<a href="{yt2html.YOUTUBE}/playlist?list={PLAYLIST_ID}" target="_blank" ' in html
    found = rows(path)
    assert [r["status"] for r in found] == ["archived", "archived", "unavailable", "archived"]
    assert found[0]["title"] == "Original title 1"               # from the page
    assert found[2]["title"] == "[Private video]"                # from the playlist
    assert found[2]["reason"] == "Private video. Sign in"
    assert '<td class="date">' in html
    assert "Saved index:" in capsys.readouterr().out


def test_links_follow_renamed_pages_and_keep_original_titles(tmp_path, youtube):
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    page = next(tmp_path.glob("*[[]TESTVIDEO02[]].html"))
    page.rename(tmp_path / "02 my name #x [TESTVIDEO02].html")
    index_file(tmp_path).rename(tmp_path / f"My index [{PLAYLIST_ID}].html")
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    path = index_file(tmp_path)
    assert path.name == f"My index [{PLAYLIST_ID}].html"         # renamed index kept
    html = path.read_text(encoding="utf-8")
    assert '<a href="02%20my%20name%20%23x%20%5BTESTVIDEO02%5D.html">Original title 2</a>' in html


def test_ctrl_c_writes_the_whole_playlist(tmp_path, capsys, youtube):
    youtube.interrupt = {"TESTVIDEO02"}
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"]) == 130
    found = rows(index_file(tmp_path))
    assert [r["status"] for r in found] == ["archived", "pending", "unavailable", "pending"]
    html = index_file(tmp_path).read_text(encoding="utf-8")
    assert "Not archived yet" in html and "1 of 4 videos archived" in html


def test_failed_video_and_its_reason(tmp_path, youtube):
    youtube.failing = {"TESTVIDEO04": "ERROR: [youtube] TESTVIDEO04: Sign in to confirm your age"}
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    html = index_file(tmp_path).read_text(encoding="utf-8")
    assert '<td class="date reason" title="Sign in to confirm your age">Failed</td>' in html


def test_reason_is_kept_until_the_video_is_tried_again(tmp_path, youtube):
    youtube.failing = {"TESTVIDEO04": "ERROR: [youtube] TESTVIDEO04: Sign in to confirm your age"}
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    youtube.interrupt = {"TESTVIDEO04"}               # Ctrl+C before it is tried again
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    assert rows(index_file(tmp_path))[3]["status"] == "pending"   # it was tried: no old reason
    youtube.interrupt, youtube.failing = set(), {}
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    assert rows(index_file(tmp_path))[3]["status"] == "archived"


def test_reason_survives_a_run_that_does_not_reach_the_video(tmp_path, youtube):
    youtube.failing = {"TESTVIDEO04": "ERROR: [youtube] TESTVIDEO04: Sign in to confirm your age",
                       **youtube.failing}
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    youtube.interrupt = {"TESTVIDEO03"}               # Ctrl+C before video 4
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    last = rows(index_file(tmp_path))[3]
    assert (last["status"], last["reason"]) == ("failed", "Sign in to confirm your age")


def test_video_twice_in_the_playlist_has_two_rows(tmp_path, youtube):
    youtube.entries = [entry(1), entry(2), entry(1)]
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    assert [r["id"] for r in rows(index_file(tmp_path))] == ["TESTVIDEO01", "TESTVIDEO02",
                                                             "TESTVIDEO01"]


def test_theme(tmp_path, youtube):
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index", "--theme", "Light"])   # any case of letters
    html = index_file(tmp_path).read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light">' in html


# List file index ------------------------------------------------------------

def write_list(tmp_path, *lines):
    path = tmp_path / "my list.txt"
    path.write_text("# comment\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_list_file_index_with_a_playlist_section(tmp_path, youtube):
    out = tmp_path / "pages"
    batch = write_list(tmp_path, url(5), "https://youtu.be/TESTVIDEO05", PLAYLIST, CHANNEL, url(6))
    yt2html.main(["-a", batch, "-o", str(out), "--index"])
    path = out / "my list.html"
    html = path.read_text(encoding="utf-8")
    assert "<h1 class=\"title\">my list.txt</h1>" in html
    assert 'Test playlist</a> <span class="source">playlist · 4 videos</span></h2>' in html
    sections = yt2html.read_index(path)
    assert [s["playlist_id"] for s in sections] == ["", PLAYLIST_ID, ""]
    assert [e["id"] for e in sections[0]["entries"]] == ["TESTVIDEO05"]   # listed twice
    assert sections[2]["entries"][0] == {
        "id": "", "url": CHANNEL, "title": "", "unavailable": False,
        "status": "not-a-video", "reason": "a channel URL"}
    # plain rows are numbered across the file, playlist rows by position
    assert [p.split("<")[0] for p in html.split('<td class="pos">')[1:]] == \
        ["1", "1", "2", "3", "4", "2", "3"]
    assert "6 of 6 videos archived" not in html and "5 of 6 videos archived" in html


def test_list_file_with_an_unreadable_playlist(tmp_path, youtube, monkeypatch):
    def broken(source, args):
        raise yt_dlp.utils.DownloadError("The playlist does not exist")

    monkeypatch.setattr(yt2html, "fetch_playlist", broken)
    out = tmp_path / "pages"
    yt2html.main(["-a", write_list(tmp_path, PLAYLIST, url(5)), "-o", str(out), "--index"])
    html = (out / "my list.html").read_text(encoding="utf-8")
    assert "not read yet" in html and "1 of 1 video archived" in html


# --index-only -----------------------------------------------------------------

def test_index_only_downloads_nothing(tmp_path, youtube):
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    next(tmp_path.glob("*[[]TESTVIDEO01[]].html")).rename(tmp_path / "renamed [TESTVIDEO01].html")
    next(tmp_path.glob("*[[]TESTVIDEO04[]].html")).unlink()
    youtube.fetched.clear()
    youtube.lists_read.clear()
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index-only"]) == 0
    assert youtube.fetched == [] and youtube.lists_read == []
    found = rows(index_file(tmp_path))
    assert [r["status"] for r in found] == ["archived", "archived", "unavailable", "pending"]
    assert found[2]["reason"] == "Private video. Sign in"        # kept from the earlier index
    assert found[3]["title"] == "Original title 4"               # remembered by the index
    assert 'href="renamed%20%5BTESTVIDEO01%5D.html"' in index_file(tmp_path).read_text("utf-8")


def test_index_only_without_an_earlier_index_reads_only_the_list(tmp_path, youtube):
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index-only"]) == 0
    assert youtube.lists_read == [PLAYLIST] and youtube.fetched == []
    assert "0 of 4 videos archived" in index_file(tmp_path).read_text(encoding="utf-8")


def test_index_only_for_a_list_file(tmp_path, youtube):
    out = tmp_path / "pages"
    batch = write_list(tmp_path, url(5), PLAYLIST)
    yt2html.main(["-a", batch, "-o", str(out), "--index"])
    youtube.fetched.clear()
    youtube.lists_read.clear()
    yt2html.main(["-a", batch, "-o", str(out), "--index-only"])
    assert youtube.fetched == [] and youtube.lists_read == []
    assert [s["playlist_id"] for s in yt2html.read_index(out / "my list.html")] == ["",
                                                                                   PLAYLIST_ID]


@pytest.mark.parametrize("argv, message", [
    (["--from-json", "x.info.json", "--index"], "cannot be used with --from-json"),
    ([url(1), "--index"], "need a playlist (URL, id or the name of its index page) or a list file"),
    ([url(1), "--index-only"], "need a playlist (URL, id or the name of its index page) or a list file"),
])
def test_index_option_errors(capsys, argv, message):
    with pytest.raises(SystemExit):
        yt2html.parse_args(argv)
    assert message in capsys.readouterr().err


def test_single_urls_get_no_index(tmp_path, capsys, youtube):
    yt2html.main([url(5), PLAYLIST, "-o", str(tmp_path), "--index"])
    assert "only playlists and list files get an index" in capsys.readouterr().out
    assert len(rows(index_file(tmp_path))) == 4


def test_index_only_with_the_name_of_the_index(tmp_path, youtube, monkeypatch):
    yt2html.main([PLAYLIST, "-o", str(tmp_path), "--index"])
    youtube.lists_read.clear()
    youtube.fetched.clear()
    monkeypatch.chdir(tmp_path)
    name = index_file(tmp_path).name
    assert yt2html.main([name, "--index-only"]) == 0
    assert yt2html.main([name.removesuffix(".html"), "--index-only"]) == 0
    assert youtube.lists_read == [] and youtube.fetched == []
    assert [p.name for p in tmp_path.glob("*.html") if "PLtest" in p.name] == [name]


def test_index_option_error_names_the_accepted_forms(capsys):
    with pytest.raises(SystemExit):
        yt2html.parse_args(["Something else", "--index-only"])
    assert "URL, id or the name of its index page" in capsys.readouterr().err
