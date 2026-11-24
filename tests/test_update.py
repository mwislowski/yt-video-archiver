"""--json write: what the options ask for and the saved data lack, added to them."""

import datetime as dt
import json
import re

import pytest

import yt2html
from conftest import caption_formats

URL = "https://www.youtube.com/watch?v=TESTVIDEO01"
TODAY = dt.date.today().strftime(yt2html.DATE_FORMAT)


def run(capsys, *argv):
    code = yt2html.main(list(argv))
    return code, capsys.readouterr().out


def page(folder):
    return next(folder.glob("*[[]TESTVIDEO01[]].html"))


def row(html, name):
    return re.search(rf"<dt>{name}</dt><dd>(.*?)</dd>", html, re.S).group(1)


def test_a_refused_translation_is_added(capsys, saved, youtube):
    before = json.loads(saved.read_text("utf-8"))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "bilingual:pl,bilingual:de,bilingual:fr,bilingual:it")
    assert code == 0 and youtube["extracted"] == 1
    assert "Fetching the video's data (no comments) for what is missing..." in out
    # German: YouTube did not offer it, so it is not asked for again
    assert ("No translation into 'de' in the saved data (YouTube offers no translation of this "
            "transcript into 'de'); not asked for again.") in out
    asked = [url for url in youtube["ydl"].urls if "tlang" in url]
    assert len(asked) == 1 and "tlang=pl" in asked[0]      # Italian: not in YouTube's list
    assert f"Updated metadata: {saved}" in out
    data = json.loads(saved.read_text("utf-8"))
    transcript = data[yt2html.TRANSCRIPT_KEY]
    assert list(transcript["translations"]) == ["fr", "pl"]
    assert list(transcript["missing"]) == ["de", "it"]
    assert transcript["missing"]["it"]["reason"] == ("YouTube offers no translation of this "
                                                     "transcript into 'it' (available: fr, pl)")
    record = dict(data[yt2html.UPDATES_KEY][0])
    assert re.fullmatch(r"\d\d:\d\d", record.pop("time"))
    assert record == {"date": dt.date.today().isoformat(), "yt2html": yt2html.__version__,
                      "added": ["translation pl"]}
    # the rest stays as saved: comments, counts, the copy's date
    for key in ("comments", "view_count", "like_count", yt2html.ARCHIVED_KEY, "formats"):
        assert data.get(key) == before.get(key)
    html = page(saved.parent).read_text("utf-8")
    time = data[yt2html.UPDATES_KEY][0]["time"]
    assert row(html, "Downloaded").endswith(f"; updated {TODAY} {time}")
    assert "Snapshot 05.11.2026" in html and 'class="transcript bilingual"' in html
    assert row(html, "Page built").endswith(f"from the saved data, with yt2html {yt2html.__version__}")
    # the same again: all there now, YouTube is not asked
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "bilingual:pl,bilingual:fr")
    assert code == 0 and youtube["extracted"] == 1
    assert ("Nothing missing in the saved data; the page stays as it is: " in out
            and "(--json load makes it again from the data)" in out)


def test_refused_again(capsys, saved, youtube):
    youtube["fail_translations"] = 1
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "bilingual:pl")
    assert code == 0                    # decision C: the page counts as finished
    assert "to add the translation later: --json write (keeps the comments)" in out
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.TRANSCRIPT_KEY]["missing"]["pl"]["date"] == dt.date.today().isoformat()
    assert yt2html.UPDATES_KEY not in data                      # nothing added
    html = page(saved.parent).read_text("utf-8")
    assert (f"not available: bilingual:pl (YouTube refused to send it (HTTP 429 Too Many "
            f"Requests), {TODAY})") in html
    assert "; updated" not in row(html, "Downloaded")


