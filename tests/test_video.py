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
        assert yt2html.video_id_from_url(source), f"not a video address: {source}"
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
    (" ovr , 480p ", (480, None, True)),
    ("1080p60", (1080, 60, False)),
])
def test_video_parameters(value, expected):
    spec = yt2html.video_spec(value)
    assert (spec["height"], spec["fps"], spec["ovr"]) == expected
    assert spec["text"] == value.strip().lower()


@pytest.mark.parametrize("value, message", [
    (URL, "is not a parameter (use a quality such as 720p or 720p30, audio:CODE or ovr; "
          "subtitles: --transcript srt); the video's address goes before --video"),
    ("sub:pl", "'sub:pl' is not a parameter"),           # subtitles: --transcript srt
    ("again", "'again' is not a parameter"),              # now ovr
    ("audio:pl!", "'audio:pl!': write audio:CODE or audio:CODE.ROLE"),
    ("audio:pl.auto_dubbed", "write audio:CODE or audio:CODE.ROLE, e.g. audio:pl"),
    ("audio:", "'audio:' is not a parameter"),
    ("hd", "'hd' is not a parameter"),
    ("700p", "'700p' is not a parameter"),            # not a quality YouTube names
    ("2160p", "'2160p' is above 1080p; yt2html downloads up to 1080p"),
    ("720p,480p", "give one quality only"),
    ("720p0", "no such frame rate"),
    ("ovr,ovr", "'ovr' is given more than once"),
    ("720p,,ovr", "empty item"),
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
    assert yt2html.parse_args(["--video", "--json", "write", URL]).video["text"] == ""
    assert yt2html.parse_args([URL]).video is None
    # the one mistake: the address taken as the parameters
    with pytest.raises(SystemExit):
        yt2html.parse_args(["--video", URL])
    assert "the video's address goes before --video" in capsys.readouterr().err


@pytest.mark.parametrize("options, message", [
    (["--json", "load"], "--video needs YouTube, so it cannot be used with --json load"),
    (["--index-only", "list.txt"], "--index-only downloads nothing and only makes the index, "
                                   "so it cannot be used with --video"),
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
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "--json", "write"]) == 0
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
    # the Command row: the files belong to the copy, right after the id
    assert row(html, "Command").startswith("yt2html TESTVIDEO01 --video")


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
    assert f"Video skipped (file exists): {page(tmp_path).with_suffix('.mp4')}. Add \"ovr\" " \
           "to --video to download it again." in out
    assert youtube["fetches"] == 1
    # again: downloaded again, yt-dlp told to overwrite
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "ovr"]) == 0
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
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "ovr"]) == 0
    assert ("Video skipped: no video or audio formats in the list (YouTube sometimes sends "
            "none for a while; try again later)." in capsys.readouterr().out)
    youtube["fresh"] = [vfmt("399", 1080, "av01.0.08M.08", 300 * MB)]
    yt2html.main([URL, "-o", str(tmp_path), "--video", "ovr"])
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
    monkeypatch.setattr(yt2html, "fetch_playlist", lambda source, args: {"title": "Test playlist",
                                                                  "entries": entries})
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
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "--json", "write"]) == 0
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


def test_audio_one_track_twice(info):
    # two codes for one track (the language finds its region): taken once, with a message
    info["formats"] = json.loads(json.dumps(DUBS))
    choice = yt2html.video_choice(info)
    found, messages = yt2html.resolve_audio(choice, yt2html.video_spec("audio:de,audio:de-DE")["audio"])
    assert found == [("de-DE", "auto-dubbed")]
    assert messages == ['"audio:de-DE" gives the same track as "audio:de"; taken once']
    # two roles of one language: two tracks
    found, _ = yt2html.resolve_audio(choice, yt2html.video_spec("audio:pl.dubbed,audio:pl.auto-dubbed")["audio"])
    assert found == [("pl", "dubbed"), ("pl", "auto-dubbed")]


