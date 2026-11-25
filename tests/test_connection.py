"""A lost connection (the user's run of 25.11.2026): the run stops before anything of
the video is written; a refusal (HTTP 429) or another failure does not stop it."""

import json
import socket
import urllib.error

import pytest
import yt_dlp

import yt2html
from conftest import REAL_CONNECTED, caption_formats

DNS = "[Errno 11001] getaddrinfo failed"


def run(capsys, *argv):
    code = yt2html.main(list(argv))
    return code, capsys.readouterr().out


def page(folder):
    return next(folder.glob("*[[]TESTVIDEO01[]].html"))


def with_page(capsys, saved):
    """The page from the saved data (no YouTube), as a run before left it."""
    code, _ = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "load",
                  "--transcript", "timed")
    assert code == 0
    return saved.read_bytes(), page(saved.parent).read_bytes()


def failing(monkeypatch, error):
    """Translations (tlang) fail with `error`; the rest is YouTube as faked."""
    real = yt2html.read_url

    def read_url(ydl, url):
        if "tlang=" in url:
            raise error
        return real(ydl, url)

    monkeypatch.setattr(yt2html, "read_url", read_url)


def test_a_translation_without_connection_stops_and_writes_nothing(capsys, saved, youtube,
                                                                   monkeypatch):
    data, html = with_page(capsys, saved)
    failing(monkeypatch, urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "timed,dual:pl")
    assert code == 1
    assert (f"FAILED: no connection to YouTube (<urlopen error {DNS}>). The run stops here: "
            "this video and the ones after it are as they were. Run the same command again "
            "when the connection is back.") in out
    assert "Done: 0 saved, 0 skipped (page exists), 1 failed, stopped: no connection." in out
    # nothing written: the page keeps its images, the data do not note a refusal
    assert saved.read_bytes() == data and page(saved.parent).read_bytes() == html
    assert "Updated metadata" not in out and "No translation" not in out


def test_the_connection_lost_before_the_page(capsys, saved, youtube, monkeypatch):
    data, html = with_page(capsys, saved)
    monkeypatch.setattr(yt2html, "connected", lambda: False)
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "timed,dual:pl")
    assert code == 1 and "Downloading transcript translation (Polish" in out
    assert ("no connection to YouTube (the connection was lost before the page was made "
            "again)") in out
    assert saved.read_bytes() == data and page(saved.parent).read_bytes() == html
    # without a page to replace, or from the saved data alone, it is not asked
    asked = []
    monkeypatch.setattr(yt2html, "connected", lambda: asked.append(1) or False)
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "load",
                    "--transcript", "timed")
    assert code == 0 and not asked
    page(saved.parent).unlink()
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "timed,dual:pl")
    assert code == 0 and not asked and "Saved page: " in out


def test_another_failure_is_a_refusal_with_its_own_hint(capsys, saved, youtube, monkeypatch):
    failing(monkeypatch, urllib.error.HTTPError("u", 500, "Internal Server Error", {}, None))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "dual:pl")
    assert code == 0
    assert ("No translation into 'pl': the download failed (HTTP Error 500: Internal Server "
            "Error).") in out
    assert "or --page ovr. Try again later." in out and "(HTTP 429)" not in out


def test_429_keeps_its_hint(capsys, saved, youtube):
    youtube["fail_translations"] = 1
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "dual:pl")
    assert code == 0 and yt2html.TRANSLATION_HINT in out


@pytest.fixture
def two(monkeypatch):
    """Two videos to download; archive() as given by the test, the calls kept."""
    calls = []

    def use(archive):
        calls.clear()

        def wrapper(source, args, playlist, outcome):
            calls.append(source)
            return archive(source, args, outcome)
        monkeypatch.setattr(yt2html, "archive", wrapper)
        return calls
    return use