def test_nothing_missing_asks_youtube_nothing(capsys, saved, youtube):
    # data without a page: the page from the data, YouTube not asked
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "timestamps")
    assert code == 0 and youtube["extracted"] == 0, out
    assert page(saved.parent).exists() and "Saved page: " in out
    assert yt2html.UPDATES_KEY not in json.loads(saved.read_text("utf-8"))
    # data and page: nothing missing, both stay as they are
    data, html = saved.stat().st_mtime_ns, page(saved.parent).stat().st_mtime_ns
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "plain,timestamps", "--page", "theme:dark")
    assert code == 0 and youtube["extracted"] == 0
    assert "Nothing missing in the saved data; the page stays as it is: " in out
    assert "Saved page" not in out and "Downloading thumbnail" not in out
    assert (saved.stat().st_mtime_ns, page(saved.parent).stat().st_mtime_ns) == (data, html)


def test_the_whole_transcript_when_the_data_have_none(capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    del data[yt2html.TRANSCRIPT_KEY]
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, _ = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                  "--transcript", "plain,bilingual:pl")
    data = json.loads(saved.read_text("utf-8"))
    assert code == 0 and data[yt2html.TRANSCRIPT_KEY]["cues"]
    assert data[yt2html.UPDATES_KEY][0]["added"] == ["transcript", "translation pl"]
    # no captions then: noted, and not asked for again
    data[yt2html.TRANSCRIPT_KEY] = None
    data[yt2html.TRANSCRIPT_MISSING_KEY] = "the video has no captions"
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript")
    assert code == 0 and youtube["extracted"] == 1
    assert "No transcript in the saved data (the video has no captions); not asked for again." in out


def test_a_video_without_captions_now(capsys, saved, youtube, monkeypatch):
    data = json.loads(saved.read_text("utf-8"))
    del data[yt2html.TRANSCRIPT_KEY]
    saved.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(yt2html, "choose_caption_track", lambda info: None)
    assert run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
               "--transcript")[0] == 0
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.TRANSCRIPT_KEY] is None and yt2html.UPDATES_KEY not in data
    assert data[yt2html.TRANSCRIPT_MISSING_KEY] == "the video has no captions"


def test_the_transcripts_track_gone(capsys, saved, youtube, monkeypatch):
    monkeypatch.setattr(yt2html, "choose_caption_track",
                        lambda info: ("manual", "en", caption_formats("English")))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript", "bilingual:pl,bilingual:it")
    assert code == 0
    reason = "the transcript's captions (English) are not in YouTube's data now"
    assert f"No translation into pl, it: {reason}." in out
    missing = json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]["missing"]
    assert missing["it"] == {"reason": reason, "refused": True, "date": dt.date.today().isoformat()}


def test_the_page_shows_fewer_comments_the_data_keep_all(capsys, saved, youtube):
    code, _ = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                  "--transcript", "bilingual:pl", "--page", "comments:2")
    assert code == 0
    assert len(json.loads(saved.read_text("utf-8"))["comments"]) == 7
    html = page(saved.parent).read_text("utf-8")
    assert html.count('class="comment ') + html.count('class="comment"') <= 2
    assert "--page comments:2" in row(html, "Command")


def test_interrupted_comments_in_the_data_do_not_stop_the_run(capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    data["comment_count"] = None      # how yt-dlp leaves data after Ctrl+C
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                    "--transcript")
    assert code == 0 and "Ctrl+C" not in out      # the saved comments are what they are


def test_a_data_file_is_updated_under_its_name(capsys, saved, youtube, tmp_path):
    renamed = saved.with_name("Old name [TESTVIDEO01].info.json")
    saved.rename(renamed)
    code, out = run(capsys, str(saved.parent / "*.info.json"), "-o", str(saved.parent),
                    "--json", "write", "--transcript", "bilingual:pl")
    assert code == 0 and f"Updated metadata: {renamed}" in out
    assert "translation" in json.loads(renamed.read_text("utf-8"))[yt2html.UPDATES_KEY][0]["added"][0]
    assert page(saved.parent).exists() and list(saved.parent.glob("*.info.json")) == [renamed]
    # a name that leads nowhere fails that source
    code, out = run(capsys, str(saved.parent / "none.info.json"), "-o", str(saved.parent),
                    "--json", "write")
    assert code == 1 and "no such file: " in out
    code, out = run(capsys, str(saved.parent / "*.nothing.json"), "-o", str(saved.parent),
                    "--json", "write")
    assert code == 1 and "no file matches " in out


