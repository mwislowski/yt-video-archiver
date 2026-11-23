"""--channel-list CHANNEL LIST PERIOD: an unofficial option (the user's,
22.11.2026; not in --help or the README): some videos of a channel, by their
publication dates or the newest N, written to a list file."""

import datetime as dt

import pytest

import yt2html
import yt2html_index as index

TODAY = dt.datetime.now(dt.timezone.utc).date()
CHANNEL = "https://www.youtube.com/@somebody/videos"


def ago(days: int) -> int:
    """yt-dlp's approximate timestamp of a video listed as published `days` ago."""
    moment = dt.datetime.combine(TODAY - dt.timedelta(days=days), dt.time(12),
                                 tzinfo=dt.timezone.utc)
    return int(moment.timestamp())


def entry(number: int, days: int | None, **more) -> dict:
    video_id = f"CHANNELVI{number:02}"
    return {"_type": "url", "id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": f"Video {number}", "timestamp": None if days is None else ago(days), **more}


@pytest.fixture
def channel(monkeypatch):
    """A fake channel: its list (entries, read lazily: `read` counts them) and
    each video's exact day (`days`: id -> date or an exception)."""
    fake = {"entries": [], "read": 0, "days": {}, "asked": [], "slept": []}

    def fetch_channel(url, args):
        fake["url"], fake["open"] = url, True
        try:
            yield {"channel": "Some Body", "title": "Some Body - Videos"}
            for item in fake["entries"]:
                fake["read"] += 1
                yield item
        finally:
            fake["open"] = False                     # yt-dlp's session ends

    def exact_day(video_id, args):
        assert not fake["open"], "the channel's list is still open"
        fake["asked"].append(video_id)
        found = fake["days"][video_id]
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(index, "fetch_channel", fetch_channel)
    monkeypatch.setattr(index, "exact_day", exact_day)
    monkeypatch.setattr(index.time, "sleep", lambda seconds: fake["slept"].append(seconds))
    return fake


def run(capsys, *argv):
    code = yt2html.main(["--channel-list", *argv])
    return code, capsys.readouterr().out


# What the user gives ---------------------------------------------------------------

@pytest.mark.parametrize("given, url", [
    ("https://www.youtube.com/@somebody", CHANNEL),
    ("https://www.youtube.com/@somebody/shorts", CHANNEL),          # always the Videos tab
    ("youtube.com/@somebody/videos?view=0", "https://youtube.com/@somebody/videos"),
    ("@somebody", CHANNEL),
    ("UCw3L8lysSdQpNaeGLBqnr5A", "https://www.youtube.com/channel/UCw3L8lysSdQpNaeGLBqnr5A/videos"),
    ("https://m.youtube.com/channel/UCw3L8lysSdQpNaeGLBqnr5A/featured",
     "https://m.youtube.com/channel/UCw3L8lysSdQpNaeGLBqnr5A/videos"),
    ("https://www.youtube.com/c/Name", "https://www.youtube.com/c/Name/videos"),
    ("https://www.youtube.com/user/Name/", "https://www.youtube.com/user/Name/videos"),
])
def test_channel_addresses(given, url):
    assert index.channel_videos_url(given) == url


@pytest.mark.parametrize("given, message", [
    ("https://www.youtube.com/watch?v=Z8lf5oWKfj0", "takes a channel, not a video or playlist"),
    ("PLeBgIYTbh-CwGJwFnluRTgBkvcM2q6jOO", "takes a channel, not a video or playlist"),
    ("https://vimeo.com/@x", "not a YouTube channel"),
    ("somebody", "not a YouTube channel"),
])
def test_not_channels(given, message):
    with pytest.raises(index.ListFileError, match=message):
        index.channel_videos_url(given)


