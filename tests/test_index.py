"""Index pages (--index, --index-only) and the metadata of video pages."""

import datetime as dt

import pytest
import yt_dlp

import yt2html

EPOCH = int(dt.datetime(2026, 11, 6, 14, 5).timestamp())   # local time
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
        return {**info, "id": video_id, "title": f"Original title {n}", "epoch": EPOCH}

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
    ({"comments": None, "comment_count": None}, "unavailable"),   # no reason known
    ({"comments": None, yt2html.COMMENTS_MESSAGE_KEY: "Comments are turned off."}, "turned-off"),
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


def test_the_metadata_for_the_index(info):
    info.update(epoch=EPOCH, duration=597.6, timestamp=None, release_timestamp=None,
                upload_date="20080529")
    info[yt2html.CAPTION_KEY] = {"language": "en-US"}
    info[yt2html.UNAVAILABLE_KEY] = {"status": "ERROR", "reason": "Video unavailable"}
    info[yt2html.UPDATES_KEY] = [
        {"date": "2026-11-20", "time": "10:12", "refreshed": True},
        {"date": "2026-11-21", "time": "08:00", "refreshed": True},
        {"date": "2026-11-22", "time": "09:30", "added": ["translation pl"]},
        {"date": "2026-12-03", "time": "14:05", "unavailable": True}]
    meta = yt2html.page_metadata(info, dt.date(2026, 11, 6), "data:image/jpeg;base64,Q09W")
    assert meta == {
        "yt2html:metadata": "2",
        "yt2html:video-id": "TESTVIDEO01",
        "yt2html:title": info["title"],
        "yt2html:archived": "2026-11-06T14:05",
        "yt2html:refreshed": "2026-11-21T08:00",     # the last refresh, not a later update
        "yt2html:unavailable": "2026-12-03T14:05",
        "yt2html:published": "2008-05-29",
        "yt2html:duration": "598",
        "yt2html:language": "en-US",
        "yt2html:comments-status": "complete",
        "yt2html:comments-downloaded": str(len(info["comments"])),
        "yt2html:comments-youtube": yt2html.youtube_comment_count(info)[0],
        "yt2html:cover": "data:image/jpeg;base64,Q09W",
    }
    assert list(meta)[-1] == "yt2html:cover"                  # the long one after the rest


def test_unknown_values_are_left_out_never_written_empty(info):
    for key in ("title", "epoch", "duration", "timestamp", "release_timestamp",
                "release_date", "upload_date", "automatic_captions", "comment_count",
                "language", "formats", yt2html.YT_COUNT_KEY):
        info.pop(key, None)
    info[yt2html.CAPTION_KEY] = None
    meta = yt2html.page_metadata(info, dt.date(2026, 11, 6))
    assert set(meta) == {"yt2html:metadata", "yt2html:video-id", "yt2html:archived",
                         "yt2html:comments-status", "yt2html:comments-downloaded"}
    assert meta["yt2html:archived"] == "2026-11-06"           # no moment: the day
    html = render(info)
    assert 'content=""' not in html
    info["duration"] = 0                                      # known, though zero
    assert yt2html.page_metadata(info, dt.date(2026, 11, 6))["yt2html:duration"] == "0"


def test_published_day(info):
    moment = int(dt.datetime(2026, 1, 2, 0, 30).timestamp())   # local time
    assert yt2html.published_day({"timestamp": moment, "upload_date": "20260101"}) == \
        dt.date(2026, 1, 2)
    assert yt2html.published_day({"release_timestamp": moment, "timestamp": 1}) == \
        dt.date(2026, 1, 2)
    assert yt2html.published_day({"release_date": "20260103", "upload_date": "20260101"}) == \
        dt.date(2026, 1, 3)
    assert yt2html.published_day({"release_date": "2026-1-3", "upload_date": "20260101"}) == \
        dt.date(2026, 1, 1)
    assert yt2html.published_day({"timestamp": 0}) == dt.date.fromtimestamp(0)
    assert yt2html.published_day({}) is None and yt2html.published_date({}) == "—"


def test_the_metadata_is_read_up_to_the_title_however_long_the_cover(tmp_path):
    cover = "data:image/jpeg;base64," + "A" * 200_000
    page = tmp_path / "page.html"
    page.write_text('<head><meta name="yt2html:video-id" content="X">'
                    f'<meta name="yt2html:cover" content="{cover}"><title>t</title>'
                    '<meta name="yt2html:title" content="not metadata"></head>', "utf-8")
    assert yt2html.read_page_metadata(page) == {"yt2html:video-id": "X", "yt2html:cover": cover}


def test_the_metadata_reading_stops_at_the_title(tmp_path, monkeypatch):
    # the index reads every page; pages are megabytes long
    page = tmp_path / "page.html"
    page.write_text('<meta name="yt2html:video-id" content="X"><title>t</title>'
                    + "x" * (3 * yt2html.META_READ_BYTES), "utf-8")
    pieces = []
    real_open = open

    def counting_open(*args, **kwargs):
        file = real_open(*args, **kwargs)
        read = file.read
        file.read = lambda size=-1: pieces.append(size) or read(size)
        return file
    monkeypatch.setattr(yt2html, "open", counting_open, raising=False)
    assert yt2html.read_page_metadata(page) == {"yt2html:video-id": "X"}
    assert pieces == [yt2html.META_READ_BYTES]


def test_the_cover_is_downloaded_and_kept_when_youtube_no_longer_gives_it(
        tmp_path, capsys, youtube, monkeypatch):
    cover = "data:image/jpeg;base64,Q09W"
    monkeypatch.setattr(yt2html, "fetch_image", lambda u: cover if u == yt2html.cover_url(
        {"id": "TESTVIDEO01"}) else None)
    yt2html.main([url(1), "-o", str(tmp_path)])
    page = next(tmp_path.glob("*.html"))
    assert yt2html.read_page_metadata(page)["yt2html:cover"] == cover
    assert yt2html.cover_url({"id": "AbC"}) == "https://i.ytimg.com/vi/AbC/mqdefault.jpg"
    monkeypatch.setattr(yt2html, "fetch_image", lambda u: None)
    capsys.readouterr()
    yt2html.main([url(1), "-o", str(tmp_path), "--overwrite"])
    out = capsys.readouterr().out
    assert "Cover could not be downloaded; kept from the existing page." in out
    assert "Thumbnail could not be downloaded." in out
    assert yt2html.read_page_metadata(page)["yt2html:cover"] == cover
    page.write_text("<title>t</title>", "utf-8")             # nothing to keep
    yt2html.main([url(1), "-o", str(tmp_path), "--overwrite"])
    assert "Cover could not be downloaded." in capsys.readouterr().out
    assert "yt2html:cover" not in yt2html.read_page_metadata(page)


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
    ({"yt2html:comments-status": "unavailable"}, "Comments unavailable"),
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
    assert '<td class="date">06.11.2026</td>' in html          # from 2026-11-06T14:05
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
    (["--load-json", "x.info.json", "--index"], "cannot be used with --load-json"),
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
