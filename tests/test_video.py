"""--video: the video file itself, after its page. yt-dlp, ffmpeg and YouTube
are faked: no test downloads anything."""

import argparse
import datetime as dt
import json
import re
import urllib.parse
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
        # the name is a yt-dlp template ("%" must come as "%%"); the extension
        # is the merged mp4, or the single format's own (m4a for audio)
        ext = "mp4" if "+" in opts["format"] else next(
            f["ext"] for f in fresh["formats"] if f["format_id"] == opts["format"])
        Path(opts["outtmpl"]["default"] % {"ext": ext}).write_bytes(b"x" * 1234)

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
    (URL, "is not a parameter (use a quality such as 720p or 720p30, audio:CODE, sub:CODE or overwrite); "
          "the video's address goes before --video"),
    ("audio:pl!", "'audio:pl!': write audio:CODE or audio:CODE.ROLE"),
    ("audio:pl.auto_dubbed", "write audio:CODE or audio:CODE.ROLE, e.g. audio:pl"),
    ("audio:", "'audio:' is not a parameter"),
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
    assert ".formats-info .formats .chosen,.formats-info .translation .chosen{color:var(--chosen)}" in html
    # the data: the result and the list it was chosen from
    saved = json.loads(html_path.with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY] == {
        "date": dt.date.today().isoformat(), "parameters": "", "video": {
            "file": video.name, "size": 1234, "date": dt.date.today().isoformat(),
            "quality": "1080p, 25 fps", "codec": "av1", "video_format": "399",
            "audio": "en", "audio_codec": "aac", "audio_format": "140"}}
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
    # again: nothing asked of YouTube, so no pause; the files there count as skipped
    youtube["sleeps"].clear()
    yt2html.main([playlist, "-o", str(tmp_path), "--video"])
    assert youtube["sleeps"] == []
    assert "Videos: 0 downloaded, 2 skipped, 0 failed." in capsys.readouterr().out


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
    assert saved[yt2html.VIDEO_KEY]["video"]["audio"] is None   # no language in the data
    assert " av1 + aac, mp4, " in row(html_path.read_text("utf-8"), "Video file")


def test_yt_dlp_age():
    today = dt.date(2026, 11, 12)
    assert yt2html.yt_dlp_age("2026.10.26", today) == 17
    assert yt2html.yt_dlp_age("2026.09.01.1", today) == 72    # a fourth part
    assert yt2html.yt_dlp_age("unknown", today) is None


# Extra audio tracks (audio:) ------------------------------------------------------

def dub(fid, language, kind, acodec="mp4a.40.2", size=9 * MB):
    """An audio format with YouTube's kind of track in its address, as in real data."""
    xtags = urllib.parse.quote(f"acont={kind}:lang={language}")
    return {**afmt(fid, acodec, size, language=language, preference=-1),
            "url": f"https://rr.googlevideo.com/videoplayback?itag=140&xtags={xtags}&mime=audio"}


DUBS = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB),
        dub("140", "en", "original"), dub("251", "en", "original", "opus", 8 * MB),
        dub("140-1", "pl", "dubbed"), dub("140-2", "pl", "dubbed-auto"),
        dub("140-3", "de-DE", "dubbed-auto"), dub("140-4", "es-US", "dubbed"),
        dub("140-5", "es-ES", "dubbed"), dub("140-6", "ru", "descriptive"),
        dub("251-7", "fr", "dubbed", "opus"), dub("140-8", "ja", "commentary")]


def test_audio_parameters():
    spec = yt2html.video_spec("720p,Audio:PL,audio:de-de.Auto-Dubbed")
    assert spec["audio"] == [("pl", None, "Audio:PL"), ("de-DE", "auto-dubbed",
                                                        "audio:de-de.Auto-Dubbed")]
    assert spec["height"] == 720


@pytest.mark.parametrize("text, expected", [
    ("audio:pl", ["pl", "dubbed"]),                 # by the author before YouTube's
    ("audio:pl.auto-dubbed", ["pl", "auto-dubbed"]),
    ("audio:de", ["de-DE", "auto-dubbed"]),         # the language finds the region
    ("audio:de-de", ["de-DE", "auto-dubbed"]),      # any case
    ("audio:es", ["es-ES", "dubbed"]),              # two regions: the first by code
    ("audio:es-US", ["es-US", "dubbed"]),
    ("audio:ru.descriptive", ["ru", "descriptive"]),
    ("audio:ja.commentary", ["ja", "commentary"]),  # a kind this video has
    ("audio:de-AT", '"audio:de-AT" not available'),   # another region: no
    ("audio:ru", '"audio:ru" not available'),         # an audio description only if asked
    ("audio:fr", '"audio:fr" not available'),         # no aac format to add
    ("audio:fil", '"audio:fil" not available'),
    ("audio:it.commentary", '"audio:it.commentary" not available'),
    ("audio:jp", '"audio:jp" not recognised'),        # not a language code
    ("audio:pl.autodubbed", '"audio:pl.autodubbed" not recognised'),
    ("audio:en", '"audio:en" is already in the video'),
    ("audio:en.original", '"audio:en.original" is already in the video'),
])
def test_audio_resolution(info, text, expected):
    info["formats"] = json.loads(json.dumps(DUBS))
    choice = yt2html.video_choice(info)
    found, messages = yt2html.resolve_audio(choice, yt2html.video_spec(text)["audio"])
    assert (found, messages) == (([tuple(expected)], []) if isinstance(expected, list)
                                 else ([], [expected]))