@pytest.mark.parametrize("given, chosen", [
    ("last:20", 20), ("LAST:1", 1), ("last:500", 500),
    ("03.05.2019", (dt.date(2019, 5, 3), dt.date(2019, 5, 3))),
    ("3.5.19", (dt.date(2019, 5, 3), dt.date(2019, 5, 3))),
    ("02.2024", (dt.date(2024, 2, 1), dt.date(2024, 2, 29))),
    ("12.2023", (dt.date(2023, 12, 1), dt.date(2023, 12, 31))),
    ("2019", (dt.date(2019, 1, 1), dt.date(2019, 12, 31))),
    ("01.01.2023-31.03.2023", (dt.date(2023, 1, 1), dt.date(2023, 3, 31))),
    ("03.2023 – 2024", (dt.date(2023, 3, 1), dt.date(2024, 12, 31))),   # a dash, any forms
    ("2019-2019", (dt.date(2019, 1, 1), dt.date(2019, 12, 31))),
])
def test_periods(given, chosen):
    assert index.channel_choice(given, dt.date(2026, 11, 22)) == chosen


@pytest.mark.parametrize("given, message", [
    ("last:0", "last:N takes N from 1 to 500"), ("last:501", "last:N takes N from 1 to 500"),
    ("last 20", "not a period"), ("20", "not a period"), ("2027", "not a period"),
    ("31.02.2019", "not a period"), ("2019-", "not a period"), ("2019-2020-2021", "not a period"),
    ("", "not a period"), ("2020-2019", "the period ends before it starts"),
    ("02.2019-31.01.2019", "the period ends before it starts"),
])
def test_not_periods(given, message):
    with pytest.raises(index.ListFileError, match=message):
        index.channel_choice(given, dt.date(2026, 11, 22))


@pytest.mark.parametrize("extra, named", [(["URL"], "a URL"), (["-o", "out"], "-o"),
                                          (["--index", "a.txt"], "--index"),
                                          (["--write-json"], "--write-json")])
def test_it_takes_nothing_else_but_cookies(capsys, tmp_path, extra, named):
    with pytest.raises(SystemExit):
        yt2html.main(["--channel-list", "@x", str(tmp_path / "x.txt"), "2019", *extra])
    assert (f"--channel-list only writes a list of a channel's videos, so it cannot be used "
            f"with {named}") in capsys.readouterr().err
    args = yt2html.parse_args(["--channel-list", "@x", str(tmp_path / "x.txt"), "2019",
                               "--cookies", "firefox"])
    assert args.channel[0] == "https://www.youtube.com/@x/videos"
    with pytest.raises(SystemExit):
        yt2html.main(["--help"])
    assert "--channel-list" not in capsys.readouterr().out


def test_wrong_values_stop_before_any_request(capsys, tmp_path, channel):
    for argv, message in ((["watch?v", "a.txt", "2019"], "not a YouTube channel"),
                          (["@x", "a.txt", "sometime"], "not a period")):
        with pytest.raises(SystemExit):
            yt2html.main(["--channel-list", *argv])
        assert message in capsys.readouterr().err
    with pytest.raises(SystemExit):
        yt2html.main(["--channel-list", "@x"])          # three values, always
    assert "url" not in channel


# The newest N -----------------------------------------------------------------------

def test_the_newest_n(tmp_path, capsys, channel):
    channel["entries"] = [entry(1, None, live_status="is_upcoming"),   # a premiere: left out
                          entry(2, 1), {"id": "UCw3L8lysSdQpNaeGLBqnr5A",     # not a video
                           "url": "https://www.youtube.com/channel/UCw3L8lysSdQpNaeGLBqnr5A"},
                          entry(3, 40), entry(4, 400), entry(5, 800)]
    target = tmp_path / "list.txt"
    code, out = run(capsys, "@somebody", str(target), "last:2")
    assert code == 0 and channel["url"] == CHANNEL
    text = target.read_text("utf-8")
    assert text == (f"# Some Body\n// {CHANNEL}, last:2: made by yt2html --channel-list on "
                    f"{dt.date.today().strftime('%d.%m.%Y')}; titles as YouTube lists them "
                    "(may be translated), so in comments; oldest first\n"
                    f"\n// Video 3\n// ~{(TODAY - dt.timedelta(days=40)).strftime('%m.%Y')}"
                    "\nCHANNELVI03\n"
                    f"\n// Video 2\n// ~{(TODAY - dt.timedelta(days=1)).strftime('%d.%m.%Y')}\n"
                    "CHANNELVI02\n")   # the newest two, oldest first
    assert channel["read"] == 4 and channel["asked"] == []      # stops there; no dates asked
    assert f"Saved list: {target} (2 videos)" in out
    assert index.read_list_file(str(target)).title == "Some Body"   # a list file as it is
    assert [i.ref for i in index.read_list_file(str(target)).items()] == ["CHANNELVI03",
                                                                          "CHANNELVI02"]