@pytest.mark.parametrize("value, message", [
    ("audio:jp", "'audio:jp': 'jp' is not a language code (e.g. pl, en, pt-BR)"),
    ("audio:pl,audio:PL", "'audio:PL' is given more than once"),
    ("audio:pl,audio:pl.dubbed", "'audio:pl' and 'audio:pl.dubbed': give the language alone or "
                                 "with a role each time, not both"),
])
def test_audio_items_checked_at_once(value, message):
    # a mistyped language or an unclear repetition: before anything is fetched
    with pytest.raises(argparse.ArgumentTypeError, match=re.escape(message)):
        yt2html.video_spec(value)


def test_audio_with_the_video(tmp_path, youtube, capsys):
    youtube["fresh"] = DUBS
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl,audio:en",
                         "--json", "write"]) == 0
    html_path = page(tmp_path)
    track = html_path.with_name(html_path.stem + ".pl.dubbed.m4a")
    assert track.exists() and html_path.with_suffix(".mp4").exists()
    assert [o["format"] for o in youtube["downloads"]] == ["399+140", "140-1"]
    out = capsys.readouterr().out
    assert ('[yt2html] Audio: "audio:en" is already in the video.\n'
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
    # again: both there, nothing downloaded; "ovr" takes both again
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:de"]) == 0
    out = capsys.readouterr().out
    assert "Audio skipped (file exists): " in out and 'Add "ovr" to --video' in out
    assert len(youtube["downloads"]) == 1
    args = yt2html.parse_args([URL, "-o", str(tmp_path), "--video", "audio:de"])
    result, _, new = yt2html.download_video(URL, args, page(tmp_path), set())
    assert not new and result["extra_audio"] == [            # nothing new, both described
        {"audio": "de-DE", "role": "auto-dubbed", "file": page(tmp_path).stem
         + ".de-DE.auto-dubbed.m4a", "size": 1234}]
    assert result["video"] == {"file": page(tmp_path).stem + ".mp4", "size": 1}
    (tmp_path / (page(tmp_path).stem + ".de-DE.auto-dubbed.m4a")).unlink()
    assert yt2html.download_video(URL, args, page(tmp_path), set())[2]   # the track: new
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:de,ovr"])
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
    # looked for as soon as the video is found: a track without a file greyed out at once
    assert "if (!body.classList.contains('has-video')) all('[data-audio]', panel).forEach(findTrack);" in js
    assert "if (i >= names.length) { button.disabled = true; return; }" in js
    assert "button.dataset.file = names[i];" in js and "return button.dataset.file ? [button.dataset.file]" in js
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


def json3(text: str) -> bytes:
    """One cue in YouTube's json3 format (what a subtitles track is fetched as)."""
    return json.dumps({"events": [{"tStartMs": 1000, "dDurationMs": 1000,
                                   "segs": [{"utf8": text}]}]}).encode()


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


def rename_all(folder, prefix):
    """The user's numbering: every file of the video gets the same prefix."""
    for path in sorted(folder.glob("*TESTVIDEO01*")):
        path.rename(path.with_name(prefix + path.name))


def test_ovr_starts_from_scratch_with_the_files_found(tmp_path, youtube, capsys):
    # day 1: page, video and a track, with the data; day 2: everything again from YouTube
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"])
    data_path = page(tmp_path).with_suffix(".info.json")
    day = yt2html.format_date(json.loads(data_path.read_text("utf-8"))[yt2html.ARCHIVED_KEY])
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--page", "ovr", "--video", "audio:pl",
                         "--json", "write"]) == 0
    out = capsys.readouterr().out
    assert len(youtube["downloads"]) == 2                     # nothing downloaded again
    assert "Audio skipped (file exists)" in out and "Updated page" in out
    # the saved data are not read: what they said about the files is named as lost
    assert (f"Replacing the saved data from {day}: {data_path} (lost: the record of the "
            "downloaded files)") in out
    html = page(tmp_path).read_text("utf-8")
    assert row(html, "Video file").startswith("next to the page: video ")
    assert 'data-audio="' + page(tmp_path).stem + '.pl.dubbed.m4a"' in html   # the switch
    saved = json.loads(data_path.read_text("utf-8"))
    assert "date" not in saved[yt2html.VIDEO_KEY]["video"]
    assert list(tmp_path.glob("*.info.json")) == [data_path]     # replaced in its file


