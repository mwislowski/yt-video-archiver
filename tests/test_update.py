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