def test_audio_asked_twice_is_taken_once(info):
    info["formats"] = json.loads(json.dumps(DUBS))
    choice = yt2html.video_choice(info)
    found, _ = yt2html.resolve_audio(choice, yt2html.video_spec("audio:pl,audio:PL.dubbed")["audio"])
    assert found == [("pl", "dubbed")]


def test_audio_with_the_video(tmp_path, youtube, capsys):
    youtube["fresh"] = DUBS
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl,audio:jp,audio:en",
                         "--save-json"]) == 0
    html_path = page(tmp_path)
    track = html_path.with_name(html_path.stem + ".pl.dubbed.m4a")
    assert track.exists() and html_path.with_suffix(".mp4").exists()
    assert [o["format"] for o in youtube["downloads"]] == ["399+140", "140-1"]
    out = capsys.readouterr().out
    assert ('[yt2html] Audio: "audio:jp" not recognised; "audio:en" is already in the video.\n'
            "[yt2html]   Tracks to add: dubbed: es-ES, es-US, pl; auto-dubbed: de-DE, pl; "
            "descriptive: ru; commentary: ja\n") in out
    assert "[yt2html] Audio: pl (dubbed), m4a, ~9 MB" in out
    assert f"[yt2html] Saved audio: {track} (<1 MB)" in out
    html = html_path.read_text("utf-8")
    assert row(html, "Video file") == (f"downloaded {TODAY}: 1080p, 25 fps av1 + en aac, mp4, "
                                       "&lt;1 MB; extra audio m4a: pl (dubbed), &lt;1 MB; "
                                       "Deno at download: yes")
    assert marked(html, "chosen") == ["~309 MB", "~9 MB", "~9 MB"]   # image, en, pl
    saved = json.loads(html_path.with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY]["extra_audio"] == [
        {"audio": "pl", "role": "dubbed", "file": track.name, "size": 1234,
         "date": dt.date.today().isoformat(), "audio_format": "140-1"}]


def test_audio_files_on_their_own(tmp_path, youtube, capsys):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path)])           # the page only
    (tmp_path / (page(tmp_path).stem + ".mp4")).write_bytes(b"v")   # a video already there
    capsys.readouterr()
    # the video is skipped, the track is added (YouTube asked for the list)
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:de"]) == 0
    out = capsys.readouterr().out
    assert "Video skipped (file exists)" in out and "Saved audio:" in out
    assert [o["format"] for o in youtube["downloads"]] == ["140-3"]
    assert "Updated page" not in out                   # the page existed: left as it is
    # again: both there, nothing downloaded; overwrite takes both again
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:de"]) == 0
    out = capsys.readouterr().out
    assert "Audio skipped (file exists): " in out and "Add overwrite to --video" in out
    assert len(youtube["downloads"]) == 1
    args = yt2html.parse_args([URL, "-o", str(tmp_path), "--video", "audio:de"])
    result, _, new = yt2html.download_video(URL, args, page(tmp_path), set())
    assert not new and result["extra_audio"] == [            # nothing new, both described
        {"audio": "de-DE", "role": "auto-dubbed", "file": page(tmp_path).stem
         + ".de-DE.auto-dubbed.m4a", "size": 1234}]
    assert result["video"] == {"file": page(tmp_path).stem + ".mp4", "size": 1}
    (tmp_path / (page(tmp_path).stem + ".de-DE.auto-dubbed.m4a")).unlink()
    assert yt2html.download_video(URL, args, page(tmp_path), set())[2]   # the track: new
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:de,overwrite"])
    assert [o["format"] for o in youtube["downloads"][-2:]] == ["399+140", "140-3"]
    assert all(o["overwrites"] for o in youtube["downloads"][-2:])


def test_audio_only_with_a_new_page(tmp_path, youtube, monkeypatch):
    # the page is made now, the video was there before: the row names both
    youtube["fresh"] = DUBS
    monkeypatch.setattr(yt2html, "existing_page", lambda folder, ident: None)
    name = yt2html.output_path({**yt2html.fetch_info(URL, None)}, tmp_path)
    Path(name).with_suffix(".mp4").write_bytes(b"v")
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl.auto-dubbed"]) == 0
    html = Path(name).read_text("utf-8")
    assert row(html, "Video file").startswith(
        f"downloaded {TODAY}: extra audio m4a: pl (auto-dubbed), &lt;1 MB; "
        f"next to the page: video {Path(name).stem}.mp4, &lt;1 MB; ")
    assert marked(html, "chosen") == ["~9 MB"]          # only the track downloaded now


def test_audio_needs_no_image_within_the_limit(tmp_path, youtube):
    # the video is there; the list has no AV1 or H.264 within 480p: the track still comes
    youtube["fresh"] = [vfmt("244", 480, "vp9", 50 * MB)] + DUBS[1:]
    yt2html.main([URL, "-o", str(tmp_path)])
    (tmp_path / (page(tmp_path).stem + ".mp4")).write_bytes(b"v")
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "480p,audio:pl"]) == 0
    assert [o["format"] for o in youtube["downloads"]] == ["140-1"]