def test_files_outside_the_output_folder_are_an_error(capsys, saved, tmp_path):
    """Data, page and files stay in one folder: a path elsewhere stops the run
    before anything is read or written; a bare name is taken in the folder."""
    elsewhere = tmp_path / "data"
    elsewhere.mkdir()
    for argv in ([str(elsewhere / "x [TESTVIDEO01].info.json"), "--json", "write"],
                 [str(elsewhere / "*.info.json"), "--json", "load"],
                 [str(elsewhere / "Title [TESTVIDEO01].html")],
                 [str(elsewhere / "Title [TESTVIDEO01].pl.own.srt"), "--json", "merge"]):
        with pytest.raises(SystemExit) as stop:
            yt2html.parse_args([*argv, "-o", str(saved.parent)])
        assert stop.value.code == 2
        assert (f"is not in the output folder ({saved.parent}); the pages, their data and "
                f'files stay in one folder: add -o "{elsewhere}" or give the name only'
                in capsys.readouterr().err)
    args = yt2html.parse_args([saved.name, "-o", str(saved.parent), "--json", "load"])
    assert args.sources == [str(saved)]
    # the same folder written another way is the same folder
    args = yt2html.parse_args([str(saved.parent / "." / saved.name), "-o",
                               str(saved.parent / "sub" / ".."), "--json", "load"])
    assert args.sources == [str(saved.parent / "." / saved.name)]


def test_without_saved_data(capsys, tmp_path, youtube, info, monkeypatch):
    out_dir = tmp_path / "out"
    # no data, no page: everything downloaded and saved, as with --json write
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    code, out = run(capsys, URL, "-o", str(out_dir), "--json", "write")
    assert code == 0, out
    assert f"No saved data for {URL}: downloading everything." in out
    data = page(out_dir).with_suffix(".info.json")
    assert data.exists()
    # a page without data: skipped, nothing replaced (no --overwrite hidden in it)
    data.unlink()
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: pytest.fail("no download"))
    code, out = run(capsys, URL, "-o", str(out_dir), "--json", "write")
    assert code == 0
    assert ("Skipped (page exists, no saved data to add to): " in out
            and "Use --page ovr --json write to download everything again with the data."
            in out)
    # also when only the fetched data show the id (an address without it)
    monkeypatch.setattr(yt2html, "video_id_from_url", lambda url: None)
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    before = page(out_dir).stat().st_mtime_ns
    code, out = run(capsys, "https://example.com/v", "-o", str(out_dir), "--json", "write")
    assert code == 0 and "Skipped (page exists): " in out
    assert page(out_dir).stat().st_mtime_ns == before


def test_update_with_a_list_file(capsys, saved, youtube, tmp_path):
    # the saved data are found by the video's id in the output folder
    (tmp_path / "list.txt").write_text("TESTVIDEO01\n", encoding="utf-8")
    code, out = run(capsys, "--index", str(tmp_path / "list.txt"), "-o", str(saved.parent),
                    "--json", "write", "--transcript", "bilingual:pl")
    assert code == 0 and f"Reading {saved}" in out
    assert yt2html.UPDATES_KEY in json.loads(saved.read_text("utf-8"))
    assert (saved.parent / "list.html").is_file()


def test_a_translation_that_comes_leaves_no_note(fake_ydl):
    transcript = {"language": "en", "name": "English",
                  "missing": {"pl": {"reason": "r", "refused": True, "date": "2026-11-05"}}}
    yt2html.add_translations(fake_ydl(), {}, transcript, caption_formats("x")[0]["url"], ["pl"])
    assert "missing" not in transcript and list(transcript["translations"]) == ["pl"]