# A period ---------------------------------------------------------------------------

def test_a_period_in_two_steps(tmp_path, capsys, channel):
    """Step 1: the approximate dates of the list choose the videos that may be
    from the period, and the reading stops when the list is surely older.
    Step 2: each of them is asked for its exact day."""
    year = TODAY.year - 3
    period = (dt.date(year, 1, 1), dt.date(year, 12, 31))
    in_days = (TODAY - dt.date(year, 6, 1)).days
    channel["entries"] = [
        entry(1, 3),                                    # new: surely after the period
        entry(2, (TODAY - dt.date(year + 1, 3, 1)).days),   # may be (a year's precision)
        entry(3, in_days), entry(4, None),             # no date: may be
        entry(5, in_days + 30), entry(6, in_days + 60),
        entry(7, (TODAY - dt.date(year - 2, 6, 1)).days),   # surely older: the reading stops
        entry(8, 10)]                                   # never read
    channel["days"] = {"CHANNELVI02": dt.date(year + 1, 1, 5), "CHANNELVI03": dt.date(year, 6, 1),
                       "CHANNELVI04": dt.date(year, 7, 7), "CHANNELVI05": None,
                       "CHANNELVI06": ValueError("ERROR: [youtube] x: Video unavailable")}
    target = tmp_path / "list.txt"
    code, out = run(capsys, "@somebody", str(target), str(year))
    assert code == 0 and channel["read"] == 7
    assert channel["asked"] == ["CHANNELVI02", "CHANNELVI03", "CHANNELVI04", "CHANNELVI05",
                                "CHANNELVI06"]
    assert len(channel["slept"]) == 4 and all(2 <= s <= 5 for s in channel["slept"])
    assert "Read 7 videos of the list; 5 videos may be from the period." in out   # 7: the last
    assert f"  3/5 CHANNELVI04: 07.07.{year}" in out
    assert "  5/5 CHANNELVI06: date not read (Video unavailable); kept in the list" in out
    text = target.read_text("utf-8").split("\n", 2)[2]
    # oldest first, by the exact day, else the list's approximate one (6 and 5);
    # 4 is listed after 3 but published later: the date decides
    assert text == ("\n// Video 6\n// date not read\nCHANNELVI06\n\n"
                    "// Video 5\n// date not read\nCHANNELVI05\n\n"
                    f"// Video 3\n= !date: 01.06.{year}\nCHANNELVI03\n\n"
                    f"// Video 4\n= !date: 07.07.{year}\nCHANNELVI04\n")   # 2: after the period
    listing = index.read_list_file(str(target))                       # a list file as it is
    assert [(i.ref, i.date) for i in listing.items()] == [
        ("CHANNELVI06", None), ("CHANNELVI05", None),
        ("CHANNELVI03", f"01.06.{year}"), ("CHANNELVI04", f"07.07.{year}")]
    assert f", {year}: made by yt2html --channel-list on " in target.read_text("utf-8")
    assert period == index.channel_choice(str(year))


def test_more_videos_to_check_than_the_limit(tmp_path, capsys, channel, monkeypatch):
    monkeypatch.setattr(index, "CANDIDATES_MAX", 3)
    channel["entries"] = [entry(n, 2) for n in range(1, 5)]
    code, out = run(capsys, "@somebody", str(tmp_path / "list.txt"), str(TODAY.year))
    assert code == 1 and channel["asked"] == []
    assert ("4 videos may be from the period." in out and "That is more than 3 to check one by "
            "one (a request each); give a shorter period. Nothing was written." in out)
    assert not (tmp_path / "list.txt").exists()