# The player with another audio track ----------------------------------------------

def test_player_audio_switches(info):
    # none without extra tracks
    assert 'class="buttons audio"' not in yt2html.render_player(["x"], language="en")
    html = yt2html.render_player(["x"], language="en-US", original_label="English (US), original",
                                 audio_tracks=[("pl", "T [id].pl.auto-dubbed.m4a", "Polish, auto-dubbed",
                                                "01 T [id].pl.auto-dubbed.m4a", ".pl.auto-dubbed.m4a"),
                                               ("de-DE", "a&b.m4a", "German (DE), auto-dubbed", "",
                                                ".de-DE.auto-dubbed.m4a")])
    row_html = re.search(r'<div class="buttons audio">(.*?)</div>', html).group(1)
    # codes on the buttons; the language and role only as the spoken name (aria-label)
    assert row_html == (
        '<button type="button" class="original" data-audio="" aria-pressed="true" '
        'aria-label="English (US), original">en-US</button>'
        '<button type="button" data-audio="T [id].pl.auto-dubbed.m4a" '
        'data-found="01 T [id].pl.auto-dubbed.m4a" data-suffix=".pl.auto-dubbed.m4a" '
        'aria-pressed="false" aria-label="Polish, auto-dubbed">pl</button>'
        '<button type="button" data-audio="a&amp;b.m4a" data-found="" '
        'data-suffix=".de-DE.auto-dubbed.m4a" aria-pressed="false" '
        'aria-label="German (DE), auto-dubbed">de-DE</button>')
    assert html.index('class="now-playing"') < html.index('class="buttons audio"')   # the last row
    # an original of an unknown language
    assert 'aria-label="Original audio">orig</button>' in yt2html.render_player(
        ["x"], audio_tracks=[("pl", "p.m4a", "Polish, dubbed", "", ".pl.dubbed.m4a")])
    assert 'aria-label="a&lt;b">a&lt;b</button>' in yt2html.render_player(
        ["x"], audio_tracks=[("a<b", "p.m4a", "a<b", "", ".a<b.m4a")])
    # the page: the tracks --video downloaded with it, not files found next to it
    info["formats"] = json.loads(json.dumps(DUBS))
    assert 'class="buttons audio"' not in render_page(info)
    info[yt2html.VIDEO_KEY] = {"date": "2026-11-12", "parameters": "audio:pl", "extra_audio": [
        {"audio": "pl", "role": "dubbed", "file": "T.pl.dubbed.m4a", "size": 1, "audio_format": "140-1"}]}
    page_html = render_page(info)
    assert ('<button type="button" class="original" data-audio="" aria-pressed="true" '
            'aria-label="en, original">en</button>'
            '<button type="button" data-audio="T.pl.dubbed.m4a" data-found="" '
            'data-suffix=".pl.dubbed.m4a" aria-pressed="false" '
            'aria-label="pl, dubbed">pl</button>' in page_html)   # names: the table's (here codes)
    # with names in the data (yt-dlp's format note), the table's language name
    for f in info["formats"]:
        if f["format_id"] == "140-1":
            f["format_note"] = "Polish, medium"
        if f["format_id"] == "140":
            f["format_note"] = "English original (default), medium"
    page_html = render_page(info)
    assert 'aria-label="Polish, dubbed">pl</button>' in page_html
    assert 'aria-label="English, original">en</button>' in page_html
    # its own colour in both themes, the original a little wider
    assert ".player .buttons.audio button[aria-pressed=true]{background:var(--audio);" in yt2html.CSS
    assert ".player .buttons.audio button.original{width:72px}" in yt2html.CSS
    assert "--audio:#5b47c4" in yt2html.LIGHT_VARIABLES
    # the script: the video muted, the track follows it, a bad file switched off
    js = yt2html.PLAYER_JS
    assert "video.muted = true;" in js and "new Audio(encodeURIComponent(names[i]));" in js
    # the names tried: the file found, the page's own name with the ending, the file saved
    assert ("[button.dataset.found, page + button.dataset.suffix, button.dataset.audio]" in js)
    assert "if (i + 1 < names.length) { load(i + 1); return; }" in js
    assert "button.disabled = true;" in js and "setAudio(panel.querySelector('[data-audio=\"\"]'));" in js
    # averaged over a second, corrected only between 80 and 20 ms (each speed
    # change can click), set to the video's time only when a second off
    assert "gaps = gaps.concat(dub.currentTime - video.currentTime).slice(-4);" in js
    assert "if (Math.abs(off) > 1) { dub.currentTime = video.currentTime; gaps = []; return; }" in js
    assert "if (Math.abs(off) > 0.08) nudging = true;" in js
    assert "else if (Math.abs(off) < 0.02) nudging = false;" in js
    assert "if (dub.playbackRate !== rate) dub.playbackRate = rate;" in js


def render_page(info):
    return yt2html.render_page(info, None, {}, dt.date(2026, 11, 12))


# Subtitles (sub:) -------------------------------------------------------------------