def test_update_helpers():
    today = dt.date.today().isoformat()
    assert yt2html.video_added({"video": {"date": "2000-01-01"}}, {}) == []
    # downloaded earlier today, as the saved data say: not added again
    there = {"file": "v.mp4", "date": today}
    assert yt2html.video_added({"video": there}, {"video": there}) == []
    assert yt2html.video_added({
        "video": {"date": today},
        "extra_audio": [{"audio": "pl", "role": "dubbed", "date": today},
                        {"audio": "de", "role": None, "date": today},
                        {"audio": "fr", "role": None}],
        "subtitles": [{"code": "en", "kind": "author", "date": today},
                      {"code": "pl", "kind": "translated"}]}, {}) == [
        "video", "extra audio pl (dubbed)", "extra audio de", "subtitles en (author)"]
    info, record = {}, {}
    yt2html.note_update(info, record, [])
    assert info == {} and record == {}                         # nothing happened: no entry
    yt2html.note_update(info, record, ["translation pl"])
    yt2html.note_update(info, record, ["translation pl", "video"])   # one run: one entry
    assert info[yt2html.UPDATES_KEY] == [record]
    assert record["added"] == ["translation pl", "video"] and record["date"] == today
    yt2html.note_update(info, {}, [], refreshed=True)          # another run, the same day
    assert [("refreshed" in u) for u in info[yt2html.UPDATES_KEY]] == [False, True]
    assert yt2html.missing_text({"reason": "r", "refused": False, "date": "2026-11-20"}) == "r"
    assert yt2html.missing_text({"reason": "r", "refused": True, "date": "2026-11-20"}) == "r, 20.11.2026"


# --json refresh ----------------------------------------------------------------------

DAY = "2026-11-20"


def comment(cid, text="t", parent="root", **extra):
    return {"id": cid, "parent": parent, "text": text, **extra}


def test_merge_comments():
    saved = [comment("P"), comment("A"), comment("Ar", parent="A"), comment("B", "old text"),
             comment("C", **{yt2html.MISSING_SINCE_KEY: "2026-11-10"}), comment("D")]
    fresh = [comment("N"), comment("P"), comment("B", "new text"), comment("C"), comment("D")]
    merged, stats = yt2html.merge_comments(saved, fresh, True, DAY)
    assert stats == {"new": 1, "gone": 2, "back": 1}
    # the gone ones stay after their neighbours (A and its reply after P)
    assert [c["id"] for c in merged] == ["N", "P", "A", "Ar", "B", "C", "D"]
    by_id = {c["id"]: c for c in merged}
    assert by_id["A"][yt2html.MISSING_SINCE_KEY] == DAY == by_id["Ar"][yt2html.MISSING_SINCE_KEY]
    assert by_id["B"]["text"] == "new text"
    assert by_id["B"][yt2html.EDITS_KEY] == [{"text": "old text", "until": DAY}]
    assert yt2html.MISSING_SINCE_KEY not in by_id["C"]                    # back on YouTube
    assert by_id["C"][yt2html.MISSING_HISTORY_KEY] == [{"from": "2026-11-10", "until": DAY}]
    assert yt2html.EDITS_KEY not in by_id["D"] and yt2html.MISSING_HISTORY_KEY not in by_id["D"]
    # gone at a later refresh again: the first day stays, the history too
    again, stats = yt2html.merge_comments(merged, [comment("N")], True, "2026-12-03")
    by_id = {c["id"]: c for c in again}
    assert by_id["A"][yt2html.MISSING_SINCE_KEY] == DAY and stats["gone"] == 4   # P, B, C, D
    assert by_id["N"][yt2html.FIRST_SEEN_KEY] == DAY           # the day it first came stays
    assert by_id["C"][yt2html.MISSING_HISTORY_KEY] == [{"from": "2026-11-10", "until": DAY}]
    # gone before, still gone: nothing new; edits and history carried over
    once_more, stats = yt2html.merge_comments(
        again, [comment("N"), comment("B", "third"), comment("C")], True, "2026-12-10")
    assert stats == {"new": 0, "gone": 0, "back": 2}          # B and C came back
    c = next(c for c in once_more if c["id"] == "C")
    assert c[yt2html.MISSING_HISTORY_KEY] == [{"from": "2026-11-10", "until": DAY},
                                              {"from": "2026-12-03", "until": "2026-12-10"}]
    b = next(c for c in once_more if c["id"] == "B")
    assert [e["text"] for e in b[yt2html.EDITS_KEY]] == ["old text", "new text"]
    assert b[yt2html.MISSING_HISTORY_KEY] == [{"from": "2026-12-03", "until": "2026-12-10"}]
    # an incomplete download (a limit, Ctrl+C) marks nothing
    merged, stats = yt2html.merge_comments(saved, fresh, False, DAY)
    assert stats["gone"] == 0 and not any(c.get(yt2html.MISSING_SINCE_KEY) == DAY for c in merged)


