"""--video: the video file itself, after its page. yt-dlp, ffmpeg and YouTube
are faked: no test downloads anything."""

import argparse
import datetime as dt
import json
import re
from pathlib import Path

import pytest

import yt2html

URL = "https://www.youtube.com/watch?v=TESTVIDEO01"
MB = 1_000_000
TODAY = dt.date.today().strftime(yt2html.DATE_FORMAT)


def vfmt(fid, height, vcodec, size, fps=25, **extra):
    return {"format_id": fid, "ext": "mp4", "height": height, "vcodec": vcodec,
            "acodec": "none", "fps": fps, "filesize": size, **extra}


def afmt(fid, acodec, size, language="en", preference=10):
    return {"format_id": fid, "ext": "m4a" if acodec.startswith("mp4a") else "webm",
            "vcodec": "none", "acodec": acodec, "filesize": size, "language": language,
            "language_preference": preference}


FORMATS = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB), vfmt("137", 1080, "avc1.640028", 400 * MB),
           vfmt("398", 720, "av01.0.05M.08", 150 * MB), vfmt("136", 720, "avc1.4d401f", 200 * MB),
           afmt("140", "mp4a.40.2", 9 * MB), afmt("251", "opus", 8 * MB)]


def row(html, name):
    match = re.search(rf"<dt>{name}</dt><dd>(.*?)</dd>", html)
    assert match, f"no row {name}"
    return re.sub(r"<[^>]+>", "", match.group(1))


def marked(html, mark):
    """The texts of the cells with a mark (planned: blue, chosen: yellow)."""
    return re.findall(rf'<td class="[^"]*\b{mark}\b[^"]*">(.*?)</td>', html)


@pytest.fixture
def youtube(monkeypatch, info):
    """YouTube, ffmpeg, Deno and yt-dlp's download, faked; what happened is kept."""
    seen = {"fetches": 0, "downloads": [], "sleeps": []}

    def fetch_info(source, args):
        return {**json.loads(json.dumps(info)), "id": yt2html.video_id_from_url(source),
                "formats": json.loads(json.dumps(FORMATS)),
                yt2html.DOWNLOAD_KEY: {"deno": True}}

    def fetch_formats(source, args):
        seen["fetches"] += 1
        return {"id": yt2html.video_id_from_url(source), "duration": 600,
                "formats": json.loads(json.dumps(seen.get("fresh", FORMATS)))}

    def yt_download(fresh, opts):
        seen["downloads"].append(opts)
        if seen.get("error"):
            raise yt2html.yt_dlp.utils.DownloadError("ERROR: HTTP Error 403: Forbidden")
        # the name is a yt-dlp template: "%" must come as "%%"
        Path(opts["outtmpl"]["default"] % {"ext": "mp4"}).write_bytes(b"x" * 1234)

    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)
    monkeypatch.setattr(yt2html, "fetch_formats", fetch_formats)
    monkeypatch.setattr(yt2html, "yt_download", yt_download)
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(yt2html.time, "sleep", seen["sleeps"].append)
    monkeypatch.setattr(yt2html.random, "uniform", lambda a, b: 9.0)
    return seen


def page(folder):
    return next(folder.glob("*TESTVIDEO01*.html"))


# Parameters ------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("", (None, None, False)),
    ("720p", (720, None, False)),          # any frame rate, the highest first
    ("720P30", (720, 30, False)),          # any case of letters
    (" overwrite , 480p ", (480, None, True)),
    ("1080p60", (1080, 60, False)),
])
def test_video_parameters(value, expected):
    spec = yt2html.video_spec(value)
    assert (spec["height"], spec["fps"], spec["overwrite"]) == expected
    assert spec["text"] == value.strip().lower()


