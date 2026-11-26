"""--json show: what the saved data hold, by the four options, with the files next to them
(the user's decisions, 25.11.2026)."""

import json

import pytest

import yt2html
import yt2html_index

ID = "TESTVIDEO01"
OTHER = "OTHERVIDEO1"


def run(capsys, *argv):
    code = yt2html.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out


@pytest.fixture
def saved(tmp_path, info):
    """Saved data in tmp_path: the English transcript with YouTube's Polish translation."""
    path = tmp_path / f"Title [{ID}].info.json"
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return path


def rich(info):
    """Data with a little of everything the block shows."""
    transcript = info[yt2html.TRANSCRIPT_KEY]
    transcript["own"] = {"de": {"language": "de", "name": "German", "cues": transcript["cues"],
                                "own": {"file": f"Title [{ID}].de.own.srt", "date": "2026-11-23"}},
                         "pl": {"language": "pl", "name": "Polish", "cues": transcript["cues"],
                                "own": {"file": f"Title [{ID}].pl.own.srt", "date": "2026-11-24"}}}
    transcript["fixed"] = {"cues": transcript["cues"], "file": f"Title [{ID}].en.fixed.srt",
                           "date": "2026-11-25"}
    transcript["tracks"] = {"es.auto-generated": {"language": "es", "name": "Spanish",
                                                  "kind": "auto-generated",
                                                  "cues": transcript["cues"]}}
    info[yt2html.NOT_SAVED_KEY] = {
        "transcript fr": {"reason": "YouTube refused to send it (HTTP 429)", "date": "2026-11-14",
                          "again": True},
        "transcript it": {"reason": "YouTube offers no translation into it", "again": False}}
    info["epoch"] = None
    info[yt2html.ARCHIVED_KEY] = "2026-11-22"
    info[yt2html.DOWNLOAD_KEY] = {"yt2html": "0.10.7"}
    info[yt2html.UPDATES_KEY] = [
        {"date": "2026-11-24", "added": ["translation pl"]},
        {"date": "2026-11-25", "refreshed": True, "comments": {"new": 2, "gone": 1, "back": 3}}]
    info[yt2html.PLAYLIST_KEY] = {"id": "PLx", "title": "Series", "position": 3, "count": 12}
    info["comments"][0][yt2html.MISSING_SINCE_KEY] = "2026-11-25"
    info[yt2html.CARDS_KEY] = [{"type": "video"}, {"type": "link"}]
    info[yt2html.VIDEO_KEY] = {
        "date": "2026-11-22", "parameters": "720p,audio:es,sub:en",
        "video": {"file": f"Title [{ID}].mp4", "size": 11, "date": "2026-11-22",
                  "quality": "720p", "codec": "av1", "audio": "en", "audio_codec": "aac"},
        "extra_audio": [{"audio": "es", "role": "dubbed", "file": f"Title [{ID}].es.dubbed.m4a",
                         "size": 5, "date": "2026-11-22"}],
        "subtitles": [{"code": "en", "kind": "auto-generated", "file": "x.srt", "size": 3}]}
    return info