def test_refresh_takes_zero_likes(info):
    # zero likes come from the watch page (like_count()); 0 is a count, not "none"
    info["like_count"] = 5
    yt2html.refresh_saved(info, {"like_count": 0})
    assert info["like_count"] == 0
    yt2html.refresh_saved(info, {"like_count": None})   # hidden now, or not read: kept
    assert info["like_count"] == 0


def test_refresh_saved_counts_and_comments(info):
    fresh = {"view_count": 99, "like_count": None, yt2html.YT_COUNT_KEY: "12",
             "channel_follower_count": 5, "comments": None}
    likes = info["like_count"]
    assert yt2html.refresh_saved(info, fresh) is None           # comments off now: kept
    assert (info["view_count"], info["like_count"], info[yt2html.YT_COUNT_KEY],
            info["channel_follower_count"]) == (99, likes, "12", 5)
    assert len(info["comments"]) == 7
    fresh = {"comments": [comment("NEW")], "comment_count": 1, yt2html.LIMIT_KEY: 1,
             yt2html.YT_COUNT_KEY: "12"}
    stats = yt2html.refresh_saved(info, fresh)
    assert stats == {"new": 1, "gone": 0, "back": 0}             # limited: nothing gone
    assert info["comment_count"] == 8 and info[yt2html.LIMIT_KEY] == 1
    fresh = {"comments": [comment("NEW")], "comment_count": None}  # Ctrl+C
    yt2html.refresh_saved(info, fresh)
    assert info["comment_count"] is None
    # saved without comments (--no-comments then): all fresh ones are new
    bare = {"comments": None}
    assert yt2html.refresh_saved(bare, {"comments": [comment("X")], "comment_count": 1}) == {
        "new": 1, "gone": 0, "back": 0}


def test_a_refresh_without_a_limit_completes_limited_comments(info):
    """What the hint after a limited copy promises: --json refresh without
    --max-comments (fetch_info leaves LIMIT_KEY None) adds the rest."""
    info["comments"] = info["comments"][:3]
    info[yt2html.LIMIT_KEY] = 3
    info["comment_count"] = 3
    assert yt2html.comment_status(info) == "limited"
    fresh = json.loads(json.dumps(info))
    fresh["comments"] = fresh["comments"] + [comment("NEW1"), comment("NEW2")]
    fresh["comment_count"] = 5
    fresh[yt2html.LIMIT_KEY] = None
    assert yt2html.refresh_saved(info, fresh) == {"new": 2, "gone": 0, "back": 0}
    assert len(info["comments"]) == 5 and yt2html.comment_status(info) == "complete"


