"""--take with saved data and with --video: every question to YouTube counts."""

import json

import pytest

import yt2html


def exhausted(*argv: str) -> "argparse.Namespace":
    """Arguments whose --take 1 another video has already taken."""
    args = yt2html.parse_args(list(argv) + ["--take", "1"])
    args.taken = {"OTHERVIDEO1"}
    return args


def test_json_refresh_counts(saved, youtube):
    before = saved.read_text("utf-8")
    args = exhausted("TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    with pytest.raises(yt2html.TakeReached):
        yt2html.archive("TESTVIDEO01", args)
    assert saved.read_text("utf-8") == before and youtube["extracted"] == 0


def test_json_write_counts_only_with_something_missing(saved, youtube, capsys):
    folder = str(saved.parent)
    args = exhausted("TESTVIDEO01", "-o", folder, "--json", "write", "--transcript", "dual:pl")
    with pytest.raises(yt2html.TakeReached):    # the refused translation is asked again
        yt2html.archive("TESTVIDEO01", args)
    assert youtube["extracted"] == 0 and not list(saved.parent.glob("*.html"))
    # nothing missing: the page is made from the data, YouTube not asked, nothing counted
    args = exhausted("TESTVIDEO01", "-o", folder, "--json", "write", "--transcript", "plain")
    assert yt2html.archive("TESTVIDEO01", args)
    assert args.taken == {"OTHERVIDEO1"}


def test_video_counts_when_it_asks_youtube(tmp_path, monkeypatch):
    page = tmp_path / "Title [TESTVIDEO01].html"
    page.write_text("page", encoding="utf-8")
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: "/usr/bin/" + name)
    args = exhausted("TESTVIDEO01", "-o", str(tmp_path), "--video")
    with pytest.raises(yt2html.TakeReached):
        yt2html.download_video("TESTVIDEO01", args, page, set())
    # the file is there: nothing to ask, nothing counted
    (tmp_path / "Title [TESTVIDEO01].mp4").write_bytes(b"mp4")
    result, formats, new = yt2html.download_video("TESTVIDEO01", args, page, set())
    assert (formats, new, args.taken) == (None, False, {"OTHERVIDEO1"})


def test_main_leaves_a_video_file_for_the_next_run(tmp_path, capsys, monkeypatch):
    for n in (1, 2):
        (tmp_path / f"Title {n} [TESTVIDEO0{n}].html").write_text("page", encoding="utf-8")

    def download_video(source, args, page, notes, info=None, previous=None):
        yt2html.taking(args, yt2html.video_id_from_url(source))
        notes.add("requested")       # the next video would wait a moment first
        return {"video": {"file": "x.mp4"}}, None, False

    monkeypatch.setattr(yt2html, "download_video", download_video)
    urls = [f"https://www.youtube.com/watch?v=TESTVIDEO0{n}" for n in (1, 2)]
    assert yt2html.main([*urls, "-o", str(tmp_path), "--video", "--take", "1"]) == 0
    out = capsys.readouterr().out
    assert "Waiting" not in out            # the next video takes nothing from YouTube
    assert ("Done: 0 saved, 2 skipped (page exists), 0 failed, 1 left for the next run. "
            "Videos: 0 downloaded, 1 skipped, 0 failed.") in out
    assert "--take 1: 1 of 2 videos that need YouTube were taken; run the same" in out


def test_the_note_for_json_refresh():
    args = yt2html.parse_args(["u", "--json", "refresh", "--take", "1"])
    args.taken = {"A"}
    assert yt2html.take_note(args, 4) == (
        "--take 1: 1 of 5 videos that need YouTube were taken; --json refresh asks YouTube "
        "for every video, so the same command would take the same ones again: give --take 5 "
        "or --take all.")


def test_video_formats_does_not_count_a_playlist(capsys, monkeypatch):
    monkeypatch.setattr(yt2html, "fetch_formats", lambda source, args:
                        {"id": "x", yt2html.DOWNLOAD_KEY: {"deno": True}})
    monkeypatch.setattr(yt2html, "format_list", lambda info, saved: ["listed"])
    args = yt2html.parse_args(["https://www.youtube.com/playlist?list=PLtest1234567890",
                               "TESTVIDEO01", "--video-formats", "--take", "1"])
    assert yt2html.list_formats(args) == 1
    out = capsys.readouterr().out
    assert "lists single videos, not playlists" in out and "listed" in out
    assert "--take" not in out