TT = "https://www.youtube.com/api/timedtext?v=TESTVIDEO01&expire=1&lang="
HLS = "https://manifest.googlevideo.com/api/manifest/hls_timedtext_playlist/id/1"
SUBTITLES = {
    "subtitles": {"en": [{"ext": "json3", "url": TT + "en&fmt=json3"}],
                  "pt-BR": [{"ext": "json3", "url": TT + "pt-BR&fmt=json3"}],
                  "my": [{"ext": "vtt", "url": HLS, "protocol": "m3u8_native"}],   # HLS only
                  "live_chat": [{"ext": "json", "url": "https://example.com/chat"}]},
    "automatic_captions": {"en-orig": [{"ext": "json3", "url": TT + "en&kind=asr&fmt=json3"}],
                           "de-orig": [{"ext": "json3", "url": TT + "de&kind=asr&fmt=json3"}],
                           "pl": [{"ext": "json3", "url": TT + "en&kind=asr&tlang=pl"}]},  # yt-dlp's
}
PAGE_DATA = {yt2html.CAPTION_KEY: {"language": "en", "kind": "asr"},
             yt2html.TRANSLATIONS_KEY: {"en": "English", "pl": "Polish", "de": "German",
                                        "fr": "French"}}


def sub_data():
    return {**json.loads(json.dumps(SUBTITLES)), **PAGE_DATA,
            "formats": json.loads(json.dumps(FORMATS))}


def test_sub_parameters():
    spec = yt2html.video_spec("SUB:PL,sub:en.Author,sub:pt-br.translated")
    assert spec["subs"] == [("pl", None, "SUB:PL"), ("en", "author", "sub:en.Author"),
                            ("pt-BR", "translated", "sub:pt-br.translated")]
    for bad in ("sub:pl.autogenerated", "sub:pl!"):
        with pytest.raises(argparse.ArgumentTypeError,
                           match=re.escape("write sub:CODE or sub:CODE.KIND (KIND: author, "
                                           "auto-generated, translated)")):
            yt2html.video_spec(bad)


@pytest.mark.parametrize("text, expected", [
    ("sub:en", ("en", "author", TT + "en&fmt=srt")),          # the author's first, as SRT
    ("sub:en.auto-generated", ("en", "auto-generated", TT + "en&kind=asr&fmt=srt")),
    ("sub:pt", ("pt-BR", "author", TT + "pt-BR&fmt=srt")),     # the language finds the region
    ("sub:de", ("de", "auto-generated", TT + "de&kind=asr&fmt=srt")),   # no author's: automatic
    # nothing in Polish: the transcript's track (here the automatic English), translated
    ("sub:pl", ("pl", "translated", TT + "en&kind=asr&fmt=srt&tlang=pl")),
    ("sub:fr.translated", ("fr", "translated", TT + "en&kind=asr&fmt=srt&tlang=fr")),
    ("sub:de.author", '"sub:de.author" not available'),
    ("sub:pl.auto-generated", '"sub:pl.auto-generated" not available'),   # yt-dlp's "pl" is a translation
    ("sub:en.translated", '"sub:en.translated" not available'),   # the transcript's own language
    ("sub:pt-PT", '"sub:pt-PT" not available'),   # another region, and not in YouTube's list
    ("sub:my", '"sub:my" not available'),          # only an HLS playlist
    ("sub:jp", '"sub:jp" not recognised'),
])
def test_sub_resolution(text, expected):
    found, messages, source = yt2html.resolve_subs(sub_data(), yt2html.video_spec(text)["subs"])
    assert source == ("en", TT + "en&kind=asr&fmt=srt")
    assert (found, messages) == (([expected], []) if isinstance(expected, tuple) else ([], [expected]))


def test_sub_details():
    data = sub_data()
    # asked twice: once
    found, _, _ = yt2html.resolve_subs(data, yt2html.video_spec("sub:pl,sub:pl.translated")["subs"])
    assert [f[:2] for f in found] == [("pl", "translated")]
    # without YouTube's list (the watch page not read): any recognised language may be tried
    data[yt2html.TRANSLATIONS_KEY] = None
    found, _, _ = yt2html.resolve_subs(data, yt2html.video_spec("sub:fi")["subs"])
    assert [f[:2] for f in found] == [("fi", "translated")]
    # no subtitles at all: nothing to translate
    found, messages, source = yt2html.resolve_subs({"formats": []}, yt2html.video_spec("sub:pl")["subs"])
    assert (found, messages, source) == ([], ['"sub:pl" not available'], None)
    assert yt2html.subs_offer({"formats": []}, None) == "none"
    assert yt2html.subs_offer(sub_data(), ("en", "u")) == (
        "by the author: en, pt-BR; auto-generated: de, en; other languages as a machine translation")