def test_without_json_the_saved_data_are_not_read(tmp_path, youtube, capsys):
    # data saved earlier describe the files; a page made without --json does not read them
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"])
    data_path = page(tmp_path).with_suffix(".info.json")
    data = data_path.read_bytes()
    assert yt2html.main([URL, "-o", str(tmp_path), "--page", "ovr", "--video", "audio:pl"]) == 0
    assert row(page(tmp_path).read_text("utf-8"), "Video file").startswith("next to the page: ")
    assert data_path.read_bytes() == data                      # and not written either


def test_json_write_with_nothing_missing_leaves_page_and_files(tmp_path, youtube, capsys):
    # the files and the data are there: nothing is asked, nothing is written
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"])
    data_path, html_path = page(tmp_path).with_suffix(".info.json"), page(tmp_path)
    before = data_path.stat().st_mtime_ns, html_path.stat().st_mtime_ns
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"]) == 0
    out = capsys.readouterr().out
    assert "the page stays as it is unless --video adds files" in out
    assert "Updated page" not in out and "Saved page" not in out
    assert (data_path.stat().st_mtime_ns, html_path.stat().st_mtime_ns) == before
    assert len(youtube["downloads"]) == 2
    # a track that is not there yet: downloaded, then the data and the page follow
    (tmp_path / (html_path.stem + ".pl.dubbed.m4a")).unlink()
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"]) == 0
    out = capsys.readouterr().out
    assert len(youtube["downloads"]) == 3 and "Updated page" in out
    saved = json.loads(data_path.read_text("utf-8"))
    assert saved[yt2html.VIDEO_KEY]["video"]["date"] == dt.date.today().isoformat()  # kept


def test_renamed_files_are_found_and_described(tmp_path, youtube, capsys):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"])
    rename_all(tmp_path, "01 ")
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--page", "ovr", "--video", "audio:pl",
                         "--json", "write"]) == 0
    assert len(youtube["downloads"]) == 2                     # recognised under the new names
    html_path = page(tmp_path)
    assert html_path.name.startswith("01 ")                   # the page keeps its name
    html = html_path.read_text("utf-8")
    track = html_path.stem + ".pl.dubbed.m4a"
    assert f'data-audio="{track}" data-found="{track}"' in html
    assert row(html, "Video file").startswith("next to the page: video 01 ")   # ovr: from scratch
    assert sorted(p.name[:3] for p in tmp_path.iterdir()) == ["01 "] * 4   # html, json, mp4, m4a


def test_a_rebuild_finds_renamed_tracks(tmp_path, youtube):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl", "--json", "write"])
    old = page(tmp_path).stem
    rename_all(tmp_path, "02 ")
    assert yt2html.main(["--json", "load", "TESTVIDEO01", "-o", str(tmp_path)]) == 0
    html = page(tmp_path).read_text("utf-8")
    # the saved name stays as the last try; the file found by id and ending comes first
    assert f'data-audio="{old}.pl.dubbed.m4a" data-found="02 {old}.pl.dubbed.m4a"' in html
    # gone: nothing found, the page's own name and the saved one are still tried
    (tmp_path / f"02 {old}.pl.dubbed.m4a").unlink()
    yt2html.main(["--json", "load", "TESTVIDEO01", "-o", str(tmp_path)])
    assert f'data-audio="{old}.pl.dubbed.m4a" data-found="" ' in page(tmp_path).read_text("utf-8")


def test_files_found_without_saved_data(tmp_path, youtube):
    # downloaded without --json write: made again, the files are only found
    youtube["fresh"] = DUBS + [dub("251-9", "pl", "dubbed", "opus")]
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl"])
    assert yt2html.main([URL, "-o", str(tmp_path), "--page", "ovr", "--video", "audio:pl"]) == 0
    html = page(tmp_path).read_text("utf-8")
    assert row(html, "Video file").startswith(
        f"next to the page: video {page(tmp_path).stem}.mp4, &lt;1 MB; "
        "extra audio m4a: pl (dubbed), &lt;1 MB; Deno at download")
    # yellow: the track's aac (code and role are certain), not the image (its variant is not)
    assert marked(html, "chosen") == ["~9 MB"]
    assert ('<td>pl</td><td>pl</td><td>dubbed</td><td class="size chosen">~9 MB</td>'
            '<td class="size">~9 MB</td>') in html             # its opus not
    assert 'data-audio="' + page(tmp_path).stem + '.pl.dubbed.m4a"' in html


