"""--json show: what the saved data hold, by the four options, with the files next to them
(the user's decisions, 25.11.2026)."""

import datetime as dt
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
        "  Contents:   playlist            saved: video 3 of 12 in Series",
        "              video page          saved",
        "              markers             saved: chapters (3), from the description; "
        "walkthroughs (1)",
        "              info cards          saved: 2",
        "              transcript          saved: en (English, auto-generated), 3 lines",
        "              transcript de       saved: own translation, 23.11.2026",
        "              transcript es.auto-generated  saved: subtitles of their own",
        "              transcript fr       not saved: YouTube refused to send it (HTTP 429), "
        "14.11.2026",
        "              transcript it       not saved: YouTube offers no translation into it",
        "                                  (not asked for again)",
        "              transcript pl       saved: YouTube's translation; own translation, "
        "24.11.2026",
        "              comments            saved: 7 of 9, 1 not on YouTube",
        "              images              unknown: the page was made before this version",
        "                                  (--json load makes it again and notes them)",
        "              video file          saved: 720p av1 + en aac, 22.11.2026 "
        "(--video 720p,audio:es)",
        "              subtitle files      saved: en (auto-generated) (--video sub:, before 0.11.0)",
        "              extra audio es      saved: dubbed",
        f"  Transcript: corrections: Title [{ID}].en.fixed.srt (25.11.2026)",
        "              possible: plain, timed, dual:de, dual:pl, dual:pl.translated, "
        "plain:es.auto-generated",
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
        "  Contents:   video page          saved",
        "              markers             saved: chapters (3), from the description; "
        "walkthroughs (1)",
        "              transcript          saved: en (English, auto-generated), 3 lines",
        "              transcript pl       saved: YouTube's translation",
        "              comments            saved: 7 of 9",
        "  Transcript: possible: plain, timed, dual:pl",
        "  Next to it: no page",
        f'  (to make the page: yt2html "{saved.name}" --json load --transcript)']
    # a video file the data do not describe; files the data name that are gone
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.VIDEO_KEY] = {"parameters": "", "video": {"file": f"Title [{ID}].mp4", "size": 4}}
    del data[yt2html.TRANSCRIPT_KEY]   # none came: only its note says so
    data[yt2html.NOT_SAVED_KEY] = {"transcript": {"reason": "the video has no subtitles",
                                                  "date": "2026-11-22", "again": False}}
    data["comments"] = None
    data[yt2html.COMMENTS_MESSAGE_KEY] = "Comments are turned off."
    saved.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / f"Other name [{ID}].mp4").write_bytes(b"1234")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "              comments            not saved: Comments are turned off.\n" in out
    assert "  Transcript:" not in out
    assert ("              transcript          not saved: the video has no subtitles, 22.11.2026\n"
            "                                  (not asked for again)\n") in out
    assert (f"              video file          saved: Title [{ID}].mp4, found next to the "
            "page\n") in out
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
    assert ("              comments            saved: 7 of 9, limited to 7\n"
            "                                  turned off on YouTube since 26.11.2026\n") in out
    assert ("              transcript pt.author  not saved: YouTube refused (HTTP 429), "
            "26.11.2026\n") in out
    assert f"(to try again: yt2html {ID} --json write --transcript plain,plain:pt.author)" in out
    # comments turned on again; a video file of another size than the data say
    data[yt2html.COMMENTS_CLOSED_KEY] = [{"from": "2026-11-26", "until": "2026-11-27"}]
    data[yt2html.VIDEO_KEY] = {"video": {"file": f"Title [{ID}].mp4", "size": 9,
                                         "quality": "720p", "date": "2026-11-22"}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / f"Title [{ID}].mp4").write_bytes(b"12")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "              comments            saved: 7 of 9, limited to 7\n" in out
    assert "  Next to it: no page, mp4 (not described)\n" in out
    del data["comments"]
    data[yt2html.TRANSCRIPT_KEY]["cues"] = []
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "comments" not in out.split("  Transcript:")[0]   # not asked for: only show:full
    assert "  Transcript: possible: none" in out
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show:full")
    assert "              comments            not asked for: --page comments:no\n" in out