@pytest.fixture
def subtitles(youtube, monkeypatch):
    """Fresh data with subtitles; YouTube's SRT files faked."""
    youtube["fresh"] = FORMATS
    youtube["subs"] = []
    fetch = yt2html.fetch_formats

    def fetch_formats(source, args):
        return {**fetch(source, args), **json.loads(json.dumps(SUBTITLES))}

    def fetch_subtitles(url, args):
        youtube["subs"].append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        language = query.get("tlang", query["lang"])[0]     # a translation: its target
        answer = youtube.get("sub_answers", {}).get(language, [b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"])
        reply = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    page_fetch = yt2html.fetch_info
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: {**page_fetch(s, a), **sub_data()})
    monkeypatch.setattr(yt2html, "fetch_formats", fetch_formats)
    monkeypatch.setattr(yt2html, "fetch_subtitles", fetch_subtitles)
    return youtube


class TooMany(Exception):
    status = 429


def test_subtitles_with_the_video(tmp_path, subtitles, capsys):
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:en,sub:pl,sub:jp,sub:fi",
                         "--save-json"]) == 0
    html_path = page(tmp_path)
    en = html_path.with_name(html_path.stem + ".en.author.srt")
    pl = html_path.with_name(html_path.stem + ".pl.translated.srt")
    assert en.read_bytes().startswith(b"1\n00:00:01,000") and pl.exists()
    assert subtitles["subs"] == [TT + "en&fmt=srt", TT + "en&kind=asr&fmt=srt&tlang=pl"]
    out = capsys.readouterr().out
    # "fi": not in YouTube's list of translation languages (from the page's data)
    assert ('[yt2html] Subtitles: "sub:jp" not recognised; "sub:fi" not available.\n'
            '[yt2html]   Subtitles to take: by the '
            "author: en, pt-BR; auto-generated: de, en; other languages as a machine translation\n"
            in out)
    assert "[yt2html] Subtitles: en (by the author), srt" in out
    assert "[yt2html] Subtitles: pl (machine translation from en), srt" in out
    # only the translation waits (the author's English does not)
    assert out.count("(YouTube refuses translations right after the video is opened)") == 1
    assert f"[yt2html] Saved subtitles: {pl} (<1 MB)" in out
    html = html_path.read_text("utf-8")
    assert row(html, "Video file").startswith(f"downloaded {TODAY}: 1080p, 25 fps av1 + en aac, mp4, ")
    assert "; subtitles srt: en (author), pl (translated from en); Deno at download: yes" in row(html, "Video file")
    assert '<span class="chosen">en</span>' in html            # the author's English, yellow
    assert ('machine-translated subtitles: possible in 3 other languages for each row with '
            'subtitles; downloaded: <span class="chosen">pl</span> (from en)</div>') in html
    saved = json.loads(html_path.with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY]["subtitles"] == [
        {"code": "en", "kind": "author", "file": en.name, "size": 38, "date": dt.date.today().isoformat()},
        {"code": "pl", "kind": "translated", "file": pl.name, "size": 38,
         "date": dt.date.today().isoformat(), "source": "en"}]


def test_subtitles_retry_empty_and_failure(tmp_path, subtitles, capsys):
    subtitles["sub_answers"] = {"en": [b"   "],                       # empty: not saved
                                "pl": [TooMany("HTTP Error 429"), b"1\n00:00:01,000 --> x\nCzesc\n"],
                                "de": [Exception("HTTP Error 404: Not Found")]}
    code = yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:en,sub:pl,sub:de"])
    out = capsys.readouterr().out
    assert code == 1                                              # one file failed
    assert "YouTube returned no subtitles for en; nothing saved." in out
    assert "(HTTP 429 from YouTube, retry 1 of 2)" in out and "Saved subtitles:" in out
    assert "Subtitles FAILED: de: HTTP Error 404: Not Found" in out
    assert page(tmp_path).with_suffix(".mp4").exists()            # the video is kept
    assert "; subtitles srt: pl (translated from en); " in row(page(tmp_path).read_text("utf-8"), "Video file")


def test_subtitles_on_their_own(tmp_path, subtitles, monkeypatch, capsys):
    yt2html.main([URL, "-o", str(tmp_path)])
    (tmp_path / (page(tmp_path).stem + ".mp4")).write_bytes(b"v")
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: None)   # no ffmpeg: not needed
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:de"]) == 0
    out = capsys.readouterr().out
    assert "Video skipped (file exists)" in out and "Saved subtitles:" in out
    assert "ffmpeg" not in out and subtitles["downloads"] == []
    # only subtitles new: still something downloaded
    args = yt2html.parse_args([URL, "-o", str(tmp_path), "--video", "sub:pt"])
    result, _, new = yt2html.download_video(URL, args, page(tmp_path), set())
    assert new and [s["code"] for s in result["subtitles"]] == ["pt-BR"]
    # again: the file is there; overwrite takes it again (and the video: ffmpeg)
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:de"]) == 0
    assert "Subtitles skipped (file exists): " in capsys.readouterr().out
    yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:de,overwrite"])
    assert "Video skipped: ffmpeg not found" in capsys.readouterr().out
    monkeypatch.setattr(yt2html.shutil, "which", lambda name: name)
    before = len(subtitles["subs"])
    yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:de,overwrite"])
    assert len(subtitles["subs"]) == before + 1 and subtitles["downloads"]


def test_subtitles_only_with_a_new_page(tmp_path, subtitles, monkeypatch):
    # the page is made now, the video was there before: the row has the date of the subtitles
    monkeypatch.setattr(yt2html, "existing_page", lambda folder, ident: None)
    name = Path(yt2html.output_path(yt2html.fetch_info(URL, None), tmp_path))
    name.with_suffix(".mp4").write_bytes(b"v")
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:de"]) == 0
    assert row(name.read_text("utf-8"), "Video file").startswith(
        f"downloaded {TODAY}: subtitles srt: de (auto-generated); "
        f"next to the page: video {name.stem}.mp4, &lt;1 MB; ")


