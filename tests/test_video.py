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
    (URL, "is not a parameter (use a quality such as 720p or 720p30, audio:CODE or overwrite); "
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
         "audio_format": "140-1"}]


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
    assert yt2html.download_video(URL, args, page(tmp_path), set()) is None   # nothing new
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:de,overwrite"])
    assert [o["format"] for o in youtube["downloads"][1:]] == ["399+140", "140-3"]
    assert all(o["overwrites"] for o in youtube["downloads"][1:])


def test_audio_only_with_a_new_page(tmp_path, youtube, monkeypatch):
    # the page is made now, the video was there before: the row names both
    youtube["fresh"] = DUBS
    monkeypatch.setattr(yt2html, "existing_page", lambda folder, ident: None)
    name = yt2html.output_path({**yt2html.fetch_info(URL, None)}, tmp_path)
    Path(name).with_suffix(".mp4").write_bytes(b"v")
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl.auto-dubbed"]) == 0
    html = Path(name).read_text("utf-8")
    assert row(html, "Video file").startswith(
        f"a video file next to the page: {Path(name).stem}.mp4, &lt;1 MB; "
        f"extra audio m4a {TODAY}: pl (auto-dubbed), &lt;1 MB; ")
    assert marked(html, "chosen") == ["~9 MB"]          # only the track downloaded now


def test_audio_needs_no_image_within_the_limit(tmp_path, youtube):
    # the video is there; the list has no AV1 or H.264 within 480p: the track still comes
    youtube["fresh"] = [vfmt("244", 480, "vp9", 50 * MB)] + DUBS[1:]
    yt2html.main([URL, "-o", str(tmp_path)])
    (tmp_path / (page(tmp_path).stem + ".mp4")).write_bytes(b"v")
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "480p,audio:pl"]) == 0
    assert [o["format"] for o in youtube["downloads"]] == ["140-1"]
