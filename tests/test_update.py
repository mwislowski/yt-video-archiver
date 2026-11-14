"""--update-json: what the options ask for and the saved data lack, added to them."""

import datetime as dt
import json
import re

import pytest

import yt2html
from conftest import FakeHTTPError, FakeYDL, caption_formats

URL = "https://www.youtube.com/watch?v=TESTVIDEO01"
TODAY = dt.date.today().strftime(yt2html.DATE_FORMAT)


def run(capsys, *argv):
    code = yt2html.main(list(argv))
    return code, capsys.readouterr().out


def page(folder):
    return next(folder.glob("*[[]TESTVIDEO01[]].html"))


def row(html, name):
    return re.search(rf"<dt>{name}</dt><dd>(.*?)</dd>", html, re.S).group(1)


@pytest.fixture
def saved(tmp_path, info):
    """Saved data in the output folder (as --write-json leaves them), with a
    refused Polish translation and German that YouTube does not offer."""
    info[yt2html.CAPTION_KEY] = {"language": "en", "kind": "asr"}
    info[yt2html.TRANSLATIONS_KEY] = {"pl": "Polish", "fr": "French"}
    transcript = info[yt2html.TRANSCRIPT_KEY]
    french = {"language": "fr", "name": "French", "cues": transcript["cues"]}
    transcript["translations"] = {"fr": french}      # already there: kept, not asked for
    transcript["missing"] = {
        "pl": {"reason": "YouTube refused to send it (HTTP 429 Too Many Requests)",
               "refused": True, "date": "2026-11-05"},
        "de": {"reason": "YouTube offers no translation of this transcript into 'de'",
               "refused": False, "date": "2026-11-05"}}
    folder = tmp_path / "out"
    folder.mkdir()
    path = folder / "Title [TESTVIDEO01].info.json"
    path.write_text(json.dumps(info), encoding="utf-8")
    return path


@pytest.fixture
def youtube(monkeypatch):
    """Fresh player data (yt-dlp faked) and the caption requests; a full
    download (fetch_info) is not expected unless a test allows it."""
    seen = {"extracted": 0, "fail_translations": 0}

    class FakeYoutubeDL(FakeYDL):
        def __init__(self, opts):
            super().__init__(fail_translations=seen["fail_translations"])
            seen["opts"] = opts
            seen["ydl"] = self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            seen["extracted"] += 1
            assert url == URL and not download and not seen["opts"].get("getcomments")
            return {"id": "TESTVIDEO01",
                    "automatic_captions": {"en-orig": caption_formats("English (Original)")}}

        def sanitize_info(self, info):
            return info

    real = yt2html.yt_dlp.YoutubeDL      # still names the page (output_path)
    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL",
                        lambda opts: FakeYoutubeDL(opts) if "skip_download" in opts else real(opts))
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: pytest.fail("no full download"))
    return seen


def test_a_refused_translation_is_added(capsys, saved, youtube):
    before = json.loads(saved.read_text("utf-8"))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
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
    assert data[yt2html.UPDATES_KEY] == [{"date": dt.date.today().isoformat(),
                                          "yt2html": yt2html.__version__,
                                          "added": ["translation pl"]}]
    # the rest stays as saved: comments, counts, the copy's date
    for key in ("comments", "view_count", "like_count", yt2html.ARCHIVED_KEY, "formats"):
        assert data.get(key) == before.get(key)
    html = page(saved.parent).read_text("utf-8")
    assert row(html, "Downloaded").endswith(
        f"; updated {TODAY} with yt2html {yt2html.__version__}: translation pl")
    assert "Snapshot 05.11.2026" in html and 'class="transcript bilingual"' in html
    assert row(html, "Page built").endswith(f"from the saved data, with yt2html {yt2html.__version__}")
    # the same again: all there now, YouTube is not asked
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                    "--transcript", "bilingual:pl,bilingual:fr")
    assert code == 0 and youtube["extracted"] == 1 and "Nothing to add from YouTube" in out


def test_refused_again(capsys, saved, youtube):
    youtube["fail_translations"] = 1
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                    "--transcript", "bilingual:pl")
    assert code == 0                    # decision C: the page counts as finished
    assert "to add the translation later: --update-json (keeps the comments)" in out
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.TRANSCRIPT_KEY]["missing"]["pl"]["date"] == dt.date.today().isoformat()
    assert yt2html.UPDATES_KEY not in data                      # nothing added
    html = page(saved.parent).read_text("utf-8")
    assert (f"not available: bilingual:pl (YouTube refused to send it (HTTP 429 Too Many "
            f"Requests), {TODAY})") in html
    assert "; updated" not in row(html, "Downloaded")