def test_refresh(capsys, saved, youtube, refreshed):
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh",
                    "--transcript", "bilingual:pl")
    assert code == 0, out
    assert "Fetching TESTVIDEO01 (to refresh the saved data)" in out
    # the transcript as saved; the translation from the same data (no second fetch)
    assert refreshed == [(URL, [])] and youtube["extracted"] == 0
    data = json.loads(saved.read_text("utf-8"))
    assert "pl" in data[yt2html.TRANSCRIPT_KEY]["translations"]
    assert data["view_count"] == 30_000_000 and data[yt2html.ARCHIVED_KEY] == "2026-11-05"
    by_id = {c["id"]: c for c in data["comments"]}
    assert len(by_id) == 8 and by_id["N1"]["text"] == "Edited"
    assert by_id["N2r2a"][yt2html.MISSING_SINCE_KEY] == dt.date.today().isoformat()
    assert by_id["NEW"][yt2html.FIRST_SEEN_KEY] == dt.date.today().isoformat()
    assert yt2html.FIRST_SEEN_KEY not in by_id["N1"]          # there from the first download
    record, = data[yt2html.UPDATES_KEY]
    time = record["time"]
    assert record == {"date": dt.date.today().isoformat(), "time": time,
                      "yt2html": yt2html.__version__,
                      "added": ["translation pl"], "refreshed": True,
                      "comments": {"new": 1, "gone": 1, "back": 0}}
    html = page(saved.parent).read_text("utf-8")
    assert f'<span class="gone">not on YouTube on {TODAY}</span></div>' in html
    assert html.count('class="gone"') == 1
    note = re.search(r'<div class="note">(8 downloaded[^<]*)</div>', html).group(1)
    assert note == (f"8 downloaded (YouTube shows 10): 5 threads, 3 replies · 1 no longer on "
                    f"YouTube · oldest first · comment dates are relative to {TODAY}")
    assert row(html, "Comments").endswith("; complete; 1 no longer on YouTube")
    assert row(html, "Downloaded").endswith(f"; refreshed {TODAY} {time}")
    assert ('<div class="value">05.11.2026</div><div class="label">Snapshot</div></div>'
            f'<div class="fact extra"><div class="value">{TODAY}</div>'
            '<div class="label">Refreshed</div>') in html
    assert f'<div class="value">{yt2html.format_int(30_000_000)}</div>' in html
    # --no-snapshot: neither tile
    run(capsys, "--json", "load", "TESTVIDEO01", "-o", str(saved.parent), "--page", "snap:no")
    html = page(saved.parent).read_text("utf-8")
    assert '"label">Snapshot<' not in html and '"label">Refreshed<' not in html
    # refreshed the day of the download: no second tile with the same date
    data[yt2html.ARCHIVED_KEY] = dt.date.today().isoformat()
    saved.write_text(json.dumps(data), encoding="utf-8")
    run(capsys, "--json", "load", "TESTVIDEO01", "-o", str(saved.parent))
    html = page(saved.parent).read_text("utf-8")
    assert '"label">Snapshot<' in html and '"label">Refreshed<' not in html


def test_refresh_without_comments_now(capsys, saved, youtube, refreshed, monkeypatch, info):
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda s, a: {**json.loads(json.dumps(info)), "comments": None,
                                      "view_count": 5})
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    data = json.loads(saved.read_text("utf-8"))
    assert code == 0 and "Nothing to add" not in out
    assert len(data["comments"]) == 7 and data["view_count"] == 5
    record = data[yt2html.UPDATES_KEY][-1]
    assert record["added"] == [] and record["refreshed"] and "comments" not in record
    html = page(saved.parent).read_text("utf-8")
    # the comment times still count from the download day
    assert "comment dates are relative to 05.11.2026" in html


def test_refresh_interrupted_stops_the_run(capsys, saved, youtube, monkeypatch, info):
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda s, a: {**json.loads(json.dumps(info)), "comment_count": None})
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    assert code == 130 and "Comments were interrupted (Ctrl+C)" in out
    assert page(saved.parent).exists()


def test_refresh_without_saved_data(capsys, tmp_path, youtube, info, monkeypatch):
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    code, out = run(capsys, URL, "-o", str(tmp_path), "--json", "refresh")
    assert code == 0 and "downloading everything." in out
    assert page(tmp_path).with_suffix(".info.json").exists()