def test_video_formats_lists_subtitles(info):
    info.update(sub_data())
    lines = yt2html.format_list(info)
    assert lines[-3:] == ["subtitles (srt):", "  by the author: en, pt-BR", "  auto-generated: de, en"]
    info["subtitles"], info["automatic_captions"] = {}, {}
    assert yt2html.format_list(info)[-1] == "subtitles: none"


def test_player_spoken_names_and_speeds():
    html = yt2html.render_player(["x"])
    for label in ("To the start", "10 seconds back", "Previous sentence", "Play",
                  "10 seconds forward", "To the end"):
        assert f'aria-label="{label}">' in html
    assert re.findall(r'aria-label="Speed ([0-9.]+)">', html) == list(yt2html.PLAYER_SPEEDS)
    assert yt2html.PLAYER_SPEEDS == ("0.25", "0.5", "0.75", "1", "1.25", "1.5", "1.75", "2")
    js = yt2html.PLAYER_JS
    # the play button's spoken name follows its symbol
    assert "toggle.textContent = '❚❚'; toggle.setAttribute('aria-label', 'Pause');" in js
    assert "toggle.textContent = '▶'; toggle.setAttribute('aria-label', 'Play');" in js
    # the extra track never slower than 0.25× (Firefox mutes slower sound)
    assert "var rate = Math.max(0.25, video.playbackRate * (nudging" in js


def test_language_names_and_the_guessed_original(info):
    # an audio track without a language, the author's subtitles with names (as TED)
    info["formats"] = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB),
                       afmt("140", "mp4a.40.2", 9 * MB, language=None, preference=None)]
    info["subtitles"] = {"en": [{"ext": "json3", "url": TT + "en", "name": "English"}],
                         "zh-CN": [{"ext": "json3", "url": TT + "zh-CN", "name": "Chinese (China)"}],
                         "pt-BR": [{"ext": "json3", "url": TT + "pt-BR", "name": "Portuguese (Brazil)"}]}
    info["automatic_captions"] = {}
    info[yt2html.CAPTION_KEY] = {"language": "en", "kind": "manual"}
    info[yt2html.TRANSLATIONS_KEY] = {"en": "English", "pt": "Portuguese"}
    html = render_page(info)
    table = re.search(r"<caption>languages</caption>(.*?)</table>", html).group(1)
    names = re.findall(r"<tr><td>([^<]*)</td><td>([^<]*)</td>", table)
    # the original: the transcript's language; names from the author's subtitles
    # before YouTube's list ("Portuguese (Brazil)", not "Portuguese")
    assert names == [("en", "English"), ("pt-BR", "Portuguese (Brazil)"), ("zh-CN", "Chinese (China)")]
    assert "<td>original</td>" in table and "original audio: en" in html
    assert "<caption>image (sizes include the audio: en aac)</caption>" in html
    # the subtitles a download can take: by code, not in the data's order
    assert yt2html.subs_offer(info, None) == "by the author: en, pt-BR, zh-CN"
    # a note of the audio format comes before the subtitles' name
    info["formats"][1].update(language="en-US", format_note="English (US) original (default), medium")
    assert ("<td>en-US</td><td>English (US)</td>" in render_page(info))
    # nothing to go by: unknown, as before
    info["formats"][1].update(language=None, format_note=None)
    info[yt2html.CAPTION_KEY] = None
    assert "<td>–</td><td>unknown</td>" in render_page(info)


def test_video_guesses_the_language_with_the_page(tmp_path, youtube, monkeypatch, capsys):
    # the fresh list has no language; the page's data (made now) gives the guess
    youtube["fresh"] = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB),
                        afmt("140", "mp4a.40.2", 9 * MB, language=None, preference=None)]
    fetch = yt2html.fetch_info
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: {
        **fetch(s, a), "formats": json.loads(json.dumps(youtube["fresh"])),
        yt2html.CAPTION_KEY: {"language": "en", "kind": "manual"},
        "subtitles": {"en": [{"ext": "json3", "url": TT + "en"}]}})
    assert yt2html.main([URL, "-o", str(tmp_path), "--video"]) == 0
    assert "[yt2html] Video: 1080p, 25 fps av1 + en aac, mp4, ~309 MB" in capsys.readouterr().out
    assert " av1 + en aac, mp4, " in row(page(tmp_path).read_text("utf-8"), "Video file")


def test_subtitles_hint_after_429(tmp_path, subtitles, capsys):
    subtitles["sub_answers"] = {"pl": [TooMany("HTTP Error 429: Too Many Requests")]}
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:pl"]) == 1
    assert ("Subtitles FAILED: pl: HTTP Error 429: Too Many Requests YouTube keeps refusing the "
            "translation; try again later, or with --cookies-from-browser firefox after opening "
            "the video with auto-translated subtitles in that browser.") in capsys.readouterr().out


# Files already next to the page: renamed, kept and described ------------------------

def rename_all(folder, prefix):
    """The user's numbering: every file of the video gets the same prefix."""
    for path in sorted(folder.glob("*TESTVIDEO01*")):
        path.rename(path.with_name(prefix + path.name))


