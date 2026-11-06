"""Playlists: all videos of a playlist URL are archived."""

import pytest
import yt_dlp

import yt2html

PLAYLIST = "https://www.youtube.com/playlist?list=PLtest1234567890"


def url(n: int) -> str:
    return f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"


def entry(n: int, **extra) -> dict:
    """A flat playlist entry as yt-dlp's YouTube tab extractor gives it."""
    return {"_type": "url", "ie_key": "Youtube", "id": f"TESTVIDEO0{n}",
            "url": url(n), "title": f"Episode {n}", **extra}


@pytest.fixture
def youtube(monkeypatch, info):
    """Fake fetch_info and fetch_playlist; records what was fetched.

    `youtube.playlists` maps playlist URLs to their entries, `youtube.broken`
    holds video ids that fail as unavailable videos do."""

    class FakeYouTube:
        playlists = {PLAYLIST: [entry(1), entry(2), entry(3)]}
        broken = set()
        fetched = []

    def fetch_info(source, args):
        FakeYouTube.fetched.append(source)
        video_id = yt2html.video_id_from_url(source)
        if video_id in FakeYouTube.broken:
            raise yt_dlp.utils.DownloadError("Private video")
        return {**info, "id": video_id}

    def fetch_playlist(source, args):
        FakeYouTube.fetched.append(source)
        if source not in FakeYouTube.playlists:
            raise yt_dlp.utils.DownloadError("The playlist does not exist")
        return "Test playlist", FakeYouTube.playlists[source]

    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)
    monkeypatch.setattr(yt2html, "fetch_playlist", fetch_playlist)
    return FakeYouTube


def pages(folder):
    return sorted(p.name for p in folder.glob("*.html"))


def test_all_videos_are_archived_in_playlist_order(tmp_path, capsys, youtube):
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert youtube.fetched == [PLAYLIST, url(1), url(2), url(3)]
    assert 'Playlist "Test playlist": 3 videos.' in out
    assert "Done: 3 saved, 0 skipped (page exists), 0 failed." in out
    assert len(pages(tmp_path)) == 3


def test_video_twice_in_the_playlist(tmp_path, capsys, youtube):
    youtube.playlists[PLAYLIST] = [entry(1), entry(1), entry(2)]
    yt2html.main([PLAYLIST, "-o", str(tmp_path)])
    assert youtube.fetched == [PLAYLIST, url(1), url(2)]
    assert 'Playlist "Test playlist": 3 videos (1 duplicate ignored).' in capsys.readouterr().out


def test_existing_pages_are_skipped_and_rerun_fetches_only_the_list(tmp_path, capsys, youtube):
    (tmp_path / "Earlier [TESTVIDEO02].html").write_text("old")
    yt2html.main([PLAYLIST, "-o", str(tmp_path)])
    assert youtube.fetched == [PLAYLIST, url(1), url(3)]
    assert "Done: 2 saved, 1 skipped (page exists), 0 failed." in capsys.readouterr().out
    youtube.fetched.clear()
    yt2html.main([PLAYLIST, "-o", str(tmp_path)])
    assert youtube.fetched == [PLAYLIST]       # new episodes would be found here
    assert "Done: 0 saved, 3 skipped (page exists), 0 failed." in capsys.readouterr().out


def test_failed_video_does_not_stop_the_playlist_and_is_retried(tmp_path, capsys, youtube):
    youtube.broken = {"TESTVIDEO02"}
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"FAILED: {url(2)}" in out
    assert "Done: 2 saved, 0 skipped (page exists), 1 failed." in out
    youtube.broken, youtube.fetched = set(), []
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path)]) == 0
    assert youtube.fetched == [PLAYLIST, url(2)]  # nothing remembered, only the pages


def test_video_given_before_the_playlist_is_archived_once(tmp_path, youtube):
    yt2html.main([url(2), PLAYLIST, "-o", str(tmp_path)])
    assert youtube.fetched == [url(2), PLAYLIST, url(1), url(3)]


def test_video_given_after_the_playlist_is_archived_once(tmp_path, capsys, youtube):
    yt2html.main([PLAYLIST, url(2), "-o", str(tmp_path)])
    assert youtube.fetched == [PLAYLIST, url(1), url(2), url(3)]
    assert f"Ignored duplicate: {url(2)}" in capsys.readouterr().out


def test_two_playlists_with_a_common_video(tmp_path, capsys, youtube):
    other = "https://www.youtube.com/playlist?list=PLother123456789"
    youtube.playlists[other] = [entry(3), entry(4)]
    yt2html.main([PLAYLIST, other, "-o", str(tmp_path)])
    assert youtube.fetched == [PLAYLIST, url(1), url(2), url(3), other, url(4)]
    assert 'Playlist "Test playlist": 2 videos (1 duplicate ignored).' in capsys.readouterr().out


