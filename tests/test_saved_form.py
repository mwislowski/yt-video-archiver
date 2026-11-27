"""The data file: what it keeps of yt-dlp's data (saved_form()), with no
download addresses and no IP address, and a page made from it the same as
one made from the whole data."""

import datetime as dt
import json

import yt2html
from conftest import caption_formats

BUILT = dt.datetime(2026, 11, 27, 12, 0)
DAY = dt.date(2026, 11, 5)
MB = 1_000_000
IP = "203.0.113.7"
VIDEOPLAYBACK = f"https://rr3---sn-test.googlevideo.com/videoplayback?expire=1&ip={IP}&itag="


def fmt(fid, vcodec, acodec, **extra):
    """A format with what yt-dlp gives besides what the program reads."""
    return {"format_id": fid, "vcodec": vcodec, "acodec": acodec, "url": VIDEOPLAYBACK + fid,
            "http_headers": {"User-Agent": "Mozilla/5.0"}, "downloader_options": {"http_chunk_size": 1},
            "abr": 129.5, "audio_channels": 2, "source_preference": -1, "has_drm": False,
            **extra}


def youtube_data(info) -> dict:
    """The fixture's info dict as yt-dlp would give it: formats with their
    addresses, caption lists, thumbnails, chapters and fields the program
    does not read."""
    timedtext = f"https://www.youtube.com/api/timedtext?v=TESTVIDEO01&ip={IP}&lang="
    kind = lambda acont, lang: f"&xtags=acont%3D{acont}%3Alang%3D{lang}"
    info.update({
        "formats": [
            {"format_id": "sb0", "ext": "mhtml", "format_note": "storyboard", "vcodec": "none",
             "acodec": "none", "url": "https://i.ytimg.com/sb/TESTVIDEO01/storyboard3_L0/default.jpg",
             "fragments": [{"url": "https://i.ytimg.com/sb/0.jpg", "duration": 10}],
             "rows": 10, "columns": 10},
            fmt("137", "avc1.640028", "none", ext="mp4", width=1920, height=1080, fps=25,
                filesize=100 * MB, tbr=1300.2, dynamic_range="SDR", protocol="https",
                format_note="1080p", fragments=[{"url": "x"}]),
            fmt("399", "av01.0.08M.08", "none", ext="mp4", width=1920, height=1080, fps=25,
                filesize_approx=80 * MB, protocol="https", format_note="1080p"),
            fmt("701", "av01.0.13M.10.0.110.09.16.09.0", "none", ext="mp4", width=3840,
                height=2160, fps=60, tbr=20000, dynamic_range="HDR10", protocol="https",
                format_note="2160p60 HDR"),
            fmt("249", "none", "opus", ext="webm", tbr=50, language="de",
                language_preference=10, protocol="https"),      # the original, by yt-dlp's mark
            fmt("234", "none", "mp4a.40.2", ext="mp4", protocol="m3u8_native", tbr=130,
                language="en", manifest_url="https://manifest.googlevideo.com/api/manifest/"
                f"hls_playlist/ip/{IP}/xtags/acont%3Doriginal%3Alang%3Den/index.m3u8"),
            fmt("140", "none", "mp4a.40.2", ext="m4a", filesize=7 * MB, language="en",
                language_preference=-1, protocol="https", format_note="original, medium",
                url=VIDEOPLAYBACK + "140" + kind("original", "en")),
            fmt("140-1", "none", "mp4a.40.2", ext="m4a", filesize=9 * MB, language="pl",
                language_preference=-1, protocol="https", format_note="Polish",
                url=VIDEOPLAYBACK + "140-1" + kind("dubbed-auto", "pl")),
            fmt("251-drc", "none", "opus", ext="webm", filesize=8 * MB, language="en",
                protocol="https"),
        ],
        "requested_formats": [{"format_id": "137", "url": VIDEOPLAYBACK + "137"}],
        "format": "137 - 1920x1080 (1080p)+140 - audio only", "format_id": "137+140",
        "url": VIDEOPLAYBACK + "137", "http_headers": {"Accept": "*/*"},
        "subtitles": {"pl": caption_formats("Polish", timedtext + "pl"),
                      "live_chat": [{"ext": "json", "url": f"https://www.youtube.com/live?ip={IP}"}]},
        "automatic_captions": {"en-orig": caption_formats("English (Original)", timedtext + "en"),
                               "en": caption_formats("English", timedtext + "en"),
                               "de": caption_formats("German", timedtext + "de&tlang=de"),
                               "xx": []},
        "thumbnails": [{"id": "0", "url": "https://i.ytimg.test/vi/TESTVIDEO01/default.jpg",
                        "preference": -10, "width": 120, "height": 90, "resolution": "120x90"},
                       {"id": "40", "url": "https://i.ytimg.test/vi/TESTVIDEO01/maxresdefault.jpg",
                        "preference": 0}],
        "chapters": [{"start_time": 0, "end_time": 110, "title": "Intro"},
                     {"start_time": 110, "end_time": 597, "title": "Second part"}],
        "heatmap": [{"start_time": 0, "end_time": 6, "value": 1.0}],
        "tags": ["test"], "categories": ["Education"], "channel_id": "UCtest",
        "uploader_id": "@Channel", "availability": "public", "age_limit": 0,
        "_format_sort_fields": ["res"], "webpage_url_domain": "youtube.com",
        "requested_subtitles": None, "epoch": 1_795_000_000,
        "_type": "video", "_version": {"version": "2026.11.20", "repository": "yt-dlp/yt-dlp"},
        "live_status": "not_live", "language": "en", "channel": "Channel",
        "channel_url": "https://www.youtube.com/channel/UCtest",
        yt2html.CAPTION_KEY: {"language": "en", "kind": "asr"},
        yt2html.TRANSLATIONS_KEY: {"pl": "Polish", "de": "German"},
        yt2html.DOWNLOAD_KEY: {"yt2html": yt2html.__version__, "deno": True},
    })
    for comment in info["comments"][1:]:
        comment["author_id"] = "UC" + comment["id"]
        comment["unknown_field"] = "dropped"
    info["comments"][0]["author_is_verified"] = True     # (and no author_id: its handle links)
    return info