def test_data_of_0_12_3_are_shown_in_today_s_form(capsys, tmp_path, saved):
    """The transcript's own "missing" and the reason for no transcript, as
    0.12.3 saved them: shown as not saved; the file is not changed."""
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.TRANSCRIPT_KEY]["missing"] = {
        "fr": {"reason": "YouTube refused to send it (HTTP 429)", "refused": True,
               "date": "2026-11-14"}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    before = saved.read_bytes()
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert code == 0 and saved.read_bytes() == before
    assert ("              transcript fr       not saved: YouTube refused to send it (HTTP 429), "
            "14.11.2026\n") in out
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
    assert "              extra audio de      not saved: no connection, 26.11.2026\n" in out
    assert ("              extra audio fr      not saved: YouTube offers no such audio track\n"
            "                                  (not asked for again)\n") in out
    assert f"(to try again: yt2html {ID} --json write --video audio:de)" in out
    assert "--transcript plain,dual:de" not in out and "audio:fr" not in out


# The three levels and Contents (notes 5.4, the user's decisions 26.11.2026)

PARTIAL = {yt2html.NOT_SAVED_KEY: {"watch page": {
    "reason": "read without the player data (KeyError)", "date": "2026-11-23", "again": False}}}
FAILED = {yt2html.EXTRAS_KEY: None, yt2html.NOT_SAVED_KEY: {
    "watch page": {"reason": "could not be read (Expecting value: line 43)", "date": "2026-11-23",
                   "again": False},
    "cards": {"reason": "the video page was not read", "date": "2026-11-23", "again": False}}}

def test_the_levels_of_show():
    for value, level in (("show", "standard"), ("show:brief", "brief"), ("SHOW:Full", "full"),
                         ("show:standard", "standard"), ("show: full ", "full")):
        args = yt2html.parse_args([ID, "--json", value])
        assert (args.json, args.show_json, args.show_level) == ("show", True, level)
    assert yt2html.parse_args([ID, "--json", "write"]).show_level == "standard"


@pytest.mark.parametrize("value, message", [
    ("show:short", "'show:short': show takes brief, standard (the default) or full after the colon"),
    ("show:", "'show:': show takes brief"),
    ("write:full", "'write:full' is not a parameter"),
])
def test_a_level_it_does_not_know(capsys, value, message):
    with pytest.raises(SystemExit):
        yt2html.parse_args([ID, "--json", value])
    assert message in capsys.readouterr().err


def test_the_full_map_names_what_no_command_asked_for(capsys, tmp_path, saved):
    data = json.loads(saved.read_text(encoding="utf-8"))
    data.pop(yt2html.CARDS_KEY, None)
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show:full")
    assert code == 0
    contents = out.split("  Contents:   ")[1].split("  Transcript:")[0].splitlines()
    assert contents == [
        "playlist            not asked for: downloaded on its own",
        "              video page          saved",
        "              markers             saved: chapters (3), from the description; "
        "walkthroughs (1)",
        "              info cards          not asked for",
        "              transcript          saved: en (English, auto-generated), 3 lines",
        "              transcript pl       saved: YouTube's translation",
        "              comments            saved: 7 of 9",
        "              video file          not asked for",
        "              extra audio         not asked for"]
    code, standard = run(capsys, ID, "-o", str(tmp_path), "--json", "show:standard")
    assert "not asked for" not in standard
    assert standard == run(capsys, ID, "-o", str(tmp_path), "--json", "show")[1]


@pytest.mark.parametrize("change, row", [
    (PARTIAL, ("video page", "partly saved",
               ["read without the player data (KeyError), 23.11.2026",
                "(transcript language guessed)"])),
    (FAILED, ("video page", "not saved", ["could not be read (Expecting value: line 43), "
                                          "23.11.2026", "(--page ovr downloads it anew)"])),
    (FAILED, ("info cards", "not saved",
              ["the video page was not read, 23.11.2026 (not asked for again)"])),
    ({yt2html.CARDS_KEY: []}, ("info cards", "saved", ["none"])),
    ({"comments": None}, ("comments", "not saved", ["none came, reason unknown",
                                                     "(--json refresh asks again)"])),
    ({"comment_count": None}, ("comments", "partly saved", ["7 of 9, interrupted (Ctrl+C)",
                                                            "(--json refresh gets the rest)"])),
    ({yt2html.PLAYLIST_KEY: None}, ("playlist", "not asked for", ["downloaded on its own"])),
    ({yt2html.MARKERS_KEY: [], "chapters": None}, ("markers", "saved", ["none"])),
    ({yt2html.NOT_SAVED_KEY: {"video": {"reason": "HTTP Error 403: Forbidden", "date": "2026-11-23",
                                        "again": True}}},
     ("video file", "not saved", ["HTTP Error 403: Forbidden, 23.11.2026"])),
])
def test_the_state_of_each_part(info, change, row):
    rows = yt2html.show_contents({**info, **change})
    assert row in rows
    assert [label for label, _, _ in rows if label == row[0]] == [row[0]]   # once


def test_chapters_without_the_video_page_and_a_translation_partly_saved(info):
    info.update(FAILED)
    transcript = info[yt2html.TRANSCRIPT_KEY]
    transcript["own"] = {"fr": {"language": "fr", "cues": transcript["cues"],
                                "own": {"file": "x.fr.own.srt"}}}
    info[yt2html.NOT_SAVED_KEY] = {**FAILED[yt2html.NOT_SAVED_KEY], "transcript fr": {
        "reason": "YouTube refused to send it", "date": "2026-11-14", "again": True}}
    rows = yt2html.show_contents(info)
    assert ("markers", "saved", ["chapters (3), origin unknown; walkthroughs (1)"]) in rows
    assert ("transcript fr", "partly saved",
            ["own translation; not saved: YouTube refused to send it, 14.11.2026"]) in rows


def test_a_note_no_part_reads_is_shown_too(info):
    info[yt2html.NOT_SAVED_KEY] = {"thumbnail": {"reason": "HTTP 404", "again": False}}
    assert yt2html.show_contents(info)[-1] == ("thumbnail", "not saved",
                                               ["HTTP 404 (not asked for again)"])


def test_a_transcript_noted_as_missing_without_a_reason(info):
    del info[yt2html.TRANSCRIPT_KEY]
    info[yt2html.NOT_SAVED_KEY] = {"transcript": {"again": False}}
    assert ("transcript", "not saved", ["reason unknown (not asked for again)"]) in \
        yt2html.show_contents(info)
    del info[yt2html.NOT_SAVED_KEY]
    rows = yt2html.show_contents(info)
    assert ("transcript", "not asked for", []) in rows
    assert not [label for label, _, _ in rows if label.startswith("transcript ")]


def test_brief(capsys, tmp_path, saved, info):
    (tmp_path / f"Title [{ID}].html").write_text("page", encoding="utf-8")
    other = json.loads(saved.read_text(encoding="utf-8"))
    other.update(id=OTHER, title="Other", comment_count=None)
    other.update(FAILED)
    other.pop(yt2html.CARDS_KEY, None)
    other[yt2html.NOT_SAVED_KEY] = {**FAILED[yt2html.NOT_SAVED_KEY],
                                    "audio de": {"reason": "r", "again": True},
                                    "transcript fr": {"reason": "r", "again": True}}
    (tmp_path / f"Other [{OTHER}].info.json").write_text(json.dumps(other), encoding="utf-8")
    code, out = run(capsys, "*.info.json", "-o", str(tmp_path), "--json", "show:brief")
    assert code == 0
    assert out.splitlines() == [
        f"Other [{OTHER}]  not saved: video page, info cards, transcript fr, extra audio de; "
        "partly saved: comments; no page",
        f'Test: "video" | part 1 / <2>? [{ID}]  all saved',
        "",
        "2 videos: translation pl in 2; no video files; something not saved in 1; no page for 1"]
    (tmp_path / f"Title [{ID}].html").write_text(
        '<meta name="yt2html:data" content="000000000000"><title>x</title>', encoding="utf-8")
    data = json.loads(saved.read_text(encoding="utf-8"))
    data[yt2html.VIDEO_KEY] = {"video": {"file": f"Title [{ID}].mp4", "size": 3}}
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show:brief")
    assert out == (f'Test: "video" | part 1 / <2>? [{ID}]  page not made from these data; '
                   "files missing\n")


def test_a_long_line_puts_its_remark_below_and_nothing_else():
    width = yt2html.SHOW_WIDTH - yt2html.SHOW_INDENT
    short = ("transcript it", "not saved", ["YouTube has none (not asked for again)"])
    long_reason = "x" * (width - 40)
    long_row = ("transcript it", "not saved", [f"{long_reason} (not asked for again)"])
    assert yt2html.contents_lines([short]) == [
        "transcript it       not saved: YouTube has none (not asked for again)"]
    assert yt2html.contents_lines([long_row]) == [
        f"transcript it       not saved: {long_reason}",
        "                    (not asked for again)"]
    # a long name has no remark to move: it stays whole
    name = ("video file", "saved", ["y" * width])
    assert yt2html.contents_lines([name]) == [f"video file          saved: {'y' * width}"]


def test_the_texts_of_show_fit_the_width(info):
    """Every fixed text of Contents (labels, states, the program's own words)
    within SHOW_WIDTH: rows of each state, the longest labels."""
    # (the error in a note of the video page is Python's or YouTube's text: left out here)
    failed = json.loads(json.dumps(FAILED))
    failed[yt2html.NOT_SAVED_KEY]["watch page"]["reason"] = "could not be read (X)"
    partial = json.loads(json.dumps(PARTIAL))
    partial[yt2html.NOT_SAVED_KEY]["watch page"]["reason"] = "read without the player data (X)"
    states = [partial, failed, {"comments": None}, {"comment_count": None},
              {yt2html.COMMENTS_MESSAGE_KEY: "Comments are turned off.", "comments": None},
              {yt2html.LIMIT_KEY: 7, yt2html.COMMENTS_CLOSED_KEY: [{"from": "2026-11-26"}]},
              {yt2html.NOT_SAVED_KEY: {"transcript": {"reason": "the video has no captions",
                                                      "date": "2026-11-22", "again": False},
                                       "audio de.auto-dubbed": {
                                           "reason": "YouTube offers no such audio track",
                                           "date": "2026-11-22", "again": False}}}]
    for change in states:
        for line in yt2html.contents_lines(yt2html.show_contents({**info, **change})):
            assert yt2html.SHOW_INDENT + len(line) <= yt2html.SHOW_WIDTH, line


def test_the_parts_of_a_copy_as_development_names_them():
    """docs/DEVELOPMENT.md (*The parts of a copy*) lists ELEMENTS in their order."""
    from pathlib import Path
    text = (Path(__file__).parent.parent / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    part = text.split("### The parts of a copy")[1].split("\n#")[0]
    named = [line.split("`")[1] for line in part.splitlines() if line.startswith("| `")]
    assert named == [name for name, _, _ in yt2html.ELEMENTS]


def test_a_line_of_exactly_the_width_stays_whole():
    width = yt2html.SHOW_WIDTH - yt2html.SHOW_INDENT
    head = "transcript it       not saved: "
    remark = " (not asked for again)"
    reason = "x" * (width - len(head) - len(remark))
    line = head + reason + remark
    assert len(line) == width
    assert yt2html.contents_lines([("transcript it", "not saved", [reason + remark])]) == [line]


def test_a_part_only_partly_saved_counts_in_the_summary(capsys, tmp_path, saved):
    other = json.loads(saved.read_text(encoding="utf-8"))
    other.update(id=OTHER, title="Other", comment_count=None)   # interrupted, nothing else
    (tmp_path / f"Other [{OTHER}].info.json").write_text(json.dumps(other), encoding="utf-8")
    code, out = run(capsys, "*.info.json", "-o", str(tmp_path), "--json", "show:brief")
    assert f"Other [{OTHER}]  partly saved: comments; no page" in out
    assert "; something not saved in 1;" in out


def test_a_transcript_by_the_author(info):
    info[yt2html.TRANSCRIPT_KEY]["kind"] = "manual"
    assert ("transcript", "saved", ["en (English, by the author), 3 lines"]) in \
        yt2html.show_contents(info)


def test_comments_not_all_there_with_the_day(info):
    info.update(comments=None)
    info[yt2html.COMMENTS_MESSAGE_KEY] = "Comments are turned off."
    info[yt2html.NOT_SAVED_KEY] = {"comments": {"reason": "Comments are turned off.",
                                                "date": "2026-11-20", "again": False}}
    assert ("comments", "not saved", ["Comments are turned off, 20.11.2026",
                                      "(--json refresh asks again)"]) in yt2html.show_contents(info)
    del info[yt2html.NOT_SAVED_KEY]   # no day (data saved before): YouTube's text as it is
    assert ("comments", "not saved", ["Comments are turned off.",
                                      "(--json refresh asks again)"]) in yt2html.show_contents(info)
    info.update(comments=info["comments"] or [{"id": "a", "text": "x"}], comment_count=None)
    info[yt2html.NOT_SAVED_KEY] = {"comments": {"reason": "interrupted (Ctrl+C)",
                                                "date": "2026-11-21", "again": True}}
    rows = yt2html.show_contents(info)
    assert ("comments", "partly saved", ["1 of 9, interrupted (Ctrl+C), 21.11.2026",
                                         "(--json refresh gets the rest)"]) in rows


# The images: notes in the page (commit 6 of the series; the user's decisions 26-27.11.2026)

def test_image_gaps(info):
    avatars = {url: "data:x" for url in yt2html.avatar_urls(info)}
    extra = {url: "data:x" for url in yt2html.extra_image_urls(info)}
    full = dict(thumbnail="data:t", cover="data:c", avatar_uris=avatars, extra_images=extra,
                no_avatars=False)
    assert yt2html.image_gaps(info, **full) == {}
    today = dt.date.today().isoformat()
    some = sorted(yt2html.comment_avatar_urls(info))
    gaps = yt2html.image_gaps(info, None, None, {u: "d" for u in some[1:]}, {}, False)
    assert gaps == {
        "thumbnail": {"reason": "thumbnail could not be downloaded", "date": today, "again": True},
        "cover": {"reason": "cover could not be downloaded", "date": today, "again": True},
        "channel avatar": {"reason": "channel avatar could not be downloaded", "date": today,
                           "again": True},
        "avatars": {"reason": f"1 of {len(some)} comment avatars could not be downloaded",
                    "date": today, "again": True},
        **({"images": {"reason": f"{len(extra)} of {len(extra)} other images could not be "
                                 "downloaded", "date": today, "again": True}} if extra else {})}
    # the same reason keeps its first day; another reason is of today
    before = {"thumbnail": {"reason": "thumbnail could not be downloaded", "date": "2026-11-01"},
              "avatars": {"reason": "5 of 9 comment avatars could not be downloaded",
                          "date": "2026-11-01"}}
    gaps = yt2html.image_gaps(info, None, None, {u: "d" for u in some[1:]}, {}, False, before)
    assert gaps["thumbnail"]["date"] == "2026-11-01" and gaps["avatars"]["date"] == today
    # the comment avatars left out are not missing
    assert "avatars" not in yt2html.image_gaps(info, None, None, {}, {}, True)


def test_the_page_keeps_its_image_notes(tmp_path, info):
    page = tmp_path / "p.html"
    page.write_text(yt2html.render_page(info, "data:t", {}, dt.date(2026, 11, 27),
                                        cover="data:c"), encoding="utf-8")
    notes = yt2html.page_image_notes(page)
    expected = ({"channel avatar"} if yt2html.channel_avatar_url(info) else set()) | {"avatars"} | (
        {"images"} if yt2html.extra_image_urls(info) else set())
    assert set(notes) == expected and notes["avatars"]["again"] is True
    avatars = {url: "data:x" for url in yt2html.avatar_urls(info)}
    extra = {url: "data:x" for url in yt2html.extra_image_urls(info)}
    page.write_text(yt2html.render_page(info, "data:t", avatars, dt.date(2026, 11, 27),
                                        cover="data:c", extra_images=extra), encoding="utf-8")
    assert yt2html.page_image_notes(page) == {}   # none missing: there all the same
    assert yt2html.read_page_metadata(page)["yt2html:not-saved"] == "{}"
    page.write_text("<title>x</title>", encoding="utf-8")   # a page made before
    assert yt2html.page_image_notes(page) is None and yt2html.page_image_notes(None) is None
    page.write_text('<meta name="yt2html:not-saved" content="[1]"><title>x</title>',
                    encoding="utf-8")
    assert yt2html.page_image_notes(page) is None
    page.write_text('<meta name="yt2html:not-saved" content="{"><title>x</title>',
                    encoding="utf-8")
    assert yt2html.page_image_notes(page) is None


def test_the_images_in_contents(capsys, tmp_path, saved):
    html = tmp_path / f"Title [{ID}].html"
    html.write_text('<meta name="yt2html:not-saved" content="{}"><title>x</title>',
                    encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert "              images              saved\n" in out
    notes = {"thumbnail": {"reason": "thumbnail could not be downloaded", "date": "2026-11-27",
                           "again": True},
             "avatars": {"reason": "3 of 120 comment avatars could not be downloaded",
                         "date": "2026-11-20", "again": True}}
    html.write_text('<meta name="yt2html:not-saved" content="'
                    + yt2html.esc(json.dumps(notes)) + '"><title>x</title>', encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert ("              images              partly saved: thumbnail could not be downloaded\n"
            "                                  3 of 120 comment avatars could not be downloaded\n"
            "                                  (since 20.11.2026; each page build tries again)\n"
            ) in out
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show:brief")
    assert "partly saved: images" in out
    # a page made before the notes: unknown, not missing
    html.write_text("<title>x</title>", encoding="utf-8")
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show:brief")
    assert "images" not in out
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show")
    assert ("              images              unknown: the page was made before this version\n"
            "                                  (--json load makes it again and notes them)\n") in out
    html.unlink()   # no page: no line
    code, out = run(capsys, ID, "-o", str(tmp_path), "--json", "show:full")
    assert "images" not in out.split("  Next to it:")[0]
    assert yt2html.contents_images({}, {}, {"avatars": {"reason": "r"}}) == [
        ("images", "partly saved", ["r", "(each page build tries again)"])]


def test_all_images_missing_fit_the_width(info):
    notes = {key: {"reason": f"120 of 120 {key}{yt2html.IMAGES_MISSING}", "date": "2026-11-27"}
             for key in yt2html.IMAGE_PARTS}
    for line in yt2html.contents_lines(yt2html.contents_images(info, {}, notes)):
        assert yt2html.SHOW_INDENT + len(line) <= yt2html.SHOW_WIDTH, line