def test_playlist_that_cannot_be_read_counts_as_one_failure(tmp_path, capsys, youtube):
    missing = "https://www.youtube.com/playlist?list=PLmissing1234567"
    assert yt2html.main([missing, url(5), "-o", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"FAILED: {missing}" in out
    assert "Done: 1 saved, 0 skipped (page exists), 1 failed." in out


def test_empty_playlist(tmp_path, capsys, youtube):
    youtube.playlists[PLAYLIST] = []
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert 'Playlist "Test playlist": 0 videos.' in out
    assert "Done: 0 saved, 0 skipped (page exists), 0 failed." in out


def test_playlist_in_a_batch_file(tmp_path, youtube):
    batch = tmp_path / "list.txt"
    batch.write_text(f"{url(5)}\n{PLAYLIST}\n", encoding="utf-8")
    yt2html.main(["-a", str(batch), "-o", str(tmp_path)])
    assert youtube.fetched == [url(5), PLAYLIST, url(1), url(2), url(3)]


def test_bare_playlist_id(tmp_path, youtube):
    youtube.playlists["PLtest1234567890"] = [entry(1)]
    yt2html.main(["PLtest1234567890", "-o", str(tmp_path)])
    assert youtube.fetched == ["PLtest1234567890", url(1)]


def test_watch_url_with_list_is_one_video(tmp_path, youtube):
    single = f"{url(1)}&list=PLtest1234567890"
    yt2html.main([single, "-o", str(tmp_path)])
    assert youtube.fetched == [single]


@pytest.mark.parametrize("item, expected", [
    (entry(1), "TESTVIDEO01"),
    ({"_type": "url", "url": "https://www.youtube.com/watch?v=TESTVIDEO02"}, "TESTVIDEO02"),
    ({"_type": "url", "id": "TESTVIDEO03", "title": "[Private video]"}, "TESTVIDEO03"),
    ({"_type": "playlist", "id": "PLnested"}, None),
    ({"_type": "url", "url": "https://www.youtube.com/@channel"}, None),
])
def test_entry_video_id(item, expected):
    assert yt2html.entry_video_id(item) == expected


class FakePlaylistYDL:
    """yt_dlp.YoutubeDL answering a playlist URL; records options and calls."""
    opts, calls = {}, []
    answers = {}

    def __init__(self, opts):
        FakePlaylistYDL.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download, process=True, ie_key=None):
        FakePlaylistYDL.calls.append((url, process))
        return FakePlaylistYDL.answers[url]


def test_fetch_playlist_reads_only_the_list(monkeypatch):
    def entries():  # yt-dlp gives the entries lazily
        yield entry(1)
        yield entry(2)

    FakePlaylistYDL.calls = []
    FakePlaylistYDL.answers = {
        "short": {"_type": "url", "url": "real", "ie_key": "YoutubeTab"},
        "real": {"_type": "playlist", "id": "PLx", "title": "Python course", "entries": entries()},
    }
    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", FakePlaylistYDL)
    args = yt2html.parse_args(["x", "--cookies-from-browser", "firefox"])
    title, items = yt2html.fetch_playlist("short", args)
    assert title == "Python course" and [i["id"] for i in items] == ["TESTVIDEO01", "TESTVIDEO02"]
    assert FakePlaylistYDL.calls == [("short", False), ("real", False)]
    assert FakePlaylistYDL.opts["extract_flat"] == "in_playlist"
    assert FakePlaylistYDL.opts["cookiesfrombrowser"] == ("firefox",)


def test_fetch_playlist_rejects_a_video(monkeypatch):
    FakePlaylistYDL.answers = {"v": {"id": "TESTVIDEO01", "title": "A video"}}
    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", FakePlaylistYDL)
    with pytest.raises(ValueError, match="does not lead to a playlist"):
        yt2html.fetch_playlist("v", yt2html.parse_args(["x"]))


def test_ctrl_c_during_comments_saves_the_page_and_stops(tmp_path, capsys, youtube, info,
                                                         monkeypatch):
    """yt-dlp ends only the comments on Ctrl+C (comment_count None); the run stops."""
    plain_fetch = yt2html.fetch_info

    def fetch(source, args):
        result = plain_fetch(source, args)
        if source == url(2):
            result.update(comments=result["comments"][:2], comment_count=None)
        return result

    monkeypatch.setattr(yt2html, "fetch_info", fetch)
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path)]) == 130
    out = capsys.readouterr().out
    assert youtube.fetched == [PLAYLIST, url(1), url(2)]   # episode 3 not started
    assert len(pages(tmp_path)) == 2
    assert "use --overwrite for this video" in out
    assert "Interrupted (Ctrl+C)" in out
    assert "Done: 2 saved, 0 skipped (page exists), 0 failed." in out


def test_ctrl_c_elsewhere_stops_with_a_summary(tmp_path, capsys, youtube, monkeypatch):
    plain_fetch = yt2html.fetch_info

    def fetch(source, args):
        if source == url(2):
            raise KeyboardInterrupt
        return plain_fetch(source, args)

    monkeypatch.setattr(yt2html, "fetch_info", fetch)
    assert yt2html.main([PLAYLIST, "-o", str(tmp_path)]) == 130
    out = capsys.readouterr().out
    assert len(pages(tmp_path)) == 1
    assert "Done: 1 saved, 0 skipped (page exists), 0 failed." in out


def test_disabled_comments_are_not_an_interruption(info):
    assert not yt2html.comments_interrupted({**info, "comments": None, "comment_count": None})
    assert not yt2html.comments_interrupted(info)
    assert yt2html.comments_interrupted({**info, "comment_count": None})