def test_the_videos_after_it_are_not_tried(capsys, tmp_path, two):
    def archive(source, args, outcome):
        raise yt_dlp.utils.DownloadError(f"ERROR: [youtube] AAAAAAAAAAA: Unable to download "
                                         f"webpage: Failed to resolve 'www.youtube.com' ({DNS})")
    calls = two(archive)
    code, out = run(capsys, "AAAAAAAAAAA", "BBBBBBBBBBB", "-o", str(tmp_path))
    assert code == 1 and calls == ["AAAAAAAAAAA"]
    assert "Done: 0 saved, 0 skipped (page exists), 1 failed, stopped: no connection." in out
    # another failure: that video fails, the next is tried
    def archive(source, args, outcome):
        raise yt_dlp.utils.DownloadError("ERROR: [youtube] AAAAAAAAAAA: Video unavailable")
    calls = two(archive)
    code, out = run(capsys, "AAAAAAAAAAA", "BBBBBBBBBBB", "-o", str(tmp_path))
    assert calls == ["AAAAAAAAAAA", "BBBBBBBBBBB"] and "stopped" not in out, (calls, out)
    # an error of the program's own with the sign in it
    def archive(source, args, outcome):
        raise yt2html.TranscriptError(f"could not download the transcript ({DNS})")
    calls = two(archive)
    code, out = run(capsys, "AAAAAAAAAAA", "BBBBBBBBBBB", "-o", str(tmp_path))
    assert calls == ["AAAAAAAAAAA"] and "stopped: no connection" in out


def test_a_video_file_without_connection(capsys, tmp_path, two, monkeypatch):
    def archive(source, args, outcome):
        outcome["target"] = tmp_path / f"Title [{source}].html"
        return "saved"
    calls = two(archive)
    monkeypatch.setattr(yt2html, "download_video", lambda *a: (_ for _ in ()).throw(
        yt_dlp.utils.DownloadError(f"ERROR: unable to download video data: {DNS}")))
    monkeypatch.setattr(yt2html, "ffmpeg_available", lambda: True, raising=False)
    code, out = run(capsys, "AAAAAAAAAAA", "BBBBBBBBBBB", "-o", str(tmp_path), "--video")
    assert code == 1 and calls == ["AAAAAAAAAAA"] and "stopped: no connection" in out
    assert "Videos: 0 downloaded, 0 skipped, 1 failed." in out


def test_what_counts_as_a_lost_connection():
    lost = [urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")),
            OSError("[Errno -3] Temporary failure in name resolution"),
            OSError("[Errno 101] Network is unreachable"),
            OSError("[WinError 10065] A socket operation was attempted to an unreachable host")]
    for error in lost:
        assert yt2html.no_connection(error), error
    try:   # the sign in an error it came from
        try:
            raise OSError("Failed to resolve 'www.youtube.com'")
        except OSError as inner:
            raise RuntimeError("download failed") from inner
    except RuntimeError as outer:
        assert yt2html.no_connection(outer)
    try:   # raised while handling it, without "from"
        try:
            raise OSError("Failed to resolve 'www.youtube.com'")
        except OSError:
            raise RuntimeError("download failed")
    except RuntimeError as outer:
        assert yt2html.no_connection(outer)
    wrapped = yt_dlp.utils.DownloadError("ERROR: failed", exc_info=(OSError, OSError(DNS), None))
    assert yt2html.no_connection(wrapped)
    for error in (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None),
                  TimeoutError("timed out"), ConnectionResetError("reset by peer"), None):
        assert not yt2html.no_connection(error)


def test_subtitles_of_their_own_without_connection():
    class Offline:
        def urlopen(self, url):
            raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

    info = {"subtitles": {"pl": caption_formats("Polish")}}
    transcript = {"language": "en", "cues": []}
    gaps = {}
    with pytest.raises(yt2html.NoConnection):
        yt2html.add_tracks(Offline(), info, transcript, ["pl.author"], gaps)
    assert gaps == {} and "tracks" not in transcript


def test_whether_youtube_resolves(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [("ok",)])
    assert REAL_CONNECTED()
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a: (_ for _ in ()).throw(socket.gaierror(11001, "failed")))
    assert not REAL_CONNECTED()