def through_file(info) -> dict:
    """The data as --json load reads them back from the data file."""
    return yt2html.upgrade_saved(json.loads(yt2html.data_text(info)))


def render(info) -> str:
    return yt2html.render_page(json.loads(json.dumps(info)), None, {}, DAY, built=BUILT,
                               from_json=True)


def test_a_page_from_the_data_file_is_the_page_from_the_whole_data(info):
    full = youtube_data(info)
    saved = through_file(full)
    assert len(json.dumps(saved)) < len(json.dumps(full))
    assert render(saved) == render(full)
    # (the formats table, the languages and the transcript row are in it)
    html = render(saved)
    assert "auto-dubbed" in html and "Video formats" in html
    # the copy's parts (--json show:full) say the same
    assert yt2html.show_contents(saved) == yt2html.show_contents(full)


def test_the_data_file_keeps_what_the_program_reads(info):
    saved = through_file(youtube_data(info))
    for key in ("requested_formats", "format", "format_id", "url", "http_headers", "heatmap",
                "availability", "age_limit", "_format_sort_fields", "webpage_url_domain",
                "requested_subtitles", "live_status"):
        assert key not in saved, key
    # the four kept for the index and search (the user, 27.11.2026)
    assert (saved["tags"], saved["categories"], saved["channel_id"], saved["uploader_id"]) == \
           (["test"], ["Education"], "UCtest", "@Channel")
    # formats: no storyboard, no address; the kind of audio from the address
    formats = {f["format_id"]: f for f in saved["formats"]}
    assert "sb0" not in formats
    assert formats["140"] == {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2",
                              "filesize": 7 * MB, "language": "en",
                              "language_preference": -1, "protocol": "https",
                              "format_note": "original, medium",
                              yt2html.AUDIO_KIND_KEY: "original"}
    assert formats["140-1"][yt2html.AUDIO_KIND_KEY] == "dubbed-auto"
    assert formats["234"][yt2html.AUDIO_KIND_KEY] == "original"         # from the HLS path
    assert yt2html.AUDIO_KIND_KEY not in formats["137"]
    assert yt2html.audio_kind(formats["140-1"]) == "dubbed-auto"
    # captions: every language with its name (none without formats); live chat
    # as an empty entry
    assert saved["subtitles"] == {"pl": [{"name": "Polish"}], "live_chat": [{}]}
    assert saved["automatic_captions"] == {"en-orig": [{"name": "English (Original)"}],
                                           "en": [{"name": "English"}], "de": [{"name": "German"}]}
    assert saved["thumbnails"][0] == {"url": "https://i.ytimg.test/vi/TESTVIDEO01/default.jpg",
                                      "preference": -10, "width": 120, "height": 90}
    assert saved["chapters"][0] == {"start_time": 0, "end_time": 110, "title": "Intro"}
    assert all("unknown_field" not in c for c in saved["comments"])
    assert saved["comments"][1]["author_id"] == "UCN1"
    # the program's own keys whole
    for key in (yt2html.TRANSCRIPT_KEY, yt2html.MARKERS_KEY, yt2html.CAPTION_KEY,
                yt2html.TRANSLATIONS_KEY, yt2html.DOWNLOAD_KEY, yt2html.ARCHIVED_KEY):
        assert saved[key] == json.loads(json.dumps(info[key])), key
    # keys with None stay: "comments": None means turned off
    assert yt2html.saved_form({"id": "x", "comments": None, "comment_count": None}) == \
           {"id": "x", "comments": None, "comment_count": None}


