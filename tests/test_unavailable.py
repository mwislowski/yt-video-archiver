"""A video no longer on YouTube (removed or private): refused on the first
download, recorded by --json refresh, shown on the page and in its metadata.

The refusals are the real ones from 17.11.2026 (data/gone_playability.json);
an age-restricted video without cookies, which is no refusal, is the real one
from 19.11.2026 (data/agegate_playability.json).
"""

import datetime as dt
import json
from pathlib import Path

import pytest

import yt2html
from conftest import FakeYDL, watch_page
from test_update import page, row, run

GONE = json.loads((Path(__file__).parent / "data" / "gone_playability.json")
                  .read_text(encoding="utf-8"))
AGEGATE = json.loads((Path(__file__).parent / "data" / "agegate_playability.json")
                     .read_text(encoding="utf-8"))
TERMINATED = ("Video unavailable. This video is no longer available because the YouTube "
              "account associated with this video has been terminated.")
TODAY = dt.date.today()


def refusal(video_id: str) -> yt2html.VideoUnavailable:
    return yt2html.playability_refusal(GONE[video_id])


def test_the_text_and_the_kind_of_each_real_refusal():
    # the texts yt-dlp printed for the same videos
    private, terminated, removed = (refusal(v) for v in
                                    ("U93QRMcQU5Y", "fwjg1F_zdCk", "r671hNQtok0"))
    assert (private.status, private.reason) == (
        "LOGIN_REQUIRED",
        "Private video. If the owner of this video has granted you access, please sign in.")
    assert (terminated.status, terminated.reason) == ("ERROR", TERMINATED)
    assert (removed.status, removed.reason) == (   # the description repeats the reason
        "ERROR", "This video has been removed for violating YouTube's Terms of Service")
    assert private.gone and terminated.gone and removed.gone
    assert str(terminated) == TERMINATED


def test_what_is_not_a_refusal_and_what_does_not_count_as_gone():
    assert yt2html.playability_refusal({"playabilityStatus": {"status": "OK"}}) is None
    assert yt2html.playability_refusal({}) is None
    # a status other than OK, but the video is there (e.g. sign in to confirm the age)
    assert yt2html.playability_refusal({
        "playabilityStatus": {"status": "LOGIN_REQUIRED", "reason": "Sign in to confirm your age"},
        "videoDetails": {"videoId": "TESTVIDEO01"}}) is None
    later = yt2html.playability_refusal({"playabilityStatus": {
        "status": "ERROR", "reason": "Something went wrong. Try again later."}})
    bot = yt2html.playability_refusal({"playabilityStatus": {
        "status": "LOGIN_REQUIRED", "reason": "Sign in to confirm you’re not a bot"}})
    unknown = yt2html.playability_refusal({"playabilityStatus": {"status": "UNPLAYABLE"}})
    assert later.reason == "Something went wrong. Try again later." and not later.gone
    assert bot.status == "LOGIN_REQUIRED" and not bot.gone
    assert (unknown.status, unknown.reason, unknown.gone) == ("UNPLAYABLE", "UNPLAYABLE", False)
    # "Private video" counts only with YouTube's status for it
    assert not yt2html.VideoUnavailable("UNPLAYABLE", "Private video").gone
    # without a reason: the error screen's title
    titled = yt2html.playability_refusal({"playabilityStatus": {
        "status": "ERROR", "errorScreen": {"playerInterstitialRenderer": {"content": {
            "interstitialViewModel": {"title": {"content": "Video unavailable."}}}}}}})
    assert titled.reason == "Video unavailable."


def test_an_age_restricted_video_without_cookies_is_no_refusal():
    """YouTube refuses to play it without signing in, but the video is there
    (videoDetails): the page is made, a refresh does not record it as gone."""
    status = AGEGATE["playabilityStatus"]
    assert (status["status"], status["reason"]) == ("LOGIN_REQUIRED", "Sign in to confirm your age")
    assert yt2html.playability_refusal(AGEGATE) is None
    shell = {"id": "6Ib4MxtHFFM", "description": ""}
    yt2html.add_watch_page_data(FakeYDL(webpage=watch_page({}, AGEGATE)), shell)
    assert shell[yt2html.WATCH_PAGE_KEY] == "read" and shell[yt2html.CAPTION_KEY] is None
    # without videoDetails the same status would be a refusal, one that may not last
    alone = yt2html.playability_refusal({"playabilityStatus": status})
    assert alone.status == "LOGIN_REQUIRED" and not alone.gone