@pytest.mark.parametrize("value, message", [
    (URL, "is not a parameter (use a quality such as 720p or 720p30, or overwrite); "
          "the video's address goes before --video"),
    ("hd", "'hd' is not a parameter"),
    ("700p", "'700p' is not a parameter"),            # not a quality YouTube names
    ("2160p", "'2160p' is above 1080p; yt2html downloads up to 1080p"),
    ("720p,480p", "give one quality only"),
    ("720p0", "no such frame rate"),
    ("overwrite,overwrite", "'overwrite' is given more than once"),
    ("720p,,overwrite", "empty item"),
])
def test_video_parameter_errors(value, message):
    with pytest.raises(argparse.ArgumentTypeError, match=re.escape(message)):
        yt2html.video_spec(value)


def test_video_option_order(capsys):
    # the documented way: the address first
    args = yt2html.parse_args([URL, "--video"])
    assert args.sources == [URL] and args.video["height"] is None
    # with parameters the order is free; "=" works too
    assert yt2html.parse_args(["--video", "720p30", URL]).video["fps"] == 30
    assert yt2html.parse_args(["--video=720p", URL]).video["height"] == 720
    assert yt2html.parse_args(["--video", "--save-json", URL]).video["text"] == ""
    assert yt2html.parse_args([URL]).video is None
    # the one mistake: the address taken as the parameters
    with pytest.raises(SystemExit):
        yt2html.parse_args(["--video", URL])
    assert "the video's address goes before --video" in capsys.readouterr().err


@pytest.mark.parametrize("options, message", [
    (["--from-json"], "--video needs YouTube, so it cannot be used with --from-json"),
    (["--index-only"], "--index-only downloads nothing, so it cannot be used with --video"),
    (["--video-formats"], "--video-formats makes no page, so it cannot be used with --video"),
])
def test_video_refuses_options(capsys, options, message):
    with pytest.raises(SystemExit):
        yt2html.parse_args([URL, "--video", *options])
    assert message in capsys.readouterr().err


# The download ----------------------------------------------------------------

def test_video_after_the_page(tmp_path, youtube, capsys):
    # the fresh list (one more format than the page had) replaces the old one
    youtube["fresh"] = FORMATS + [vfmt("135", 480, "avc1.4d401e", 50 * MB)]
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "--save-json"]) == 0
    html_path = page(tmp_path)
    video = html_path.with_suffix(".mp4")
    assert video.read_bytes() == b"x" * 1234           # named like the page
    opts = youtube["downloads"][0]
    assert opts["format"] == "399+140" and opts["merge_output_format"] == "mp4"
    assert opts["quiet"] and not opts["noprogress"] and not opts["overwrites"]
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("[yt2html] ")]
    steps = [line.split(":")[0] for line in lines if re.match(r"\[yt2html\] (Saved|Video|Updated)", line)]
    assert steps == ["[yt2html] Saved metadata", "[yt2html] Saved page", "[yt2html] Video",
                     "[yt2html] Video", "[yt2html] Saved video", "[yt2html] Updated metadata",
                     "[yt2html] Updated page"]
    assert "[yt2html] Video: fetching the current list of formats..." in out
    assert "[yt2html] Video: 1080p, 25 fps av1 + en aac, mp4, ~309 MB" in out
    assert f"[yt2html] Saved video: {video} (1 MB)" not in out    # 1234 bytes: "<1 MB"
    assert f"[yt2html] Saved video: {video} (<1 MB)" in out
    # the page again: what was downloaded, in yellow
    html = html_path.read_text("utf-8")
    assert row(html, "Video file") == (f"downloaded {TODAY}: 1080p, 25 fps av1 + en aac, mp4, "
                                       "&lt;1 MB; Deno at download: yes")
    assert marked(html, "chosen") == ["~309 MB", "~9 MB"] and marked(html, "planned") == []
    assert ".formats-info .formats .chosen{color:var(--chosen)}" in html
    # the data: the result and the list it was chosen from
    saved = json.loads(html_path.with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY] == {
        "date": dt.date.today().isoformat(), "parameters": "", "file": video.name,
        "size": 1234, "quality": "1080p, 25 fps", "codec": "av1", "video_format": "399",
        "audio": "en", "audio_codec": "aac", "audio_format": "140"}
    assert [f["format_id"] for f in saved["formats"]] == [f["format_id"] for f in FORMATS] + ["135"]
    # the Command row describes the page, not the video
    assert "--video" not in row(html, "Command")