def test_downloaded_names_the_runs(info):
    info[yt2html.UPDATES_KEY] = [
        {"date": "2026-11-20", "time": "10:12", "added": ["translation pl"]},
        {"date": "2026-12-03", "time": "18:40", "added": [], "refreshed": True,
         "comments": {"new": 2, "gone": 1, "back": 0}}]
    assert yt2html.about_downloaded(info, dt.date(2026, 11, 5)).endswith(
        "; updated 20.11.2026 10:12; refreshed 03.12.2026 18:40")


def test_comments_turned_off_after_the_download(capsys, saved, youtube, monkeypatch, info):
    def turned_off(source, args):
        return {**json.loads(json.dumps(info)), "comments": None,
                yt2html.COMMENTS_MESSAGE_KEY: "Comments are turned off."}
    monkeypatch.setattr(yt2html, "fetch_info", turned_off)
    assert run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")[0] == 0
    data = json.loads(saved.read_text("utf-8"))
    today = dt.date.today().isoformat()
    assert len(data["comments"]) == 7          # kept, none marked as gone
    assert data[yt2html.COMMENTS_CLOSED_KEY] == [{"from": today, "turned_off": True}]
    assert not any(c.get(yt2html.MISSING_SINCE_KEY) for c in data["comments"])
    html = page(saved.parent).read_text("utf-8")
    assert (f'<div class="note closed">As of {TODAY}, comments on this video are turned off on '
            "YouTube; the ones below can no longer be seen there.</div>") in html
    assert row(html, "Comments").endswith(f"; turned off on YouTube as of {TODAY}")
    # still off at the next refresh: the first day stays
    data[yt2html.COMMENTS_CLOSED_KEY][0]["from"] = "2026-11-20"
    saved.write_text(json.dumps(data), encoding="utf-8")
    run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.COMMENTS_CLOSED_KEY] == [{"from": "2026-11-20", "turned_off": True}]
    # on again: the note goes, the data keep when they were off
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.COMMENTS_CLOSED_KEY] == [
        {"from": "2026-11-20", "turned_off": True, "until": today}]
    html = page(saved.parent).read_text("utf-8")
    assert "can no longer be seen there" not in html and "as of" not in row(html, "Comments")
    # off once more: a new entry
    monkeypatch.setattr(yt2html, "fetch_info", turned_off)
    run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    closed = json.loads(saved.read_text("utf-8"))[yt2html.COMMENTS_CLOSED_KEY]
    assert closed[-1] == {"from": today, "turned_off": True} and len(closed) == 2


def test_comments_no_longer_shown_and_nothing_to_close(info):
    fresh = {"comments": None}
    yt2html.refresh_saved(info, fresh)
    assert info[yt2html.COMMENTS_CLOSED_KEY][0]["turned_off"] is False
    note = yt2html.closed_note(info[yt2html.COMMENTS_CLOSED_KEY][0])
    assert note.endswith("YouTube no longer shows comments on this video; the ones below can "
                         "no longer be seen there.") and note.startswith("As of ")
    assert "; no longer shown on YouTube as of " in yt2html.about_comments(info)
    # saved without comments: nothing to note
    bare = {"comments": None}
    yt2html.refresh_saved(bare, fresh)
    assert yt2html.COMMENTS_CLOSED_KEY not in bare
    # --no-comments now (no list at all): nothing either
    other = {"comments": [comment("A")]}
    yt2html.refresh_saved(other, {})
    assert yt2html.COMMENTS_CLOSED_KEY not in other


def test_refresh_title_description_and_markers(info):
    old_title, old_description = info["title"], info["description"]
    fresh = {"title": "New title", "description": old_description, "comments": None,
             yt2html.MARKERS_KEY: []}
    yt2html.refresh_saved(info, fresh)
    assert info["title"] == "New title" and info[yt2html.MARKERS_KEY] == []
    assert info[yt2html.PREVIOUS_KEY] == [{"until": dt.date.today().isoformat(), "title": old_title}]
    yt2html.refresh_saved(info, {"description": "New text", yt2html.MARKERS_KEY: None})
    assert info["description"] == "New text" and info[yt2html.MARKERS_KEY] == []   # not read: kept
    assert info[yt2html.PREVIOUS_KEY][-1] == {"until": dt.date.today().isoformat(),
                                              "description": old_description}
    assert info["title"] == "New title"                       # not in the fresh data: kept