def test_overwrite_keeps_what_the_files_were(tmp_path, youtube, capsys):
    # day 1: page, video and a track, with the data; day 2: the page again
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--save-json"])
    first = page(tmp_path).read_text("utf-8")
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite", "--video", "audio:pl",
                         "--save-json"]) == 0
    out = capsys.readouterr().out
    assert len(youtube["downloads"]) == 2                     # nothing downloaded again
    assert "Audio skipped (file exists)" in out and "Updated page" in out
    html = page(tmp_path).read_text("utf-8")
    # the page made again says what it said: the day, the variant, the switch, yellow
    assert row(html, "Video file") == row(first, "Video file")
    assert marked(html, "chosen") == marked(first, "chosen") == ["~309 MB", "~9 MB", "~9 MB"]
    assert 'data-audio="' + page(tmp_path).stem + '.pl.dubbed.m4a"' in html
    saved = json.loads(page(tmp_path).with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY]["video"]["video_format"] == "399"
    assert saved[yt2html.VIDEO_KEY]["extra_audio"][0]["audio_format"] == "140-1"


def test_renamed_files_are_found_and_described(tmp_path, youtube, capsys):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--save-json"])
    rename_all(tmp_path, "01 ")
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite", "--video", "audio:pl",
                         "--save-json"]) == 0
    assert len(youtube["downloads"]) == 2                     # recognised under the new names
    html_path = page(tmp_path)
    assert html_path.name.startswith("01 ")                   # the page keeps its name
    html = html_path.read_text("utf-8")
    track = html_path.stem + ".pl.dubbed.m4a"
    assert f'data-audio="{track}" data-found="{track}"' in html
    assert row(html, "Video file").startswith(f"downloaded {TODAY}: 1080p, 25 fps av1 + en aac, mp4, "
                                              "&lt;1 MB; extra audio m4a: pl (dubbed), &lt;1 MB; ")
    assert sorted(p.name[:3] for p in tmp_path.iterdir()) == ["01 "] * 4   # html, json, mp4, m4a


def test_a_rebuild_finds_renamed_tracks(tmp_path, youtube):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--save-json"])
    old = page(tmp_path).stem
    rename_all(tmp_path, "02 ")
    assert yt2html.main(["--from-json", "TESTVIDEO01", "-o", str(tmp_path)]) == 0
    html = page(tmp_path).read_text("utf-8")
    # the saved name stays as the last try; the file found by id and ending comes first
    assert f'data-audio="{old}.pl.dubbed.m4a" data-found="02 {old}.pl.dubbed.m4a"' in html
    # gone: nothing found, the page's own name and the saved one are still tried
    (tmp_path / f"02 {old}.pl.dubbed.m4a").unlink()
    yt2html.main(["--from-json", "TESTVIDEO01", "-o", str(tmp_path)])
    assert f'data-audio="{old}.pl.dubbed.m4a" data-found="" ' in page(tmp_path).read_text("utf-8")


def test_files_found_without_saved_data(tmp_path, youtube):
    # downloaded without --save-json: made again, the files are only found
    youtube["fresh"] = DUBS + [dub("251-9", "pl", "dubbed", "opus")]
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl"])
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite", "--video", "audio:pl"]) == 0
    html = page(tmp_path).read_text("utf-8")
    assert row(html, "Video file").startswith(
        f"next to the page: video {page(tmp_path).stem}.mp4, &lt;1 MB; "
        "extra audio m4a: pl (dubbed), &lt;1 MB; Deno at download")
    # yellow: the track's aac (code and role are certain), not the image (its variant is not)
    assert marked(html, "chosen") == ["~9 MB"]
    assert ('<td>pl</td><td>pl</td><td>dubbed</td><td class="size chosen">~9 MB</td>'
            '<td class="size">~9 MB</td>') in html             # its opus not
    assert 'data-audio="' + page(tmp_path).stem + '.pl.dubbed.m4a"' in html


def test_renamed_subtitles_are_found_and_described(tmp_path, subtitles, capsys):
    yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:en", "--save-json"])
    rename_all(tmp_path, "04 ")
    before = len(subtitles["subs"])
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite", "--video", "sub:en",
                         "--save-json"]) == 0
    assert len(subtitles["subs"]) == before                  # not downloaded again
    saved = json.loads(page(tmp_path).with_suffix(".info.json").read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY]["subtitles"] == [
        {"code": "en", "kind": "author", "file": page(tmp_path).stem + ".en.author.srt", "size": 38,
         "date": dt.date.today().isoformat()}]
    # named unlike the page: found by the id and the ending
    srt = next(tmp_path.glob("*.en.author.srt"))
    srt.rename(tmp_path / "subs [TESTVIDEO01].en.author.srt")
    capsys.readouterr()
    yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:en"])
    assert len(subtitles["subs"]) == before
    assert (f"Subtitles skipped (file exists): {tmp_path / 'subs [TESTVIDEO01].en.author.srt'}"
            in capsys.readouterr().out)


def test_the_video_only_found_keeps_the_list_of_formats(tmp_path, youtube):
    yt2html.main([URL, "-o", str(tmp_path), "--video"])
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite", "--video"]) == 0
    assert youtube["fetches"] == 1                            # no list needed for a file there
    html = page(tmp_path).read_text("utf-8")
    assert row(html, "Video file").endswith("; Deno at download: yes")
    assert marked(html, "planned") == ["~309 MB", "~9 MB"]   # the page's own list of formats