def test_the_watch_page_raises_before_reading_anything_else(info):
    shell = {"id": "fwjg1F_zdCk"}
    with pytest.raises(yt2html.VideoUnavailable, match="has been terminated"):
        yt2html.add_watch_page_data(FakeYDL(webpage=watch_page({}, GONE["fwjg1F_zdCk"])), shell)
    assert yt2html.MARKERS_KEY not in shell


class ShellYDL(FakeYDL):
    """yt-dlp for a video YouTube refuses: with missing formats ignored it
    only warns and returns an empty shell; the watch page says why."""

    def __init__(self, opts):
        super().__init__(webpage=watch_page({}, GONE["fwjg1F_zdCk"]))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        return {"id": "fwjg1F_zdCk", "title": "youtube video #fwjg1F_zdCk", "comments": []}

    def sanitize_info(self, info):
        return info


def test_the_first_download_fails_and_writes_nothing(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", ShellYDL)
    code, out = run(capsys, "fwjg1F_zdCk", "-o", str(tmp_path), "--json", "write")
    assert code == 1
    assert f"FAILED: fwjg1F_zdCk: {TERMINATED}" in out
    assert not list(tmp_path.iterdir())
    # --overwrite of an existing page: the same, the page stays as it was
    (tmp_path / "Old [fwjg1F_zdCk].html").write_text("old")
    code, out = run(capsys, "fwjg1F_zdCk", "-o", str(tmp_path), "--page", "ovr")
    assert code == 1 and f"FAILED: fwjg1F_zdCk: {TERMINATED}" in out
    assert [p.name for p in tmp_path.iterdir()] == ["Old [fwjg1F_zdCk].html"]


def gone_on_refresh(monkeypatch, video_id="fwjg1F_zdCk"):
    def fetch_info(source, args):
        raise refusal(video_id)
    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)


def test_refresh_records_it_and_changes_nothing_else(capsys, saved, youtube, monkeypatch):
    before = json.loads(saved.read_text("utf-8"))
    gone_on_refresh(monkeypatch)
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh",
                    "--transcript", "dual:pl")
    assert code == 0, out
    assert f"No longer on YouTube: {TERMINATED} (recorded)" in out
    assert youtube["extracted"] == 0          # the refused translation is not asked for again
    data = json.loads(saved.read_text("utf-8"))
    assert data.pop(yt2html.UNAVAILABLE_KEY) == {"status": "ERROR", "reason": TERMINATED}
    record, = data.pop(yt2html.UPDATES_KEY)
    time = record.pop("time")
    assert record == {"date": TODAY.isoformat(), "yt2html": yt2html.__version__, "added": [],
                      "unavailable": True}
    assert data == before                     # title, comments, transcript: untouched
    html = page(saved.parent).read_text("utf-8")
    day = TODAY.strftime(yt2html.DATE_FORMAT)
    assert (f'<div class="fact extra warn"><div class="value">{day}</div>'
            '<div class="label">Refreshed</div></div>') in html
    assert row(html, "Downloaded").endswith(
        f"; no longer on YouTube {day} {time} “{yt2html.esc(TERMINATED)}”")
    assert f'<meta name="yt2html:unavailable" content="{TODAY.isoformat()}T{time}">' in html
    assert "<title>" + yt2html.esc(before["title"]) in html
    # --no-snapshot: no tiles, so nothing there
    run(capsys, "--json", "load", "TESTVIDEO01", "-o", str(saved.parent), "--page", "snap:no")
    assert '"label">Refreshed<' not in page(saved.parent).read_text("utf-8")


def test_another_refusal_on_refresh_fails_and_saves_nothing(capsys, saved, youtube,
                                                            monkeypatch):
    before = saved.read_bytes()
    monkeypatch.setattr(yt2html, "fetch_info", lambda s, a: (_ for _ in ()).throw(
        yt2html.VideoUnavailable("LOGIN_REQUIRED", "Sign in to confirm you’re not a bot")))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    assert code == 1 and "FAILED: TESTVIDEO01: Sign in to confirm you’re not a bot" in out
    assert saved.read_bytes() == before
    assert not list(saved.parent.glob("*.html"))