def test_refresh_sources_as_for_update(capsys, saved, youtube, refreshed, tmp_path):
    code, out = run(capsys, str(saved.parent / "*.info.json"), "-o", str(saved.parent),
                    "--json", "refresh")
    assert code == 0 and f"Reading {saved}" in out
    (tmp_path / "list.txt").write_text("Title [TESTVIDEO01].html\n", encoding="utf-8")
    code, out = run(capsys, "--index", str(tmp_path / "list.txt"), "-o", str(saved.parent),
                    "--json", "refresh")
    assert code == 0 and f"Reading {saved}" in out


def test_saved_data_survive_an_interrupted_save(capsys, saved, youtube, monkeypatch):
    before = saved.read_bytes()

    def interrupted(source, target):
        raise KeyboardInterrupt
    monkeypatch.setattr(yt2html.os, "replace", interrupted)
    assert yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "bilingual:pl"]) == 130  # as after Ctrl+C
    assert saved.read_bytes() == before                       # the old data, whole


def test_refresh_interrupted_does_not_advise_overwrite(capsys, saved, youtube, monkeypatch, info):
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda s, a: {**json.loads(json.dumps(info)), "comment_count": None})
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    assert code == 130 and ("use --json refresh again for this video to get the rest "
                            "(--page ovr would replace the saved comments).") in out


def test_the_same_video_twice_in_a_list_is_updated_once(capsys, saved, youtube, tmp_path):
    listing = tmp_path / "mine.txt"
    listing.write_text(f"Title [TESTVIDEO01].html\n{URL}\n", encoding="utf-8")
    code, out = run(capsys, "--index", str(listing), "-o", str(saved.parent), "--json", "write")
    assert code == 0 and out.count(f"Reading {saved}") == 1
    index = (saved.parent / "mine.html").read_text("utf-8")
    assert index.count('<div class="row d0') == 2                 # but shown in both places


def test_a_failed_update_with_a_list_file(capsys, saved, youtube, tmp_path, monkeypatch):
    def fails(*args):
        raise yt2html.TranscriptError("could not download the transcript (offline)")
    monkeypatch.setattr(yt2html, "update_transcript", fails)
    data = json.loads(saved.read_text("utf-8"))
    del data[yt2html.TRANSCRIPT_KEY]
    saved.write_text(json.dumps(data), encoding="utf-8")
    listing = tmp_path / "mine.txt"
    listing.write_text("TESTVIDEO01\n", encoding="utf-8")
    code, out = run(capsys, "--index", str(listing), "-o", str(saved.parent), "--json", "write",
                    "--transcript")
    index = (saved.parent / "mine.html").read_text("utf-8")
    # the reason is only in the console; the index shows what is in the folder
    assert code == 1 and "could not download the transcript" in out
    assert "could not download" not in index and "not downloaded" in index


def test_the_whole_transcript_uses_the_saved_original(capsys, saved, youtube, monkeypatch):
    # the fresh data (yt-dlp alone) know nothing of the watch page: the saved
    # data's default captions and original audio decide the language
    data = json.loads(saved.read_text("utf-8"))
    del data[yt2html.TRANSCRIPT_KEY]
    data[yt2html.ORIGINAL_AUDIO_KEY] = "pl"
    saved.write_text(json.dumps(data), encoding="utf-8")
    seen = []

    def choose(info):
        seen.append((yt2html.transcript_language(info), info.get(yt2html.CAPTION_KEY)))
        return "auto", "en", caption_formats("English")
    monkeypatch.setattr(yt2html, "choose_caption_track", choose)
    assert run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
               "--transcript")[0] == 0
    assert seen[0] == ("pl", {"language": "en", "kind": "asr"})