def test_the_block(capsys, tmp_path, info):
    path = tmp_path / f"Title [{ID}].info.json"
    path.write_text(json.dumps(rich(info), ensure_ascii=False), encoding="utf-8")
    (tmp_path / f"Title [{ID}].html").write_text("page", encoding="utf-8")
    (tmp_path / f"Title [{ID}].mp4").write_bytes(b"x" * 11)
    (tmp_path / f"Title [{ID}].en.auto-generated.srt").write_text("1", encoding="utf-8")
    (tmp_path / f"Title [{ID}].pl.own.srt").write_text("1", encoding="utf-8")
    (tmp_path / f"Title [{ID}] old.srt").write_text("1", encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert code == 0
    size = path.stat().st_size
    assert out.splitlines() == [
        f'Test: "video" | part 1 / <2>? [{ID}]  ({path.name}, {-(-size // 1000)} kB)',
        "  Data:       downloaded 22.11.2026 (yt2html 0.10.7)",
        "              updated 24.11.2026 (translation pl)",
        "              refreshed 25.11.2026 (2 new comments, 1 comment gone, 3 comments back)",
        "              playlist: video 3 of 12 in Series",
        "  Page:       7 comments of 9, 1 not on YouTube, 3 chapters, 2 info cards",
        "  Transcript: en (English, auto-generated), 3 lines",
        "              translations: de (own, 23.11.2026), pl (YouTube), pl (own, 24.11.2026)",
        f"              corrections: Title [{ID}].en.fixed.srt (25.11.2026)",
        "              other subtitles: es (auto-generated)",
        "              possible: plain, timed, dual:de, dual:pl, dual:pl.translated, "
        "plain:es.auto-generated",
        "  Video:      720p av1 + en aac, 22.11.2026 (--video 720p,audio:es)",
        "              extra audio: es (dubbed)",
        "              subtitles (--video sub:, before 0.11.0): en (auto-generated)",
        "  Not saved:  transcript fr: YouTube refused to send it (HTTP 429), 14.11.2026",
        "              transcript it: YouTube offers no translation into it (not asked for again)",
        f"  Next to it: page, mp4 (described), 3 SRTs: Title [{ID}] old.srt, "
        "en (auto-generated), pl (own)",
        f"              missing: Title [{ID}].es.dubbed.m4a",
        f"  (to try again: yt2html {ID} --json write --transcript plain,dual:fr)"]


def test_a_plain_block_and_what_is_not_there(capsys, tmp_path, saved, info):
    code, out = run(capsys, saved.name, "-o", str(tmp_path), "--json", "show")
    assert code == 0
    lines = out.splitlines()
    assert lines[1].startswith("  Data:       downloaded ")
    assert lines[2:] == [
        "  Page:       7 comments of 9, 3 chapters",
        "  Transcript: en (English, auto-generated), 3 lines",
        "              translations: pl (YouTube)",
        "              possible: plain, timed, dual:pl",
        "  Video:      none",
        "  Next to it: no page",
        f'  (to make the page: yt2html "{saved.name}" --json load --transcript)']
    # a video file the data do not describe; files the data name that are gone
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.VIDEO_KEY] = {"parameters": "", "video": {"file": f"Title [{ID}].mp4", "size": 4}}
    del data[yt2html.TRANSCRIPT_KEY]
    data[yt2html.TRANSCRIPT_KEY] = None
    data[yt2html.NOT_SAVED_KEY] = {"transcript": {"reason": "the video has no subtitles",
                                                  "date": "2026-11-22", "again": False}}
    data["comments"] = None
    data[yt2html.COMMENTS_MESSAGE_KEY] = "Comments are turned off."
    saved.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / f"Other name [{ID}].mp4").write_bytes(b"1234")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "  Page:       comments turned off" in out
    assert "  Transcript: none\n" in out
    assert ("  Not saved:  transcript: the video has no subtitles, 22.11.2026 (not asked for "
            "again)\n") in out
    assert f"  Video:      Title [{ID}].mp4, found next to the page" in out
    assert "  Next to it: no page, mp4 (not described)" in out
    (tmp_path / f"Other name [{ID}].mp4").unlink()
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert f"              missing: Title [{ID}].mp4" in out