def test_the_video_only_found_keeps_the_list_of_formats(tmp_path, youtube):
    yt2html.main([URL, "-o", str(tmp_path), "--video"])
    assert yt2html.main([URL, "-o", str(tmp_path), "--page", "ovr", "--video"]) == 0
    assert youtube["fetches"] == 1                            # no list needed for a file there
    html = page(tmp_path).read_text("utf-8")
    assert row(html, "Video file").endswith("; Deno at download: yes")
    assert marked(html, "planned") == ["~309 MB", "~9 MB"]   # the page's own list of formats


def test_described_only_the_same_file():
    old = [{"audio": "pl", "role": "dubbed", "file": "a.m4a", "size": 5, "date": "2026-11-12",
            "audio_format": "140-1"}]
    entry = {"audio": "pl", "role": "dubbed", "file": "01 a.m4a", "size": 5}
    assert yt2html.described(entry, old, "audio", "role") == {**old[0], "file": "01 a.m4a"}
    assert yt2html.described({**entry, "size": 6}, old, "audio", "role") == {**entry, "size": 6}
    assert yt2html.described({**entry, "role": None}, old, "audio", "role") == {**entry, "role": None}
    assert yt2html.described(entry, [{**old[0], "date": None}], "audio", "role") == entry


def test_replaced_data_note(tmp_path):
    path = tmp_path / "x.info.json"
    path.write_text("not json", encoding="utf-8")
    assert yt2html.replaced_data_note(path, {}) == f"Replacing saved data that could not be read: {path}"
    own = {"language": "pl", "cues": [], "own": {"file": "x.pl.own.srt", "date": "2026-11-01"}}
    youtube = {"language": "de", "cues": []}
    path.write_text(json.dumps({
        yt2html.ARCHIVED_KEY: "2026-10-12",
        yt2html.TRANSCRIPT_KEY: {"language": "en", "translations": {"pl": own, "de": youtube,
                                                                    "fr": youtube, "it": own}},
        "comments": [{"id": "a"}, {"id": "b", yt2html.MISSING_SINCE_KEY: "2026-11-02"}],
        yt2html.VIDEO_KEY: {"video": {}}}), encoding="utf-8")
    fresh = {yt2html.TRANSCRIPT_KEY: {"translations": {"fr": youtube}}}
    assert yt2html.replaced_data_note(path, fresh) == (
        f"Replacing the saved data from 12.10.2026: {path} (lost: own translations it, pl; "
        "1 comment no longer on YouTube; YouTube's translation into de; the record of the "
        "downloaded files)")
    path.write_text(json.dumps({"comments": []}), encoding="utf-8")
    assert yt2html.replaced_data_note(path, {}) == f"Replacing the saved data: {path}"


def test_again_downloads_into_the_renamed_files(tmp_path, youtube):
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl"])
    stem = page(tmp_path).stem
    for ext in (".mp4", ".pl.dubbed.m4a"):   # only the video and the track renamed
        (tmp_path / (stem + ext)).rename(tmp_path / ("03 " + stem + ext))
    yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl,ovr"])
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