def test_nothing_missing_asks_youtube_nothing(capsys, saved, youtube):
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                    "--transcript", "timestamps")
    assert code == 0 and youtube["extracted"] == 0, out
    assert "Nothing to add from YouTube; the page is made from the saved data." in out
    assert page(saved.parent).exists()
    assert yt2html.UPDATES_KEY not in json.loads(saved.read_text("utf-8"))


def test_the_whole_transcript_when_the_data_have_none(capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    del data[yt2html.TRANSCRIPT_KEY]
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, _ = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                  "--transcript", "plain,bilingual:pl")
    data = json.loads(saved.read_text("utf-8"))
    assert code == 0 and data[yt2html.TRANSCRIPT_KEY]["cues"]
    assert data[yt2html.UPDATES_KEY][0]["added"] == ["transcript", "translation pl"]
    # no captions then: noted, and not asked for again
    data[yt2html.TRANSCRIPT_KEY] = None
    data[yt2html.TRANSCRIPT_MISSING_KEY] = "the video has no captions"
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                    "--transcript")
    assert code == 0 and youtube["extracted"] == 1
    assert "No transcript in the saved data (the video has no captions); not asked for again." in out


def test_a_video_without_captions_now(capsys, saved, youtube, monkeypatch):
    data = json.loads(saved.read_text("utf-8"))
    del data[yt2html.TRANSCRIPT_KEY]
    saved.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(yt2html, "choose_caption_track", lambda info: None)
    assert run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
               "--transcript")[0] == 0
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.TRANSCRIPT_KEY] is None and yt2html.UPDATES_KEY not in data
    assert data[yt2html.TRANSCRIPT_MISSING_KEY] == "the video has no captions"


def test_the_transcripts_track_gone(capsys, saved, youtube, monkeypatch):
    monkeypatch.setattr(yt2html, "choose_caption_track",
                        lambda info: ("manual", "en", caption_formats("English")))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                    "--transcript", "bilingual:pl,bilingual:it")
    assert code == 0
    reason = "the transcript's captions (English) are not in YouTube's data now"
    assert f"No translation into pl, it: {reason}." in out
    missing = json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]["missing"]
    assert missing["it"] == {"reason": reason, "refused": True, "date": dt.date.today().isoformat()}


def test_the_page_shows_fewer_comments_the_data_keep_all(capsys, saved, youtube):
    code, _ = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                  "--transcript", "bilingual:pl", "--max-comments", "2")
    assert code == 0
    assert len(json.loads(saved.read_text("utf-8"))["comments"]) == 7
    html = page(saved.parent).read_text("utf-8")
    assert html.count('class="comment ') + html.count('class="comment"') <= 2
    assert "--max-comments 2" in row(html, "Command")


def test_interrupted_comments_in_the_data_do_not_stop_the_run(capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    data["comment_count"] = None      # how yt-dlp leaves data after Ctrl+C
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--update-json",
                    "--transcript")
    assert code == 0 and "Ctrl+C" not in out      # the saved comments are what they are


def test_a_file_given_elsewhere_is_updated_where_it_is(capsys, saved, youtube, tmp_path):
    elsewhere = tmp_path / "data" / "Old name [TESTVIDEO01].info.json"
    elsewhere.parent.mkdir()
    saved.rename(elsewhere)
    pages = tmp_path / "pages"
    code, out = run(capsys, str(tmp_path / "data" / "*.info.json"), "-o", str(pages),
                    "--update-json", "--transcript", "bilingual:pl")
    assert code == 0 and f"Updated metadata: {elsewhere}" in out
    assert "translation" in json.loads(elsewhere.read_text("utf-8"))[yt2html.UPDATES_KEY][0]["added"][0]
    assert page(pages).exists() and not list(pages.glob("*.info.json"))
    # a name that leads nowhere fails that source
    code, out = run(capsys, str(tmp_path / "none.info.json"), "--update-json")
    assert code == 1 and "no such file: " in out
    code, out = run(capsys, str(tmp_path / "*.nothing.json"), "--update-json")
    assert code == 1 and "no file matches " in out