def test_several_files_and_the_summary(capsys, tmp_path, saved, info):
    other = dict(info, id=OTHER, title="Other")
    other[yt2html.TRANSCRIPT_KEY] = dict(info[yt2html.TRANSCRIPT_KEY], translations={})
    other[yt2html.VIDEO_KEY] = {"parameters": "", "video": {"file": f"Other [{OTHER}].mp4",
                                                            "size": 3, "quality": "1080p",
                                                            "date": "2026-11-20"}}
    (tmp_path / f"Other [{OTHER}].info.json").write_text(json.dumps(other), encoding="utf-8")
    (tmp_path / f"Other [{OTHER}].mp4").write_bytes(b"123")
    (tmp_path / f"Other [{OTHER}].html").write_text("page", encoding="utf-8")
    (tmp_path / f"Title [{ID}].mp4").write_bytes(b"12")
    code, out = run(capsys, "*.info.json", "-o", str(tmp_path), "--json", "show")
    assert code == 0
    blocks = out.split("\n\n")
    assert len(blocks) == 3 and blocks[0].startswith("Other [")
    assert blocks[2].strip() == ("2 videos: translation pl in 1; video in 2 (1080p 1; 1 not "
                                 "described); no page for 1")
    other[yt2html.NOT_SAVED_KEY] = {"transcript de": {"reason": "r", "again": False}}
    (tmp_path / f"Other [{OTHER}].info.json").write_text(json.dumps(other), encoding="utf-8")
    code, out = run(capsys, "*.info.json", "-o", str(tmp_path), "--json", "show")
    assert out.rstrip().endswith("; something not saved in 1; no page for 1")
    # one that cannot be read: FAILED, the rest shown, code 1
    code, out = run(capsys, ID, "MISSINGVID1", "-o", str(tmp_path), "--json", "show")
    assert code == 1 and "FAILED: MISSINGVID1: no saved data for MISSINGVID1" in out
    assert f"[{ID}]" in out and "videos:" not in out


def test_the_videos_of_a_list_file(capsys, tmp_path, saved, info):
    listing = tmp_path / "list.txt"
    listing.write_text(f"https://www.youtube.com/watch?v={ID}\nPLkeptkeptkeptkept01\nPLnotkeptnotkeptnot1\n"
                       f"own file.mp4\n", encoding="utf-8")
    kept = {"PLkeptkeptkeptkept01": {"title": "Kept", "entries": [ID, OTHER], "read": "2026-11-20T10:00"}}
    (tmp_path / "list.html").write_text(
        f'<script type="application/json" id="{yt2html_index.DATA_ID}">{json.dumps(kept)}</script>',
        encoding="utf-8")
    code, out = run(capsys, "--index", str(listing), "-o", str(tmp_path), "--json", "show")
    assert code == 0
    assert "Playlist PLkeptkeptkeptkept01: the videos as the index keeps them (read 20.11.2026 10:00)." in out
    assert ("Playlist PLnotkeptnotkeptnot1: not in the index page, and --json show reads nothing from "
            f"YouTube; its videos are left out (make the index first: yt2html --index "
            f"{listing}).") in out
    assert out.count(f"[{ID}]  (") == 1   # once, though the playlist has it too
    assert out.rstrip().endswith(f"; no saved data for 1 ({OTHER})")


def test_options_it_refuses(capsys, tmp_path, saved):
    for extra, named in ((["--transcript"], "--transcript"), (["--page", "ovr"], "--page"),
                         (["--video"], "--video"), (["--video-formats"], "--video-formats"),
                         (["--take", "5"], "--take"), (["--olang", "pl"], "--olang"),
                         (["--transcript", "ovr"], "--transcript")):
        with pytest.raises(SystemExit):
            yt2html.parse_args([ID, "--json", "show", *extra])
        err = capsys.readouterr().err
        assert (f"--json show only shows what the saved data hold, so it cannot be used with "
                f"{named}") in err
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show", "--cookies", "firefox")
    assert code == 0 and "Note: --cookies has no effect with --json show" in out
    # nothing is written, nothing read from YouTube
    before = saved.read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == [saved.name]
    assert saved.read_bytes() == before