def test_reading_stops_at_the_limit(tmp_path, capsys, channel, monkeypatch):
    monkeypatch.setattr(index, "READ_MAX", 3)
    channel["entries"] = [entry(n, 1) for n in range(1, 6)]
    channel["days"] = {f"CHANNELVI0{n}": TODAY for n in range(1, 6)}
    code, out = run(capsys, "@somebody", str(tmp_path / "list.txt"), str(TODAY.year))
    assert code == 0 and channel["read"] == 3
    assert "Note: stopped after 3 videos of the list; older ones were not read." in out
    assert ("// stopped after 3 videos of the list; older ones were not read\n"
            in (tmp_path / "list.txt").read_text("utf-8"))


def test_nothing_in_the_period(tmp_path, capsys, channel):
    channel["entries"] = [entry(1, 2), entry(2, 3000)]
    code, out = run(capsys, "@somebody", str(tmp_path / "list.txt"), "2001")
    assert code == 0 and "No videos of the channel in the period; nothing was written." in out
    assert not (tmp_path / "list.txt").exists() and channel["asked"] == []


def test_ctrl_c_and_failures_write_nothing(tmp_path, capsys, channel, monkeypatch):
    channel["entries"] = [entry(1, 2)]
    channel["days"] = {"CHANNELVI01": KeyboardInterrupt()}

    def interrupted(video_id, args):
        raise KeyboardInterrupt

    monkeypatch.setattr(index, "exact_day", interrupted)
    code, out = run(capsys, "@somebody", str(tmp_path / "a.txt"), str(TODAY.year))
    assert code == 130 and "Interrupted (Ctrl+C) while checking the dates" in out

    def broken(url, args):
        yield {"channel": "x"}
        raise yt2html.yt_dlp.utils.DownloadError("no such channel")

    monkeypatch.setattr(index, "fetch_channel", broken)
    code, out = run(capsys, "@somebody", str(tmp_path / "a.txt"), "last:3")
    assert code == 1 and f"FAILED: {CHANNEL}" in out

    def stopped(url, args):
        yield {"channel": "x"}
        raise KeyboardInterrupt

    monkeypatch.setattr(index, "fetch_channel", stopped)
    code, out = run(capsys, "@somebody", str(tmp_path / "a.txt"), "last:3")
    assert code == 130 and "Interrupted (Ctrl+C) while reading the channel" in out
    assert not (tmp_path / "a.txt").exists()


def test_the_list_never_replaces_a_file(tmp_path, capsys, channel):
    channel["entries"] = [entry(1, 2)]
    (tmp_path / "list.txt").write_text("mine", "utf-8")
    code, out = run(capsys, "@somebody", str(tmp_path / "new" / "list.txt"), "last:1")
    assert code == 0 and (tmp_path / "new" / "list.txt").exists()       # a folder made
    code, out = run(capsys, "@somebody", str(tmp_path / "list.txt"), "last:1")
    assert (tmp_path / "list.txt").read_text("utf-8") == "mine"
    assert (tmp_path / "list-1.txt").exists() and "exists, so the list went to" in out


# The approximate dates of the list ---------------------------------------------------

@pytest.mark.parametrize("days, earliest, latest", [
    (0, 2, -1), (3, 5, 2), (10, 18, 9), (40, 72, 39), (400, 767, 399), (2000, 2367, 1999)])
def test_possible_days(days, earliest, latest):
    """yt-dlp's moment is "now" less the list's "N units ago", so the video is from
    up to one unit earlier: a day, a week, a month or a year; a day more on both
    sides."""
    assert index.possible_days(entry(1, days)) == (TODAY - dt.timedelta(days=earliest),
                                                   TODAY - dt.timedelta(days=latest))
    assert index.possible_days(entry(1, None)) is None