def test_without_saved_data(capsys, tmp_path, youtube, info, monkeypatch):
    out_dir = tmp_path / "out"
    # no data, no page: everything downloaded and saved, as with --write-json
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    code, out = run(capsys, URL, "-o", str(out_dir), "--update-json")
    assert code == 0, out
    assert f"No saved data for {URL}: downloading everything (as with --write-json)." in out
    data = page(out_dir).with_suffix(".info.json")
    assert data.exists()
    # a page without data: skipped, nothing replaced (no --overwrite hidden in it)
    data.unlink()
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: pytest.fail("no download"))
    code, out = run(capsys, URL, "-o", str(out_dir), "--update-json")
    assert code == 0
    assert ("Skipped (page exists, no saved data to update): " in out
            and "Use --overwrite --write-json to download everything again." in out)
    # also when only the fetched data show the id (an address without it)
    monkeypatch.setattr(yt2html, "video_id_from_url", lambda url: None)
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    before = page(out_dir).stat().st_mtime_ns
    code, out = run(capsys, "https://example.com/v", "-o", str(out_dir), "--update-json")
    assert code == 0 and "Skipped (page exists): " in out
    assert page(out_dir).stat().st_mtime_ns == before


def test_a_list_file_of_saved_data(capsys, saved, youtube, tmp_path):
    (tmp_path / "list.txt").write_text(str(saved.parent / "*.info.json") + "\n", encoding="utf-8")
    code, out = run(capsys, "-a", str(tmp_path / "list.txt"), "-o", str(saved.parent),
                    "--update-json", "--transcript", "bilingual:pl")
    assert code == 0 and f"Reading {saved}" in out
    assert yt2html.UPDATES_KEY in json.loads(saved.read_text("utf-8"))


def test_a_translation_that_comes_leaves_no_note(fake_ydl):
    transcript = {"language": "en", "name": "English",
                  "missing": {"pl": {"reason": "r", "refused": True, "date": "2026-11-05"}}}
    yt2html.add_translations(fake_ydl(), {}, transcript, caption_formats("x")[0]["url"], ["pl"])
    assert "missing" not in transcript and list(transcript["translations"]) == ["pl"]


def test_update_helpers():
    assert yt2html.video_added({"video": {"date": "2000-01-01"}}) == []
    today = dt.date.today().isoformat()
    assert yt2html.video_added({
        "video": {"date": today},
        "extra_audio": [{"audio": "pl", "role": "dubbed", "date": today},
                        {"audio": "de", "role": None, "date": today},
                        {"audio": "fr", "role": None}],
        "subtitles": [{"code": "en", "kind": "author", "date": today},
                      {"code": "pl", "kind": "translated"}]}) == [
        "video", "extra audio pl (dubbed)", "extra audio de", "subtitles en (author)"]
    info = {}
    yt2html.note_update(info, [])
    assert info == {}
    yt2html.note_update(info, ["translation pl"])
    yt2html.note_update(info, ["translation pl", "video"])      # the same day: one record
    assert info[yt2html.UPDATES_KEY] == [{"date": today, "yt2html": yt2html.__version__,
                                          "added": ["translation pl", "video"]}]
    info[yt2html.UPDATES_KEY][0]["date"] = "2026-11-20"
    yt2html.note_update(info, ["translation de"])
    assert [u["date"] for u in info[yt2html.UPDATES_KEY]] == ["2026-11-20", today]
    assert yt2html.missing_text({"reason": "r", "refused": False, "date": "2026-11-20"}) == "r"
    assert yt2html.missing_text({"reason": "r", "refused": True, "date": "2026-11-20"}) == "r, 20.11.2026"


# --refresh-json ----------------------------------------------------------------------

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


@pytest.fixture
def refreshed(saved, youtube, monkeypatch, info):
    """Fresh data for --refresh-json: N2r2a deleted, a new comment, N1 edited,
    more views."""
    asked = []

    def fetch_info(source, args):
        asked.append((source, args.transcript))
        fresh = json.loads(json.dumps(info))
        fresh["comments"] = [c for c in fresh["comments"] if c["id"] != "N2r2a"]
        fresh["comments"].insert(1, {**fresh["comments"][1], "id": "NEW", "text": "Fresh one"})
        next(c for c in fresh["comments"] if c["id"] == "N1")["text"] = "Edited"
        fresh["comment_count"] = len(fresh["comments"])
        fresh["view_count"] = 30_000_000
        fresh[yt2html.YT_COUNT_KEY] = "10"
        fresh[yt2html.ARCHIVED_KEY] = dt.date.today().isoformat()
        fresh["automatic_captions"] = {"en-orig": caption_formats("English (Original)")}
        return fresh

    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)
    return asked