def test_update_json_records_the_files(tmp_path, youtube, capsys):
    # the page and its data first; then --json write --video adds the files to the data
    youtube["fresh"] = DUBS
    assert yt2html.main([URL, "-o", str(tmp_path), "--json", "write"]) == 0
    data_path = page(tmp_path).with_suffix(".info.json")
    data = json.loads(data_path.read_text("utf-8"))
    data[yt2html.LIMIT_KEY] = 7                 # downloaded with a limit (YouTube counts 9)
    data_path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--json", "write", "--video", "audio:pl",
                         "--page", "comments:100"]) == 0
    out = capsys.readouterr().out
    assert f"Updated metadata: {data_path}" in out
    assert out.count("The saved data has only 7 comments") == 1       # told once
    data = json.loads(data_path.read_text("utf-8"))
    assert len(data["comments"]) == 7
    assert [a["audio"] for a in data[yt2html.VIDEO_KEY]["extra_audio"]] == ["pl"]
    assert data[yt2html.UPDATES_KEY][-1]["added"] == ["video", "extra audio pl (dubbed)"]
    html = page(tmp_path).read_text("utf-8")
    assert row(html, "Video file").startswith(f"downloaded {TODAY}: 1080p")
    assert f"; updated {TODAY} {data[yt2html.UPDATES_KEY][-1]['time']}</dd>" in html
    # again, all files there: nothing new to record
    assert yt2html.main([URL, "-o", str(tmp_path), "--json", "write", "--video", "audio:pl",
                         "--page", "comments:2"]) == 0
    data = json.loads(data_path.read_text("utf-8"))
    assert len(data[yt2html.UPDATES_KEY]) == 1                 # no new entry
    assert len(data["comments"]) == 7          # the page shows fewer, the data keep all


def test_update_json_narrows_only_the_page(tmp_path, youtube):
    # cards:no with --video: the data are saved again after the files, still with the cards
    assert yt2html.main([URL, "-o", str(tmp_path), "--json", "write"]) == 0
    data_path = page(tmp_path).with_suffix(".info.json")
    data = json.loads(data_path.read_text("utf-8"))
    card = {"type": "video", "title": "Card", "url": "https://www.youtube.com/watch?v=abcdefghijk"}
    data[yt2html.CARDS_KEY] = [card]
    data_path.write_text(json.dumps(data), encoding="utf-8")
    assert yt2html.main([URL, "-o", str(tmp_path), "--json", "write", "--video",
                         "--page", "cards:no"]) == 0
    assert json.loads(data_path.read_text("utf-8"))[yt2html.CARDS_KEY] == [card]
    assert row(page(tmp_path).read_text("utf-8"), "Info cards") == "left out"


def test_json_write_with_renamed_data_keeps_what_the_files_were(tmp_path, youtube):
    # the data read now describe the files, under whatever name they have in -o
    youtube["fresh"] = DUBS
    out = tmp_path / "out"
    assert yt2html.main([URL, "-o", str(out), "--json", "write", "--video"]) == 0
    data_path = page(out).with_suffix(".info.json")
    renamed = data_path.with_name("Old name [TESTVIDEO01].info.json")
    data_path.rename(renamed)
    assert yt2html.main([str(renamed), "-o", str(out), "--json", "write", "--video",
                         "audio:pl"]) == 0
    files = json.loads(renamed.read_text("utf-8"))[yt2html.VIDEO_KEY]
    assert all(entry.get("date") == dt.date.today().isoformat()
               for entry in [files["video"], *files["extra_audio"]])
    assert json.loads(renamed.read_text("utf-8"))[yt2html.UPDATES_KEY][-1]["added"] == [
        "extra audio pl (dubbed)"]