def test_other_states(capsys, tmp_path, saved):
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.UPDATES_KEY] = [{"date": "2026-11-26", "unavailable": True}]
    data[yt2html.UNAVAILABLE_KEY] = {"status": "private", "reason": "Private video"}
    data[yt2html.LIMIT_KEY] = 7
    data[yt2html.COMMENTS_CLOSED_KEY] = [{"from": "2026-11-26", "turned_off": True}]
    data[yt2html.NOT_SAVED_KEY] = {
        "transcript pt.author": {"reason": "YouTube refused (HTTP 429)", "date": "2026-11-26",
                                 "again": True}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "              no longer on YouTube 26.11.2026\n" in out
    assert "              no longer on YouTube: Private video\n" in out
    assert ("  Page:       7 comments of 9, limited to 7, turned off on YouTube since "
            "26.11.2026, 3 chapters\n") in out
    assert f"(to try again: yt2html {ID} --json write --transcript plain,plain:pt.author)" in out
    # comments turned on again; a video file of another size than the data say
    data[yt2html.COMMENTS_CLOSED_KEY] = [{"from": "2026-11-26", "until": "2026-11-27"}]
    data[yt2html.VIDEO_KEY] = {"video": {"file": f"Title [{ID}].mp4", "size": 9,
                                         "quality": "720p", "date": "2026-11-22"}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / f"Title [{ID}].mp4").write_bytes(b"12")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "  Page:       7 comments of 9, limited to 7, 3 chapters\n" in out
    assert "  Next to it: no page, mp4 (not described)\n" in out
    del data["comments"]
    data[yt2html.TRANSCRIPT_KEY]["cues"] = []
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "  Page:       comments left out, 3 chapters" in out
    assert "              possible: none" in out


def test_data_of_0_12_3_are_shown_in_today_s_form(capsys, tmp_path, saved):
    """The transcript's own "missing" and the reason for no transcript, as
    0.12.3 saved them: shown in Not saved; the file is not changed."""
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.TRANSCRIPT_KEY]["missing"] = {
        "fr": {"reason": "YouTube refused to send it (HTTP 429)", "refused": True,
               "date": "2026-11-14"}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    before = saved.read_bytes()
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert code == 0 and saved.read_bytes() == before
    assert "  Not saved:  transcript fr: YouTube refused to send it (HTTP 429), 14.11.2026\n" in out
    assert f"(to try again: yt2html {ID} --json write --transcript plain,dual:fr)" in out


def test_a_page_not_made_from_its_data(capsys, tmp_path, saved):
    (tmp_path / f"Title [{ID}].html").write_text(
        '<meta name="yt2html:data" content="000000000000"><title>x</title>', encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "  Next to it: page (not made from these data)\n" in out
    assert (f'(to make the page from these data: yt2html "{saved.name}" --json load '
            f'--transcript)') in out
    mark = yt2html.data_mark(saved.read_bytes())
    (tmp_path / f"Title [{ID}].html").write_text(
        f'<meta name="yt2html:data" content="{mark}"><title>x</title>', encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "  Next to it: page\n" in out and "to make the page" not in out
    # in the summary
    (tmp_path / f"Title [{ID}].html").write_text(
        '<meta name="yt2html:data" content="000000000000"><title>x</title>', encoding="utf-8")
    other = json.loads(saved.read_text(encoding="utf-8"))
    other.update(id=OTHER, title="Other")
    (tmp_path / f"Other [{OTHER}].info.json").write_text(json.dumps(other), encoding="utf-8")
    code, out = run(capsys, "*.info.json", "-o", str(tmp_path), "--json", "show")
    assert out.rstrip().endswith("; no page for 1; a page not made from its data for 1")


def test_not_saved_audio_with_its_own_hint(capsys, tmp_path, saved):
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.NOT_SAVED_KEY] = {
        "audio de": {"reason": "no connection", "date": "2026-11-26", "again": True},
        "audio fr": {"reason": "YouTube offers no such audio track", "again": False}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "  Not saved:  audio de: no connection, 26.11.2026\n" in out
    assert "              audio fr: YouTube offers no such audio track (not asked for again)\n" in out
    assert f"(to try again: yt2html {ID} --json write --video audio:de)" in out
    assert "--transcript plain,dual:de" not in out and "audio:fr" not in out