def test_refresh(capsys, saved, youtube, refreshed):
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--refresh-json",
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
    assert data[yt2html.UPDATES_KEY] == [{
        "date": dt.date.today().isoformat(), "yt2html": yt2html.__version__,
        "added": ["translation pl"], "refreshed": True,
        "comments": {"new": 1, "gone": 1, "back": 0}}]
    html = page(saved.parent).read_text("utf-8")
    assert f'<span class="gone">not on YouTube on {TODAY}</span></div>' in html
    assert html.count('class="gone"') == 1
    note = re.search(r'<div class="note">(8 downloaded[^<]*)</div>', html).group(1)
    assert note == (f"8 downloaded (YouTube shows 10): 5 threads, 3 replies · 1 no longer on "
                    f"YouTube · oldest first · comment dates are relative to {TODAY}")
    assert row(html, "Comments").endswith("; complete; 1 no longer on YouTube")
    assert row(html, "Downloaded").endswith(
        f"; refreshed {TODAY} with yt2html {yt2html.__version__}: comments (1 new, 1 no longer "
        "on YouTube), counts, translation pl")
    assert ('<div class="value">05.11.2026</div><div class="label">Snapshot</div></div>'
            f'<div class="fact extra"><div class="value">{TODAY}</div>'
            '<div class="label">Refreshed</div>') in html
    assert f'<div class="value">{yt2html.format_int(30_000_000)}</div>' in html
    # --no-snapshot: neither tile
    run(capsys, "--load-json", "TESTVIDEO01", "-o", str(saved.parent), "--no-snapshot")
    html = page(saved.parent).read_text("utf-8")
    assert '"label">Snapshot<' not in html and '"label">Refreshed<' not in html


def test_refresh_without_comments_now(capsys, saved, youtube, refreshed, monkeypatch, info):
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda s, a: {**json.loads(json.dumps(info)), "comments": None,
                                      "view_count": 5})
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--refresh-json")
    data = json.loads(saved.read_text("utf-8"))
    assert code == 0 and "Nothing to add" not in out
    assert len(data["comments"]) == 7 and data["view_count"] == 5
    assert data[yt2html.UPDATES_KEY][-1] == {"date": dt.date.today().isoformat(),
                                             "yt2html": yt2html.__version__, "added": [],
                                             "refreshed": True}
    html = page(saved.parent).read_text("utf-8")
    assert row(html, "Downloaded").endswith(f"with yt2html {yt2html.__version__}: counts")
    # the comment times still count from the download day
    assert "comment dates are relative to 05.11.2026" in html


def test_refresh_interrupted_stops_the_run(capsys, saved, youtube, monkeypatch, info):
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda s, a: {**json.loads(json.dumps(info)), "comment_count": None})
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--refresh-json")
    assert code == 130 and "Comments were interrupted (Ctrl+C)" in out
    assert page(saved.parent).exists()


def test_refresh_without_saved_data(capsys, tmp_path, youtube, info, monkeypatch):
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: json.loads(json.dumps(info)))
    code, out = run(capsys, URL, "-o", str(tmp_path), "--refresh-json")
    assert code == 0 and "downloading everything (as with --write-json)" in out
    assert page(tmp_path).with_suffix(".info.json").exists()


def test_comments_change_text():
    assert yt2html.comments_change({"new": 0, "gone": 0, "back": 0}) == "comments (no changes)"
    assert yt2html.comments_change({"new": 2, "gone": 0, "back": 1}) == (
        "comments (2 new, 1 back on YouTube)")


def test_refresh_sources_as_for_update(capsys, saved, youtube, refreshed, tmp_path):
    code, out = run(capsys, str(saved.parent / "*.info.json"), "-o", str(saved.parent),
                    "--refresh-json")
    assert code == 0 and f"Reading {saved}" in out
    (tmp_path / "list.txt").write_text(str(saved.parent / "*.info.json") + "\n", encoding="utf-8")
    code, out = run(capsys, "-a", str(tmp_path / "list.txt"), "-o", str(saved.parent),
                    "--refresh-json")
    assert code == 0 and f"Reading {saved}" in out