def test_the_video_comes_back(capsys, saved, youtube, monkeypatch, refreshed):
    data = json.loads(saved.read_text("utf-8"))
    data[yt2html.UNAVAILABLE_KEY] = {"status": "ERROR", "reason": TERMINATED}
    data[yt2html.UPDATES_KEY] = [{"date": "2026-12-03", "time": "14:05", "yt2html": "x",
                                  "added": [], "unavailable": True}]
    saved.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    assert code == 0, out
    data = json.loads(saved.read_text("utf-8"))
    assert yt2html.UNAVAILABLE_KEY not in data and data["view_count"] == 30_000_000
    html = page(saved.parent).read_text("utf-8")
    downloaded = row(html, "Downloaded")
    assert "; no longer on YouTube 03.12.2026 14:05; refreshed " in downloaded
    assert "“" not in downloaded and " warn" not in html and "yt2html:unavailable" not in html


def test_the_tile_and_the_row_of_several_checks(info):
    info[yt2html.UNAVAILABLE_KEY] = {"status": "LOGIN_REQUIRED", "reason": "Private video"}
    info[yt2html.UPDATES_KEY] = [
        {"date": "2026-11-05", "time": "18:00", "unavailable": True},
        {"date": "2026-12-03", "time": "14:05", "unavailable": True},
        {"date": "2026-12-04", "time": "09:00", "added": ["translation pl"]}]
    assert yt2html.about_downloaded(info, dt.date(2026, 11, 5)).endswith(
        "; no longer on YouTube 05.11.2026 18:00; no longer on YouTube 03.12.2026 14:05 "
        "“Private video”; updated 04.12.2026 09:00")
    # the last check, even on the copy's day; a refresh before it does not count
    info[yt2html.UPDATES_KEY].insert(0, {"date": "2026-11-20", "time": "08:00", "refreshed": True})
    info[yt2html.UPDATES_KEY][1]["date"] = "2026-11-01"
    info[yt2html.UPDATES_KEY][2]["date"] = "2026-11-05"
    assert yt2html.refreshed_tile(info, dt.date(2026, 11, 5)) == (
        '<div class="fact extra warn"><div class="value">05.11.2026</div>'
        '<div class="label">Refreshed</div></div>')
    assert yt2html.page_metadata(info, dt.date(2026, 11, 5))["yt2html:unavailable"] == \
        "2026-11-05T14:05"


def test_warning_colours_in_both_themes():
    for css in (yt2html.CSS, yt2html.LIGHT_VARIABLES):
        assert "--warn-bg:" in css and "--warn-fg:" in css
    assert ".fact.warn{background:var(--warn-bg)}" in yt2html.CSS
    assert ".fact.warn .value{color:var(--warn-fg)}" in yt2html.CSS


THUMB = "data:image/jpeg;base64,T0xE"


def test_a_refresh_keeps_the_thumbnail_youtube_no_longer_gives(capsys, saved, youtube,
                                                               monkeypatch):
    # YouTube's image server no longer gave any thumbnail of a private video
    # (17.11.2026, U93QRMcQU5Y): the page made again keeps the one it had
    gone_on_refresh(monkeypatch)
    old = saved.parent / "Renamed by me [TESTVIDEO01].html"
    old.write_text(f'<main><img class="thumbnail" src="{THUMB}" alt=""></main>', "utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    assert code == 0, out
    assert "Thumbnail could not be downloaded; kept from the existing page." in out
    assert f'<img class="thumbnail" src="{THUMB}" alt="">' in old.read_text("utf-8")
    # what YouTube gives now comes first
    monkeypatch.setattr(yt2html, "fetch_image", lambda url: "data:image/jpeg;base64,TkVX")
    run(capsys, "--json", "load", "TESTVIDEO01", "-o", str(saved.parent))
    assert 'src="data:image/jpeg;base64,TkVX"' in old.read_text("utf-8")


def test_without_a_thumbnail_anywhere_the_placeholder(capsys, saved, youtube, monkeypatch,
                                                      tmp_path):
    gone_on_refresh(monkeypatch)
    (saved.parent / "Old [TESTVIDEO01].html").write_text("<main>no image</main>", "utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(saved.parent), "--json", "refresh")
    assert code == 0 and "Thumbnail could not be downloaded." in out
    assert "Thumbnail not available" in page(saved.parent).read_text("utf-8")
    assert yt2html.images_of_page(None) == {}
    assert yt2html.images_of_page(tmp_path / "missing.html") == {}