def test_interrupted_writes_leave_no_cut_files(tmp_path, youtube, monkeypatch):
    # an SRT file: only the temporary file is left, so the next page writes it again
    def interrupted(source, target):
        raise KeyboardInterrupt
    assert yt2html.main([URL, "-o", str(tmp_path), "--json", "write"]) == 0
    monkeypatch.setattr(yt2html.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        yt2html.write_srt_files(json.loads(page(tmp_path).with_suffix(".info.json").read_text("utf-8")),
                                [("plain", None), ("srt", None)], page(tmp_path))
    assert not list(tmp_path.glob("*.srt"))
    assert list(tmp_path.glob("*.srt.part"))


def test_files_all_there_ask_youtube_nothing(tmp_path, youtube, monkeypatch, capsys):
    subtitles = youtube
    """A page left as it is, with the video and every track and subtitles asked
    for next to it: no list of formats, so no pause before the next video
    either (the user, 20.11.2026: a run that only fills gaps in a playlist)."""
    subtitles["fresh"] = DUBS
    playlist = "https://www.youtube.com/playlist?list=PLtest1234567890"
    entries = [{"_type": "url", "id": f"TESTVIDEO0{n}", "title": f"Episode {n}",
                "url": f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"} for n in (1, 2)]
    monkeypatch.setattr(yt2html, "fetch_playlist", lambda source, args: {"title": "Test playlist",
                                                                  "entries": entries})
    command = [playlist, "-o", str(tmp_path), "--video", "audio:de"]
    assert yt2html.main(command) == 0
    fetches, downloads = subtitles["fetches"], len(subtitles["downloads"])
    subtitles["sleeps"].clear()
    capsys.readouterr()
    assert yt2html.main(command) == 0
    out = capsys.readouterr().out
    assert subtitles["fetches"] == fetches and subtitles["sleeps"] == []
    assert "fetching the current list of formats" not in out
    assert out.count("Audio skipped (file exists): ") == 2
    assert "Videos: 0 downloaded, 2 skipped, 0 failed." in out
    assert len(subtitles["downloads"]) == downloads
    # one file missing: that video asks for the list and takes only that file
    next(tmp_path.glob("*TESTVIDEO02*.de-DE.auto-dubbed.m4a")).unlink()
    assert yt2html.main(command) == 0
    assert subtitles["fetches"] == fetches + 1
    assert len(subtitles["downloads"]) == downloads + 1
    # a page made again takes the fresh list (it tells which formats the files came from)
    assert yt2html.main(command + ["--page", "ovr"]) == 0
    assert subtitles["fetches"] == fetches + 3


def test_files_at_hand_rules(tmp_path):
    def found(value):
        at_hand = yt2html.files_at_hand(tmp_path, "TESTVIDEO01", yt2html.video_spec(value))
        return None if at_hand is None else [(key, path.name) for key, path in at_hand]

    def make(*names):
        for name in names:
            (tmp_path / name).write_bytes(b"x")

    assert found("720p") == []                                       # nothing asked
    make("Title [TESTVIDEO01].mp4", "Title [TESTVIDEO01].f140.m4a")   # not an extra track
    assert found("audio:de") is None
    make("Title [TESTVIDEO01].de.descriptive.m4a")
    assert found("audio:de") is None                    # an audio description only if asked
    assert found("audio:de.descriptive") == [(("de", "descriptive"),
                                              "Title [TESTVIDEO01].de.descriptive.m4a")]
    make("04 Title [TESTVIDEO01] (copy).es-US.dubbed.m4a")           # renamed, regional
    assert found("audio:es") == [(("es-US", "dubbed"),
                                  "04 Title [TESTVIDEO01] (copy).es-US.dubbed.m4a")]
    assert found("audio:es-ES") is None                 # a code with a region: exactly
    make("Title [TESTVIDEO01].de-DE.dubbed.m4a")        # as resolve_audio(): an exact code
    assert found("audio:de") is None                    # ("de") hides the regional ones
    make("Title [TESTVIDEO01].pt-BR.auto-generated.srt")   # subtitles: not --video's
    assert found("audio:es-US") == [(("es-US", "dubbed"),
                                     "04 Title [TESTVIDEO01] (copy).es-US.dubbed.m4a")]


def test_files_added_to_a_page_left_as_it_is_are_named(tmp_path, youtube, capsys):
    # without --json the page is not made again: the console says what that means
    youtube["fresh"] = DUBS
    yt2html.main([URL, "-o", str(tmp_path)])
    capsys.readouterr()
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl"]) == 0
    assert ("  Warning: the page was not made again, so its Video file row does not list the "
            "new files and the player has no switch for the new audio tracks. To add them: "
            "--page ovr (everything again from YouTube), or --json write if the page has saved "
            "data.") in capsys.readouterr().out
    # nothing new: nothing to say
    assert yt2html.main([URL, "-o", str(tmp_path), "--video", "audio:pl"]) == 0
    assert "Warning: the page was not made again" not in capsys.readouterr().out


def test_the_command_row_leaves_out_old_parameters(info):
    # data saved before --transcript srt: "again" and sub: are no part of --video now
    info[yt2html.VIDEO_KEY] = {"parameters": "720p30, again, sub:pl, audio:pl"}
    html = render_page(info)
    assert "--video 720p30,audio:pl" in re.search(r'<code class="command">(.*?)</code>', html).group(1)