def test_fetch_channel_reads_lazily_with_approximate_dates(monkeypatch):
    """The yt-dlp side: approximate dates asked for, a redirect followed, the
    entries handed on one by one as yt-dlp reads them."""
    calls = []

    class FakeYDL:
        def __init__(self, opts):
            calls.append(("opts", opts))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append(("closed",))

        def extract_info(self, url, download, process, ie_key=None):
            calls.append(("extract", url, process, ie_key))
            if url == CHANNEL:
                return {"_type": "url", "url": "https://real/videos", "ie_key": "YoutubeTab"}

            def entries():
                for number in (1, 2, 3):
                    calls.append(("entry", number))
                    yield entry(number, number)
            return {"_type": "playlist", "channel": "Some Body", "entries": entries()}

    monkeypatch.setattr(index.yt_dlp, "YoutubeDL", FakeYDL)
    args = yt2html.parse_args(["--channel-list", "@somebody", "x.txt", "last:1",
                               "--cookies", "firefox"])
    found = index.fetch_channel(CHANNEL, args)
    assert next(found)["channel"] == "Some Body"
    assert next(found)["id"] == "CHANNELVI01"
    found.close()
    opts = calls[0][1]
    assert opts["extractor_args"] == {"youtubetab": {"approximate_date": [""]}}
    assert opts["extract_flat"] == "in_playlist" and opts["cookiesfrombrowser"][0] == "firefox"
    assert calls[1:] == [("extract", CHANNEL, False, None),
                         ("extract", "https://real/videos", False, "YoutubeTab"),
                         ("entry", 1), ("closed",)]           # the rest never read


@pytest.mark.parametrize("days, shown", [
    (0, TODAY.strftime("%d.%m.%Y")), (10, (TODAY - dt.timedelta(days=10)).strftime("%d.%m.%Y")),
    (40, (TODAY - dt.timedelta(days=40)).strftime("%m.%Y")),
    (400, (TODAY - dt.timedelta(days=400)).strftime("%Y"))])
def test_approximate_comments(days, shown):
    """last:N: "// ~" and the list's date, as precise as the list tells it."""
    assert index.approximate_comment(entry(1, days)) == f"// ~{shown}"
    assert index.approximate_comment(entry(1, None)) is None


def test_the_newest_n_without_a_date(tmp_path, capsys, channel):
    channel["entries"] = [entry(1, None)]
    run(capsys, "@somebody", str(tmp_path / "list.txt"), "last:1")
    assert (tmp_path / "list.txt").read_text("utf-8").endswith("\n\n// Video 1\nCHANNELVI01\n")


def test_oldest_first():
    day = dt.date(2025, 3, 1)
    later = dt.date(2025, 4, 1)
    # as the list gives them, newest first: [id, title, exact day or comment, approximate]
    chosen = [["A", "", later, later],
              ["B", "", None, None],              # no date at all: stays after the next older
              ["C", "", day, later],              # exact day before the approximate one
              ["D", "", "// ~03.2025", day],      # last:N: a comment, the approximate day counts
              ["E", "", day, None],               # the same day as C, listed later: older
              ["F", "", None, None]]              # nothing older: first
    assert [video[0] for video in index.oldest_first(chosen)] == ["F", "E", "D", "C", "B", "A"]
    # listed oldest, but the approximate day says later: moved (F stays after it)
    chosen.append(["G", "", None, dt.date(2025, 3, 15)])
    assert [video[0] for video in index.oldest_first(chosen)] == ["E", "D", "C", "B", "G", "F", "A"]
    assert index.oldest_first([]) == []


def test_a_list_not_newest_first(tmp_path, capsys, channel):
    # YouTube's Videos tab lists the newest first; if it does not, the dates decide
    channel["entries"] = [entry(1, 40), entry(2, 1), entry(3, 400)]
    run(capsys, "@somebody", str(tmp_path / "last.txt"), "last:3")
    assert [i.ref for i in index.read_list_file(str(tmp_path / "last.txt")).items()] == [
        "CHANNELVI03", "CHANNELVI01", "CHANNELVI02"]
    year = TODAY.year - 3
    channel["entries"] = [entry(4, (TODAY - dt.date(year, 3, 1)).days),
                          entry(5, (TODAY - dt.date(year, 9, 1)).days)]
    channel["days"] = {"CHANNELVI04": None, "CHANNELVI05": None}     # no exact day read
    run(capsys, "@somebody", str(tmp_path / "period.txt"), str(year))
    assert [i.ref for i in index.read_list_file(str(tmp_path / "period.txt")).items()] == [
        "CHANNELVI04", "CHANNELVI05"]