def test_video_limit_and_both_marks(tmp_path, youtube):
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "720p"]) == 0
    assert youtube["downloads"][0]["format"] == "398+140"
    html = page(tmp_path).read_text("utf-8")
    # downloaded in yellow, the planned default stays blue
    assert marked(html, "chosen") == ["~159 MB", "~9 MB"] and marked(html, "planned") == ["~309 MB"]
    assert row(html, "Video file").startswith(f"downloaded {TODAY}: 720p, 25 fps av1 + en aac")


def test_video_frame_rate_limit(info):
    info["formats"] = [vfmt("302", 720, "avc1.4d401f", 90 * MB, fps=60),
                       vfmt("136", 720, "avc1.4d401f", 60 * MB, fps=30), afmt("140", "mp4a.40.2", 9 * MB)]
    choice = yt2html.video_choice(info)
    take = lambda value: yt2html.choose_variant(choice, yt2html.video_spec(value))["format"]["format_id"]
    assert take("720p") == "302" and take("720p30") == "136" and take("") == "302"
    assert yt2html.choose_variant(choice, yt2html.video_spec("480p")) is None
    assert yt2html.limit_text(yt2html.video_spec("720p30")) == "720p30"
    assert yt2html.limit_text(yt2html.video_spec("")) == "1080p"


def test_video_file_already_there(tmp_path, youtube, capsys):
    yt2html.main([URL, "-o", str(tmp_path), "--video"])
    capsys.readouterr()
    # again: the page is skipped, the video too, and YouTube is not asked
    assert yt2html.main([URL, "-o", str(tmp_path), "--video"]) == 0
    out = capsys.readouterr().out
    assert "Skipped (page exists)" in out
    assert f"Video skipped (file exists): {page(tmp_path).with_suffix('.mp4')}. Add overwrite " \
           "to --video to download it again." in out
    assert youtube["fetches"] == 1
    # overwrite: downloaded again, yt-dlp told to overwrite
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "overwrite"]) == 0
    assert youtube["fetches"] == 2 and youtube["downloads"][-1]["overwrites"] is True


def test_video_for_an_existing_page(tmp_path, youtube, capsys):
    yt2html.main([URL, "-o", str(tmp_path)])
    before = page(tmp_path).read_text("utf-8")
    capsys.readouterr()
    # the page is there: only the video is downloaded, the page stays as it is
    assert yt2html.main([URL, "-o", str(tmp_path), "--video"]) == 0
    out = capsys.readouterr().out
    assert "Saved video:" in out and "Updated page" not in out
    assert page(tmp_path).read_text("utf-8") == before


def test_video_part_files_are_not_a_video(tmp_path, youtube):
    yt2html.main([URL, "-o", str(tmp_path)])
    # one format of an interrupted download: not taken as the video
    (tmp_path / (page(tmp_path).stem + ".f399.mp4")).write_bytes(b"x")
    assert yt2html.video_files_for({"id": "TESTVIDEO01"}, tmp_path) == []
    assert yt2html.main([URL, "-o", str(tmp_path), "--video"]) == 0
    assert youtube["downloads"]


def test_video_without_ffmpeg(tmp_path, youtube, monkeypatch, capsys):
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: None)
    monkeypatch.setattr(yt2html.sys, "platform", "win32")
    assert yt2html.main([URL, "-o", str(tmp_path), "--video"]) == 0   # the page is made
    out = capsys.readouterr().out
    assert ("Video skipped: ffmpeg not found (it joins image and sound). Install it: "
            "winget install --id Gyan.FFmpeg -e, then open a new terminal.") in out
    assert page(tmp_path) and youtube["fetches"] == 0 and not youtube["downloads"]
    monkeypatch.setattr(yt2html.sys, "platform", "darwin")
    assert yt2html.install_hint("ffmpeg") == "brew install ffmpeg"
    monkeypatch.setattr(yt2html.sys, "platform", "freebsd")      # other systems: Linux's
    assert yt2html.install_hint("deno").startswith("curl -fsSL https://deno.land")