def test_no_ip_address_in_the_data_file(info):
    text = yt2html.data_text(youtube_data(info)).decode("utf-8")
    assert IP not in text and "googlevideo" not in text and "timedtext" not in text


def test_an_address_with_an_ip_address_is_left_out_wherever_it_is(capsys):
    address = f"https://img.test/a.jpg?ip={IP}"
    data = {"id": "x", "thumbnail": address, "description": address,
            "thumbnails": [{"url": address}, {"url": "https://img.test/b.jpg"}],
            yt2html.CARDS_KEY: [{"title": address, "image": address, "url": "https://x.test/"}],
            yt2html.VIDEO_KEY: {"sources": [address, "kept"]}}
    assert yt2html.saved_form(data) == {
        "id": "x", "description": address,      # what people wrote stays as it is
        "thumbnails": [{}, {"url": "https://img.test/b.jpg"}],
        yt2html.CARDS_KEY: [{"title": address, "url": "https://x.test/"}],
        yt2html.VIDEO_KEY: {"sources": ["kept"]}}
    assert ("Note: left out of the saved data, as they hold an IP address: thumbnail, "
            f"thumbnails[].url, {yt2html.CARDS_KEY}[].image, {yt2html.VIDEO_KEY}.sources[]"
            in capsys.readouterr().out)
    clean = yt2html.saved_form(data)
    capsys.readouterr()
    assert yt2html.old_form(data) and not yt2html.old_form(clean)
    assert capsys.readouterr().out == ""        # old_form() says nothing


def test_json_write_writes_old_data_anew(capsys, saved, youtube):
    """Transitional (YT2HTML.local.md 5.4): data of 0.13.1 and before
    are written anew by --json write, without asking YouTube, and the page with
    them (it keeps the mark of its data)."""
    data = youtube_data(json.loads(saved.read_text("utf-8")))
    saved.write_text(json.dumps(data), encoding="utf-8")
    folder = str(saved.parent)
    assert yt2html.main([str(saved), "-o", folder, "--json", "load"]) == 0
    page = next(saved.parent.glob("*.html"))
    capsys.readouterr()
    assert yt2html.main(["TESTVIDEO01", "-o", folder, "--json", "write"]) == 0
    out = capsys.readouterr().out
    assert youtube["extracted"] == 0
    assert ("Saved data of an older form: written anew without the download addresses and the "
            "fields yt2html does not use.") in out
    text = saved.read_text("utf-8")
    assert IP not in text and "heatmap" not in text
    assert yt2html.UPDATES_KEY not in json.loads(text)        # no run of its own in the history
    assert yt2html.made_from(page, yt2html.data_mark(saved.read_bytes()))
    # once written anew, nothing is missing
    assert yt2html.main(["TESTVIDEO01", "-o", folder, "--json", "write"]) == 0
    assert "Nothing missing in the saved data" in capsys.readouterr().out


def test_json_merge_writes_the_data_file_form(capsys, tmp_path, info):
    """--json merge (one's own translation, one's own corrections) writes
    the data in the same form as every other write."""
    path = tmp_path / "Title [TESTVIDEO01].info.json"
    path.write_text(json.dumps(youtube_data(info)), encoding="utf-8")
    starts = (0, 6000, 120000)
    for language, kind in (("de", "own"), ("en", "fixed")):
        srt = tmp_path / f"Title [TESTVIDEO01].{language}.{kind}.srt"
        srt.write_text("\n".join(f"{i}\n{yt2html.srt_time(s)} --> {yt2html.srt_time(s + 2000)}\n"
                                 f"line {i}\n" for i, s in enumerate(starts, 1)), encoding="utf-8")
        assert yt2html.main(["--json", "merge", str(srt), "-o", str(tmp_path)]) == 0
        text = path.read_text("utf-8")
        assert IP not in text and "heatmap" not in text, kind
        path.write_text(json.dumps(youtube_data(json.loads(text))), encoding="utf-8")
