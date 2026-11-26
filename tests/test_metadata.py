"""The yt2html:... metadata of video pages (what the index reads)."""

import datetime as dt

import pytest
import yt_dlp

import yt2html

EPOCH = int(dt.datetime(2026, 11, 6, 14, 5).timestamp())   # local time


def url(n: int) -> str:
    return f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"


@pytest.fixture
def youtube(monkeypatch, info):
    """Fake YouTube: every video is the test video under its own id."""
    def fetch_info(source, args):
        video_id = yt2html.video_id_from_url(source)
        return {**info, "id": video_id, "title": f"Original title {video_id[-1]}", "epoch": EPOCH}

    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)


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
    # the mark of the saved data the page is made from, when there are any
    meta = yt2html.page_metadata(info, dt.date(2026, 11, 6), "data:image/jpeg;base64,Q09W",
                                 data="3f9a1c0e7b2d")
    assert meta["yt2html:data"] == "3f9a1c0e7b2d" and list(meta)[-1] == "yt2html:cover"
    # the first 12 hex digits of the SHA-256 of the data file's bytes
    assert yt2html.data_mark(b"{}") == "44136fa355b3"


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
    yt2html.main([url(1), "-o", str(tmp_path), "--page", "ovr"])
    out = capsys.readouterr().out
    assert "Cover could not be downloaded; kept from the existing page." in out
    assert "Thumbnail could not be downloaded." in out
    assert yt2html.read_page_metadata(page)["yt2html:cover"] == cover
    page.write_text("<title>t</title>", "utf-8")             # nothing to keep
    yt2html.main([url(1), "-o", str(tmp_path), "--page", "ovr"])
    assert "Cover could not be downloaded." in capsys.readouterr().out
    assert "yt2html:cover" not in yt2html.read_page_metadata(page)


def test_metadata_of_a_page_not_made_by_yt2html(tmp_path):
    other = tmp_path / "other.html"
    other.write_text("<html><head><title>x</title></head></html>", encoding="utf-8")
    assert yt2html.read_page_metadata(other) == {}
    assert yt2html.read_page_metadata(tmp_path / "missing.html") == {}


def test_failure_reason():
    error = yt_dlp.utils.DownloadError("ERROR: [youtube] TESTVIDEO03: Private video. Sign in\nmore")
    assert yt2html.failure_reason(error) == "Private video. Sign in"