def test_video_nothing_to_take(tmp_path, youtube, capsys):
    youtube["fresh"] = [vfmt("244", 480, "vp9", 50 * MB), afmt("140", "mp4a.40.2", 9 * MB)]
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "480p"]) == 0
    assert ("Video skipped: no AV1 or H.264 image up to 480p (see Video formats on the page)."
            in capsys.readouterr().out)
    youtube["fresh"] = [{"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"}]
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "overwrite"]) == 0
    assert ("Video skipped: no video or audio formats in the list (YouTube sometimes sends "
            "none for a while; try again later)." in capsys.readouterr().out)
    youtube["fresh"] = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB)]
    yt2html.main([URL, "-o", str(tmp_path), "--video", "overwrite"])
    assert "Video skipped: no audio track in the list." in capsys.readouterr().out
    assert not youtube["downloads"]


def test_video_download_fails(tmp_path, youtube, capsys):
    youtube["error"] = True
    assert yt2html.main([URL, "-o", str(tmp_path), "--video"]) == 1
    out = capsys.readouterr().out
    assert ("Video FAILED: HTTP Error 403: Forbidden. The page is saved; run the same command "
            "again to retry.") in out
    assert row(page(tmp_path).read_text("utf-8"), "Video file").startswith("not downloaded")


def test_video_notes_once(tmp_path, youtube, monkeypatch, capsys):
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: None if name == "deno" else name)
    monkeypatch.setattr(yt2html.yt_dlp.version, "__version__", "2020.01.01")
    playlist = "https://www.youtube.com/playlist?list=PLtest1234567890"
    entries = [{"_type": "url", "id": f"TESTVIDEO0{n}", "title": f"Episode {n}",
                "url": f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"} for n in (1, 2)]
    monkeypatch.setattr(yt2html, "fetch_playlist", lambda source, args: ("Test playlist", entries))
    assert yt2html.main([playlist, "-o", str(tmp_path), "--video"]) == 0
    out = capsys.readouterr().out
    assert out.count("Note: no JavaScript runtime (Deno) found, so YouTube may offer fewer "
                     "formats. Install it: ") == 1
    assert out.count("days old; if the download fails, update: uv tool upgrade "
                     "yt-video-archiver") == 1
    # a pause between the two videos, not before the first or after the last
    assert youtube["sleeps"] == [9.0] and out.count("Waiting 9 s before the next video...") == 1
    assert ("Done: 2 saved, 0 skipped (page exists), 0 failed. Videos: 2 downloaded, "
            "0 skipped, 0 failed.") in out
    # again: nothing asked of YouTube, so no pause
    youtube["sleeps"].clear()
    yt2html.main([playlist, "-o", str(tmp_path), "--video"])
    assert youtube["sleeps"] == []


def test_video_title_with_percent_and_unknown_language(tmp_path, youtube, monkeypatch):
    fetch = yt2html.fetch_info
    monkeypatch.setattr(yt2html, "fetch_info", lambda source, args: {**fetch(source, args),
                                                                    "title": "100% real"})
    youtube["fresh"] = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB),
                        afmt("140", "mp4a.40.2", 9 * MB, language=None, preference=None)]
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "--save-json"]) == 0
    html_path = page(tmp_path)
    assert html_path.name.startswith("100% real") and html_path.with_suffix(".mp4").exists()
    saved = json.loads(html_path.with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY]["audio"] is None        # no language in the data
    assert " av1 + aac, mp4, " in row(html_path.read_text("utf-8"), "Video file")


def test_yt_dlp_age():
    today = dt.date(2026, 11, 12)
    assert yt2html.yt_dlp_age("2026.10.26", today) == 17
    assert yt2html.yt_dlp_age("2026.09.01.1", today) == 72    # a fourth part
    assert yt2html.yt_dlp_age("unknown", today) is None