def test_subtitles_found_without_saved_data(tmp_path, subtitles):
    yt2html.main([URL, "-o", str(tmp_path), "--video", "sub:en,sub:pl"])
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite", "--video", "sub:en,sub:pl"]) == 0
    html = page(tmp_path).read_text("utf-8")
    assert "; subtitles srt: en (author), pl (translated); " in row(html, "Video file")
    assert '<span class="chosen">en</span>' in html
    # a translation only found: its source is not known
    assert 'downloaded: <span class="chosen">pl</span></div>' in html


def test_described_only_the_same_file():
    old = [{"audio": "pl", "role": "dubbed", "file": "a.m4a", "size": 5, "date": "2026-11-12",
            "audio_format": "140-1"}]
    entry = {"audio": "pl", "role": "dubbed", "file": "01 a.m4a", "size": 5}
    assert yt2html.described(entry, old, "audio", "role") == {**old[0], "file": "01 a.m4a"}
    assert yt2html.described({**entry, "size": 6}, old, "audio", "role") == {**entry, "size": 6}
    assert yt2html.described({**entry, "role": None}, old, "audio", "role") == {**entry, "role": None}
    assert yt2html.described(entry, [{**old[0], "date": None}], "audio", "role") == entry


def test_previous_video(tmp_path):
    assert yt2html.previous_video(None) == {}
    path = tmp_path / "x.info.json"
    path.write_text("not json", encoding="utf-8")
    assert yt2html.previous_video(path) == {}
    path.write_text("[1]", encoding="utf-8")
    assert yt2html.previous_video(path) == {}
    path.write_text(json.dumps({yt2html.VIDEO_KEY: {"date": "2026-11-12"}}), encoding="utf-8")
    assert yt2html.previous_video(path) == {"date": "2026-11-12"}


def test_overwrite_downloads_into_the_renamed_files(tmp_path, youtube):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl"])
    stem = page(tmp_path).stem
    for ext in (".mp4", ".pl.dubbed.m4a"):   # only the video and the track renamed
        (tmp_path / (stem + ext)).rename(tmp_path / ("03 " + stem + ext))
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl,overwrite"])
    names = [o["outtmpl"]["default"] for o in youtube["downloads"][2:]]
    assert names == [str(tmp_path / f"03 {stem}.%(ext)s"), str(tmp_path / f"03 {stem}.pl.dubbed.%(ext)s")]


def test_audio_files_for(tmp_path):
    info = {"id": "TESTVIDEO01", yt2html.VIDEO_KEY: {"extra_audio": [
        {"audio": "pt-BR", "role": "dubbed", "file": "x", "size": 1},
        {"audio": "pl", "role": None, "file": "y", "size": 1},
        {"audio": "de", "role": "auto-dubbed", "file": "z", "size": 1}]}}
    (tmp_path / "1 T [TESTVIDEO01].PT-BR.DUBBED.M4A").write_bytes(b"a")   # any case
    (tmp_path / "1 T [TESTVIDEO01].pl.m4a").write_bytes(b"a")
    (tmp_path / "1 T [TESTVIDEO01].pl.dubbed.m4a").write_bytes(b"a")    # another version
    (tmp_path / "T [OTHERVIDEO].de.auto-dubbed.m4a").write_bytes(b"a")  # another video
    assert yt2html.audio_files_for(info, tmp_path) == {
        ("pt-BR", "dubbed"): "1 T [TESTVIDEO01].PT-BR.DUBBED.M4A",
        ("pl", None): "1 T [TESTVIDEO01].pl.m4a"}


def test_video_file_row_by_day():
    info = {"id": "TESTVIDEO01", yt2html.VIDEO_KEY: {
        "date": "2026-11-13", "parameters": "audio:de",
        "video": {"file": "v.mp4", "size": 293 * MB, "date": "2026-11-12", "quality": "1080p, 30 fps",
                  "codec": "av1", "video_format": "399", "audio": "en", "audio_codec": "aac",
                  "audio_format": "140"},
        "extra_audio": [{"audio": "de", "role": "auto-dubbed", "file": "d.m4a", "size": 70 * MB,
                         "date": "2026-11-13", "audio_format": "140-3"},
                        {"audio": "pl", "role": "dubbed", "file": "p.m4a", "size": 69 * MB}],
        "subtitles": [{"code": "en", "kind": "author", "file": "e.srt", "size": 9,
                       "date": "2026-11-12"}]}}
    assert yt2html.about_video_file(info, ["01 v.mp4"], 293 * MB) == (
        "downloaded 12.11.2026: 1080p, 30 fps av1 + en aac, mp4, 293 MB; subtitles srt: en (author); "
        "downloaded 13.11.2026: extra audio m4a: de (auto-dubbed), 70 MB; "
        "next to the page: extra audio m4a: pl (dubbed), 69 MB; no video or audio formats in the data")
    # the video only found: from the data when nothing was found when building
    info[yt2html.VIDEO_KEY]["video"] = {"file": "v.mp4", "size": 2 * MB}
    assert yt2html.about_video_file(info).startswith("downloaded 12.11.2026: subtitles srt: en (author); "
                                                     "downloaded 13.11.2026: extra audio m4a: de (auto-dubbed)"
                                                     ", 70 MB; next to the page: video v.mp4, 2 MB; extra")
    assert yt2html.about_video_file({"id": "x"}) == "not downloaded; no video or audio formats in the data"
