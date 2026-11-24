"""The index of a list file (--index FILE, --index-only FILE)."""

import json
import re
import shutil
import struct
from pathlib import Path

import pytest
import yt_dlp

import yt2html
import yt2html_index as index

DATA = Path(__file__).parent / "data"
PLAYLIST_ID = "PLtest1234567890"
PLAYLIST = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}"
COURSE = json.loads((DATA / "playlist_kurs.json").read_text(encoding="utf-8"))  # real, flat
COVER = "data:image/jpeg;base64,Q09WRVI="


def url(n: int) -> str:
    return f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"


def entry(n: int) -> dict:
    return {"_type": "url", "ie_key": "Youtube", "id": f"TESTVIDEO0{n}", "url": url(n),
            "title": f"Translated title {n}"}


@pytest.fixture
def youtube(monkeypatch, info):
    """Fake YouTube: playlists by id (a list of entries, or an error message);
    every video is the test video under its own id; `failing` gives errors."""

    class FakeYouTube:
        playlists = {PLAYLIST_ID: [entry(1), entry(2)]}
        failing = {}
        interrupt = set()
        fetched = []
        lists_read = []

    def fetch_info(source, args):
        FakeYouTube.fetched.append(source)
        video_id = yt2html.video_id_from_url(source)
        if video_id in FakeYouTube.interrupt:
            raise KeyboardInterrupt
        if video_id in FakeYouTube.failing:
            raise yt_dlp.utils.DownloadError(FakeYouTube.failing[video_id])
        return {**info, "id": video_id, "title": f"Original title {video_id[-1]}"}

    def fetch_playlist(source, args):
        FakeYouTube.lists_read.append(source)
        found = FakeYouTube.playlists.get(yt2html.playlist_id_from_url(source))
        if found is None or isinstance(found, str):
            raise yt_dlp.utils.DownloadError(found or "ERROR: [youtube:tab] x: offline")
        return {"title": "Test playlist", "channel": "Channel",
                "uploader_url": "https://www.youtube.com/@Channel",
                "channel_url": "https://www.youtube.com/channel/UCx", "playlist_count": 3,
                "description": "About the playlist.", "entries": found}

    monkeypatch.setattr(yt2html, "fetch_info", fetch_info)
    monkeypatch.setattr(yt2html, "fetch_playlist", fetch_playlist)
    return FakeYouTube


def write_list(tmp_path, text: str, name="series.txt") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def page(folder: Path, video_id: str, name: str | None = None, **meta) -> Path:
    """A page as yt2html writes it (only what the index reads)."""
    tags = {"metadata": "2", "video-id": video_id, "title": f"Title of {video_id}",
            "duration": "1308", "language": "pl", **meta}
    path = folder / (name or f"Page {video_id} [{video_id}].html")
    path.write_text("<!DOCTYPE html>\n<head>\n" + "".join(
        f'<meta name="yt2html:{key}" content="{yt2html.esc(value)}">\n'
        for key, value in tags.items() if value is not None)
        + "<title>t</title>\n</head><body>content</body>", encoding="utf-8")
    return path


def build(tmp_path, text: str, playlists: dict | None = None, folder: Path | None = None):
    """(page, builder) for a list file over a folder (tmp_path by default)."""
    listing = index.read_list_file(write_list(tmp_path, text))
    return index.render_index(listing, playlists or {}, folder or tmp_path)


def html(tmp_path, text: str, playlists: dict | None = None, folder: Path | None = None) -> str:
    return build(tmp_path, text, playlists, folder)[0]


def body(page_html: str) -> str:
    main = re.split(r'<main class="page index[^"]*">', page_html, maxsplit=1)[1]
    return main.split("<footer", 1)[0]


def rows(page_html: str) -> list[str]:
    return re.findall(r'<div class="row[^"]*">.*?</div></div>(?=\n|$)', body(page_html))


def blocks(page_html: str) -> list[str]:
    """Each block of the index (title, notes, chapters, rows) as a short code:
    'h2 d0 1 Part', 'row d1 2', 'note d0'."""
    found = []
    for line in body(page_html).strip().split("\n"):
        tag = re.match(r'<(\w+) class="([^"]*)"', line)
        if not tag:
            continue
        kind, classes = tag.group(1), tag.group(2).split()
        number = re.search(r'<span class="num">(\d+)</span>', line)
        number = number.group(1) if number else "-"
        if kind.startswith("h") and kind != "h1":
            text = re.search(r'<span class="text">(.*?)</span>', line).group(1)
            found.append(f"{kind} {classes[1]} {number} {text}".rstrip())
        elif "row" in classes:
            found.append(f"row {classes[1]} {number}")
        elif "note" in classes:
            found.append(f"note {classes[1]}")
    return found


def playlist(*ids: str, title="Test playlist", **extra) -> dict:
    return {"title": title, "channel": "Channel", "channel_url": "https://www.youtube.com/@Channel",
            "count": len(ids), "description": "", "entries": list(ids), **extra}


# Structure: chapters, playlists, numbers --------------------------------------------

def test_a_lone_playlist_puts_its_videos_in_its_place(tmp_path):
    result = html(tmp_path, f"{PLAYLIST_ID}\n", {PLAYLIST_ID: playlist("TESTVIDEO01", "TESTVIDEO02")})
    assert blocks(result) == ["note d0", "row d0 1", "row d0 2"]
    assert '<h1 class="title">Test playlist</h1>' in result   # the only playlist, no "#"


def test_two_playlists_become_chapters(tmp_path):
    lists = {PLAYLIST_ID: playlist("TESTVIDEO01"), "PLother000001": playlist("TESTVIDEO02",
                                                                             title="Other")}
    result = html(tmp_path, f"# Two\n{PLAYLIST_ID}\nPLother000001\n", lists)
    assert blocks(result) == ["h2 d0 1 Test playlist", "note d0", "row d1 1",
                              "h2 d0 2 Other", "note d0", "row d1 1"]


def test_a_playlist_next_to_a_video_becomes_a_chapter_one_level_lower(tmp_path):
    result = html(tmp_path, f"## Part\nTESTVIDEO03\n{PLAYLIST_ID}\n",
                  {PLAYLIST_ID: playlist("TESTVIDEO01", "TESTVIDEO02")})
    assert blocks(result) == ["h2 d0 1 Part", "row d1 1", "h3 d1 2 Test playlist", "note d1",
                              "row d2 1", "row d2 2"]
    assert "<title>Test playlist</title>" in result     # the only playlist of the file


def test_at_the_lowest_level_a_playlist_stands_next_to_the_others(tmp_path):
    result = html(tmp_path, f"# T\n#### Deep\nTESTVIDEO03\n{PLAYLIST_ID}\nTESTVIDEO04\n",
                  {PLAYLIST_ID: playlist("TESTVIDEO01", "TESTVIDEO02")})
    assert blocks(result) == ["h4 d2 1 Deep", "row d3 1", "note d2", "row d3 2", "row d3 3",
                              "row d3 4"]


def test_an_empty_heading_over_a_lone_playlist_takes_its_title(tmp_path):
    lists = {PLAYLIST_ID: playlist("TESTVIDEO01")}
    result = html(tmp_path, f"# T\n##\n{PLAYLIST_ID}\n## Mine\n{PLAYLIST_ID}\n## Last\n###\n"
                            "TESTVIDEO02\n", lists)
    assert blocks(result) == ["h2 d0 1 Test playlist", "note d0", "row d1 1",
                              "h2 d0 2 Mine", "note d0", "row d1 1",       # the heading wins
                              "h2 d0 3 Last", "h3 d1 1", "row d2 1"]       # empty, numbered


def test_a_title_of_ones_own_over_a_playlist(tmp_path):
    lists = {PLAYLIST_ID: playlist("TESTVIDEO01")}
    assert "<title>My course</title>" in html(tmp_path, f"= My course\n{PLAYLIST_ID}\n", lists)
    result = html(tmp_path, f"# T\n= My course\n{PLAYLIST_ID}\nTESTVIDEO02\n##\n= Inner\n"
                            f"{PLAYLIST_ID}\n## Heading\n= Loses\n{PLAYLIST_ID}\n", lists)
    assert blocks(result)[0] == "h2 d0 1 My course"                # its chapter
    assert "h2 d0 3 Inner" in blocks(result)                      # the empty heading
    assert "h2 d0 4 Heading" in blocks(result)                    # the heading's own text wins
    # the playlist's own note still names it as on YouTube
    assert result.count(f'<a href="{PLAYLIST}" target="_blank" rel="noopener noreferrer">'
                        f'Test playlist</a>') == 3


def test_numbers_start_at_one_on_each_level_and_skip_notes(tmp_path):
    result = html(tmp_path, "# T\n> Intro\nTESTVIDEO01\n## A\n> About A\nTESTVIDEO02\n"
                            "### A1\nTESTVIDEO03\nTESTVIDEO04\n## B\n")
    # notes on their own at the indent of their chapter (the list's at the title's)
    assert blocks(result) == ["note d0", "row d0 1", "h2 d0 2 A", "note d0", "row d1 1",
                              "h3 d1 2 A1", "row d2 1", "row d2 2", "h2 d0 3 B"]


def test_a_skipped_level_is_shown_as_written(tmp_path):
    assert blocks(html(tmp_path, "### Deep first\nTESTVIDEO01\n")) == ["h3 d1 1 Deep first",
                                                                       "row d2 1"]


def test_no_numbers(tmp_path):
    result = html(tmp_path, "!numbers: no\n## A\nTESTVIDEO01\n")
    assert blocks(result) == ["h2 d0 - A", "row d1 -"]          # the indents stay
    assert 'class="num"' not in result


def test_two_playlists_give_no_title(tmp_path):
    lists = {PLAYLIST_ID: playlist("TESTVIDEO01"), "PLother000001": playlist("TESTVIDEO02")}
    assert "<title>series</title>" in html(tmp_path, f"{PLAYLIST_ID}\nPLother000001\n", lists)


def test_title_from_the_file_name(tmp_path):
    result = html(tmp_path, "#\nTESTVIDEO01\n")
    assert "<title>series</title>" in result and '<h1 class="title">series</h1>' in result


def test_the_same_video_twice_has_two_rows(tmp_path):
    assert len(rows(html(tmp_path, "TESTVIDEO01\nTESTVIDEO01\n"))) == 2


# Rows: what is in the folder ---------------------------------------------------

def test_a_video_with_its_page_and_file(tmp_path):
    page(tmp_path, "TESTVIDEO01", name="My page & co [TESTVIDEO01].html", cover=COVER,
         language="en-US")
    (tmp_path / "Title [TESTVIDEO01].mp4").write_bytes(b"")
    row, = rows(html(tmp_path, "TESTVIDEO01\n"))
    assert '<div class="row d0">' in row
    # the thumbnail plays the file (its address relative to the index)
    assert (f'<span class="thumb" data-file="Title%20%5BTESTVIDEO01%5D.mp4"><img src="{COVER}" '
            'alt=""><span class="time">21:48</span></span>') in row
    assert ('<a class="name" href="My%20page%20%26%20co%20%5BTESTVIDEO01%5D.html" '
            'target="_blank" rel="noopener noreferrer">Title of TESTVIDEO01</a>') in row
    # language as in the data, in small letters; the format with the file's name;
    # YouTube with the original title
    assert ('<div class="line">en-us · <span title="Title [TESTVIDEO01].mp4">MP4</span> · '
            f'<a href="{url(1)}" target="_blank" rel="noopener noreferrer" '
            'title="Title of TESTVIDEO01">YouTube</a></div>') in row


def test_only_the_page(tmp_path):
    page(tmp_path, "TESTVIDEO01", cover=COVER)
    row, = rows(html(tmp_path, "TESTVIDEO01\n"))
    assert '<div class="row d0 missing">' in row
    assert f'<span class="thumb off"><img src="{COVER}" alt="">' in row      # dimmed, with time
    assert '<span class="time">21:48</span>' in row
    assert 'pl · <span class="miss">only the page downloaded</span> · <a href=' in row


def test_a_video_no_longer_on_youtube(tmp_path):
    page(tmp_path, "TESTVIDEO01", unavailable="2026-12-03T14:05")
    (tmp_path / "x [TESTVIDEO01].webm").write_bytes(b"")
    row, = rows(html(tmp_path, "TESTVIDEO01\n"))
    assert ('· WEBM</span> · <span class="miss" title="Title of TESTVIDEO01">no longer on '
            'YouTube</span></div>') in row.replace('<span title="x [TESTVIDEO01].webm">', "")
    assert "YouTube</a>" not in row


def test_console_notes_follow_the_lines(tmp_path):
    result, builder = build(tmp_path, "clip.mp4\nTESTVIDEO01\nother.mkv\n")
    assert [note.split(":")[0] for note in builder.console_notes(advice=True)] == [
        "line 1", "line 2", "line 3"]


def test_not_downloaded(tmp_path):
    result, builder = build(tmp_path, "TESTVIDEO01\nOld name [TESTVIDEO02].html\n"
                                      "= Mine\nTESTVIDEO03\n")
    first, second, third = rows(result)
    assert '<span class="thumb off empty"></span>' in first
    assert '<span class="name"><span class="id">TESTVIDEO01</span></span>' in first
    assert ('<span class="miss">not downloaded</span> · '
            f'<a href="{url(1)}" target="_blank" rel="noopener noreferrer">YouTube</a>') in first
    assert '<span class="name">Old name</span>' in second         # from the name in the line
    assert '<span class="name">Mine</span>' in third
    assert builder.console_notes(advice=True)[:2] == [
        "line 1: TESTVIDEO01 not downloaded (--index downloads it)",
        "line 2: TESTVIDEO02 not downloaded (--index downloads it)"]
    assert builder.console_notes(advice=False)[0] == "line 1: TESTVIDEO01 not downloaded"


def test_titles_never_come_from_the_entries_of_a_playlist(tmp_path):
    # YouTube sends the entries' titles translated (the course is Polish; these are English)
    data = {COURSE["id"]: playlist(*[e["id"] for e in COURSE["entries"]])}
    result, builder = build(tmp_path, f"{COURSE['id']}\n", data)
    assert "Python Fundamentals" not in result
    assert f'<span class="id">{COURSE["entries"][2]["id"]}</span>' in result
    assert builder.console_notes(advice=True) == ["line 1: 16 videos not downloaded "
                                                  "(--index downloads them)"]


def test_formats_of_an_unfinished_video_download_are_not_the_video(tmp_path):
    """As on the page: "Title [ID].f399.mp4" (video only, left by an
    interrupted --video) is not the video, also beside the finished file."""
    page(tmp_path, "TESTVIDEO01")
    mp4(tmp_path / "Title [TESTVIDEO01].f399.mp4", 65)
    (tmp_path / "Title [TESTVIDEO01].f251.webm").write_bytes(b"audio only")
    row, = rows(html(tmp_path, "TESTVIDEO01\n"))
    assert "data-file" not in row and "f399" not in row
    mp4(tmp_path / "Title [TESTVIDEO01].mp4", 65)
    row, = rows(html(tmp_path, "TESTVIDEO01\n"))
    assert 'data-file="Title%20%5BTESTVIDEO01%5D.mp4"' in row and "f399" not in row
    assert yt2html.video_files(tmp_path, "TESTVIDEO01") == [tmp_path / "Title [TESTVIDEO01].mp4"]


def test_several_pages_and_several_videos_each_get_a_note(tmp_path, capsys):
    page(tmp_path, "TESTVIDEO01", "A [TESTVIDEO01].html")
    page(tmp_path, "TESTVIDEO01", "B [TESTVIDEO01].html")
    mp4(tmp_path / "A [TESTVIDEO01].mp4", 65)
    mp4(tmp_path / "B [TESTVIDEO01].mp4", 65)
    html(tmp_path, "TESTVIDEO01\n")
    out = capsys.readouterr().out
    assert "Note: several pages for [TESTVIDEO01]" in out
    assert "Note: several video files for [TESTVIDEO01]" in out


def test_a_title_of_ones_own_wins_over_the_page(tmp_path):
    page(tmp_path, "TESTVIDEO01")
    row, = rows(html(tmp_path, "= Episode one\nTESTVIDEO01\n"))
    assert '>Episode one</a>' in row and 'title="Title of TESTVIDEO01">YouTube' in row


def test_a_video_file_without_a_page(tmp_path):
    mp4(tmp_path / "Clip name [TESTVIDEO01].mp4", 65)
    result, builder = build(tmp_path, "TESTVIDEO01\n")
    row, = rows(result)
    assert '<div class="row d0">' in row and '<span class="name">Clip name</span>' in row
    assert ('<span class="thumb format" data-file="Clip%20name%20%5BTESTVIDEO01%5D.mp4">MP4'
            '<span class="time">1:05</span></span>') in row
    assert builder.console_notes(advice=True) == [          # after --index-only
        "line 1: TESTVIDEO01 has a video file but no page (Clip name [TESTVIDEO01].mp4) "
        "(--index makes it)"]
    assert builder.console_notes(advice=False) == [         # after --index: no advice
        "line 1: TESTVIDEO01 has a video file but no page (Clip name [TESTVIDEO01].mp4)"]


def test_a_file_of_ones_own(tmp_path):
    mp4(tmp_path / "Holiday 2026.mp4", 2282)
    row, = rows(html(tmp_path, "Holiday 2026.mp4\n"))
    assert ('<span class="thumb format" data-file="Holiday%202026.mp4">MP4'
            '<span class="time">38:02</span></span>') in row
    assert '<span class="name">Holiday 2026</span>' in row       # plain text: no page
    assert '<div class="line"><span title="Holiday 2026.mp4">MP4</span></div>' in row
    assert "YouTube" not in row
    named, = rows(html(tmp_path, "= Our trip\nHoliday 2026.mp4\n"))
    assert '<span class="name">Our trip</span>' in named


def test_a_file_of_ones_own_that_is_not_there(tmp_path):
    result, builder = build(tmp_path, "Missing.mkv\n")
    row, = rows(result)
    assert ('<div class="row d0 missing"><span class="thumb off empty"></span><div class="info">'
            '<div class="title"><span class="num">1</span> <span class="name">Missing</span></div>'
            in row)
    assert '<span class="miss" title="Missing.mkv">file not found</span>' in row
    assert builder.console_notes(advice=True) == [
        f"line 1: file not found: Missing.mkv (looked for in {tmp_path}; check the name)"]


def test_no_thumbnails(tmp_path):
    page(tmp_path, "TESTVIDEO01", cover=COVER)
    (tmp_path / "x [TESTVIDEO01].mp4").write_bytes(b"")
    page(tmp_path, "TESTVIDEO02")
    result = html(tmp_path, "!thumbnails: no\nTESTVIDEO01\nTESTVIDEO02\nTESTVIDEO03\n")
    first, second, third = rows(result)
    assert COVER not in result and 'class="thumb' not in result
    assert (f'<span class="play" data-file="x%20%5BTESTVIDEO01%5D.mp4">{index.PLAY_ICON}</span>'
            in first)
    assert '<div class="line">21:48 · pl · <span title=' in first     # the time in the line
    assert '<span class="play off">' in second and "data-file" not in second + third
    assert '21:48 · pl · <span class="miss">' in second
    assert '<span class="play off">' in third and '<div class="line"><span class="miss">' in third


def test_no_thumbnails_and_no_duration(tmp_path):
    page(tmp_path, "TESTVIDEO01", duration=None)
    row, = rows(html(tmp_path, "!thumbnails: no\nTESTVIDEO01\n"))
    assert '<div class="line">pl · <span class="miss">' in row


def test_a_folder_is_no_file(tmp_path):
    (tmp_path / "clip.mp4").mkdir()
    assert "file not found" in html(tmp_path, "clip.mp4\n")


def test_a_page_of_an_older_version(tmp_path):
    page(tmp_path, "TESTVIDEO01", metadata=None, duration=None, language=None)
    result, builder = build(tmp_path, "TESTVIDEO01\n")
    assert ">Title of TESTVIDEO01</a>" in result      # what can be read is shown
    assert builder.console_notes(advice=False) == [
        "Made by an older version, without the data the index needs (make it again): "
        "Page TESTVIDEO01 [TESTVIDEO01].html"]


def test_an_unreadable_playlist_keeps_its_place(tmp_path):
    result = html(tmp_path, f"# T\n= My course\n{PLAYLIST_ID}\nTESTVIDEO01\n", {PLAYLIST_ID: None})
    first, second = rows(result)
    assert '<span class="name">My course</span>' in first
    assert ('<span class="miss">playlist could not be read</span> · '
            f'<a href="{PLAYLIST}" target="_blank" rel="noopener noreferrer">YouTube</a>') in first
    assert blocks(result) == ["row d0 1", "row d0 2"]
    # alone: no title from it
    assert "<title>series</title>" in html(tmp_path, f"{PLAYLIST_ID}\n", {PLAYLIST_ID: None})


# Notes ----------------------------------------------------------------------------

@pytest.mark.parametrize("note, shown", [
    ("**Bold** and *italic* and _too_", "<strong>Bold</strong> and <em>italic</em> and <em>too</em>"),
    ("snake_case_name and 2*3*4", "snake_case_name and 2*3*4"),
    ("**not closed and * alone", "**not closed and * alone"),
    ("<b>tags</b> & such", "&lt;b&gt;tags&lt;/b&gt; &amp; such"),
    ("www.example.org #tag @channel 1:23", "www.example.org #tag @channel 1:23"),
    # links: simplified Markdown (the user, 20.11.2026); a bare address is text
    ("See https://example.org/a_b_c.", "See https://example.org/a_b_c."),
    ("See [the docs](https://example.org/a_b_c).",
     'See <a href="https://example.org/a_b_c" target="_blank" rel="noopener noreferrer">'
     'the docs</a>.'),
    ("[](http://example.org/x?a=1&b=2)",       # empty brackets: the address shows
     '<a href="http://example.org/x?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">'
     'http://example.org/x?a=1&amp;b=2</a>'),
    ("**[bold](https://e.org)** [a_b *c*](https://e.org/d_e_f)",   # emphasis round, not in
     '<strong><a href="https://e.org" target="_blank" rel="noopener noreferrer">bold</a></strong>'
     ' <a href="https://e.org/d_e_f" target="_blank" rel="noopener noreferrer">a_b *c*</a>'),
    ("[<b>](https://e.org) [x](https://e.org/a(b)c)",   # text escaped; ")" ends the address
     '<a href="https://e.org" target="_blank" rel="noopener noreferrer">&lt;b&gt;</a> '
     '<a href="https://e.org/a(b" target="_blank" rel="noopener noreferrer">x</a>c)'),
    ("[x] (https://e.org) [x](e.org) [x](ftp://e.org) [x](https://e.org [x](https://e .org)",
     "[x] (https://e.org) [x](e.org) [x](ftp://e.org) [x](https://e.org [x](https://e .org)"),
    ("two [a](https://a.org) and [b](https://b.org)",
     'two <a href="https://a.org" target="_blank" rel="noopener noreferrer">a</a> and '
     '<a href="https://b.org" target="_blank" rel="noopener noreferrer">b</a>'),
    ("[a](https://a.org) \x000\x00",          # text like the inner mark is no link
     '<a href="https://a.org" target="_blank" rel="noopener noreferrer">a</a> 0'),
    # ((grey)): the colour of what the program adds (the user, 22.11.2026)
    ("Three parts ((the last one from 2024))",
     'Three parts <span class="aside">the last one from 2024</span>'),
    ("((**note:** see [this](https://e.org))) and **bold ((grey))**",   # with the others
     '<span class="aside"><strong>note:</strong> see <a href="https://e.org" target="_blank" '
     'rel="noopener noreferrer">this</a></span> and <strong>bold <span class="aside">grey'
     '</span></strong>'),
    ("((one)) and ((two))", '<span class="aside">one</span> and <span class="aside">two</span>'),
    # brackets inside in pairs (the user's playlist line, 22.11.2026)
    ("16 videos (((06.07.2020–16.06.2025))) · Channel",
     '16 videos <span class="aside">(06.07.2020–16.06.2025)</span> · Channel'),
    ("((see (x) and (y)))", '<span class="aside">see (x) and (y)</span>'),
    ("((a) b)) and ((c (d))", "((a) b)) and ((c (d))"),         # a bracket without its pair
    ("((x (y (z)) w))", "((x (y (z)) w))"),                     # pairs inside pairs: as written
    ("(( spaced )) and ((end ))", "(( spaced )) and ((end ))"),   # as written
    ("(()), f(x) and ((not closed", "(()), f(x) and ((not closed"),
])
def test_note_formatting(tmp_path, note, shown):
    assert f'<div class="note d0"><p>{shown}</p></div>' in html(tmp_path, f"> {note}\n")


def test_grey_in_notes_of_rows_and_in_the_style(tmp_path):
    row, = rows(html(tmp_path, "TESTVIDEO01\n> Of the video ((aside))\n"))
    assert '<p>Of the video <span class="aside">aside</span></p>' in row
    assert ".index .note .aside{color:var(--muted)}" in index.INDEX_CSS


def test_notes_in_a_row_are_paragraphs_of_one_note(tmp_path):
    result = html(tmp_path, "> One\n> Two\nTESTVIDEO01\n> Of the video\n> Also\n")
    assert '<div class="note d0"><p>One</p><p>Two</p></div>' in result
    row, = rows(result)
    # plain (the default): the note after the title and line, under the thumbnail
    assert ('</div></div><div class="note"><p>Of the video</p><p>Also</p></div></div>'
            in row)


@pytest.mark.parametrize("layout", ["plain", "compact", "cards"])
def test_the_layout_names_the_index_and_places_the_note(tmp_path, layout):
    result = html(tmp_path, f"!layout: {layout}\n> Intro\nTESTVIDEO01\n> Of the video\n")
    assert f'<main class="page index layout-{layout}">' in result
    row, = rows(result)
    info = re.search(r'<div class="info">.*?<div class="line">.*?</div>(.*)', row).group(1)
    note = '<div class="note"><p>Of the video</p></div>'
    # plain: the note follows the info (under the thumbnail); else inside it
    assert info == ("</div>" + note + "</div>" if layout == "plain" else note + "</div></div>")
    assert '<div class="note d0"><p>Intro</p></div>' in result   # chapters' notes: the same


def test_the_number_stands_beside_the_title_in_its_info(tmp_path):
    """Not a column of the row: the row is laid out as without numbers; the
    title (which the player shows) does not include the number."""
    row, = rows(html(tmp_path, "= Mine\nTESTVIDEO01\n"))
    assert ('<span class="thumb off empty"></span><div class="info"><div class="title">'
            '<span class="num">1</span> <span class="name">Mine</span></div><div class="line">'
            in row)
    row, = rows(html(tmp_path, "!numbers: no\n= Mine\nTESTVIDEO01\n"))
    assert '<div class="info"><span class="name">Mine</span><div class="line">' in row
    assert 'class="num"' not in row and 'class="title"' not in row


def test_the_default_layout_is_plain(tmp_path):
    assert '<main class="page index layout-plain">' in html(tmp_path, "TESTVIDEO01\n")


def test_the_layouts_in_css():
    css = index.INDEX_CSS
    assert ".layout-plain .row>.note{grid-column:1/3}" in css
    assert (".index.layout-plain .note,.index.layout-compact .note,.index.layout-cards .row .note{"
            in css)
    assert ".index.layout-cards .row .info{min-height:var(--thumb-h)" in css
    assert ".layout-cards .note{" not in css      # the notes of chapters keep their frame


def test_the_notes_of_a_playlist(tmp_path):
    data = {COURSE["id"]: {"title": COURSE["title"], "channel": COURSE["channel"],
                           "channel_url": COURSE["channel_url"],
                           "count": COURSE["playlist_count"],
                           "description": COURSE["description"], "entries": ["TESTVIDEO01"]}}
    result = html(tmp_path, f"# Course\n> Mine before.\n{COURSE['id']}\n> Mine after.\n", data)
    notes = re.findall(r'<div class="note[^"]*">.*?</div>', body(result), re.S)
    assert notes[0] == '<div class="note d0"><p>Mine before.</p></div>'
    assert notes[1].startswith(          # the user's order, 21.11.2026: what, how many, whose
        '<div class="note d0 playlist-note"><p>Playlist <a href="https://www.youtube.com/'
        f'playlist?list={COURSE["id"]}" target="_blank" rel="noopener noreferrer">Python '
        'podstawy podstaw</a> · 16 videos · Channel <a href="https://www.youtube.com/channel/'
        'UCw3L8lysSdQpNaeGLBqnr5A" target="_blank" rel="noopener noreferrer">getitjob</a></p>'
        '<p class="about">💡 Masz')
    # the description: each line a paragraph, blank lines left out (the user, 22.11.2026);
    # addresses clickable
    assert ('Twoim kodzie.</p><p class="about">🔗 Przetestuj AIdama: <a href="https://aidam.pl" '
            'target="_blank" rel="noopener noreferrer">https://aidam.pl</a></p></div>') in notes[1]
    assert notes[1].count('<p class="about">') == 3
    assert notes[2] == '<div class="note d0"><p>Mine after.</p></div>'   # after its videos
    assert body(result).index("Mine after.") > body(result).index('class="row')


def test_the_description_of_a_playlist_is_not_formatted(tmp_path):
    data = {PLAYLIST_ID: playlist("TESTVIDEO01", description="*not italic* and **not bold**")}
    assert '<p class="about">*not italic* and **not bold**</p>' in html(tmp_path, f"{PLAYLIST_ID}\n",
                                                                      data)
    # YouTube's text, not the user's: bare addresses stay links, [text](address) is not ours
    data = {PLAYLIST_ID: playlist("TESTVIDEO01",
                                  description="<b> & https://e.org [x](https://f.org)")}
    assert ('<p class="about">&lt;b&gt; &amp; <a href="https://e.org" target="_blank" '
            'rel="noopener noreferrer">https://e.org</a> [x](<a href="https://f.org" '
            'target="_blank" rel="noopener noreferrer">https://f.org</a>)</p>') in html(
        tmp_path, f"{PLAYLIST_ID}\n", data)


def test_the_description_of_a_playlist_without_blank_lines(tmp_path):
    # lines trimmed, empty ones and those of spaces left out, line ends of any kind
    data = {PLAYLIST_ID: playlist("TESTVIDEO01", description="  First  line \n\n \t \r\n"
                                                             "Second\r\n\n\nThird   x  \rFourth\n")}
    result = html(tmp_path, f"{PLAYLIST_ID}\n", data)
    assert ('<p class="about">First  line</p><p class="about">Second</p>'
            '<p class="about">Third   x</p><p class="about">Fourth</p></div>') in result
    assert "pre-wrap" not in index.INDEX_CSS               # no longer needed
    data = {PLAYLIST_ID: playlist("TESTVIDEO01", description=" \n\n ")}   # nothing left
    assert 'class="about"' not in html(tmp_path, f"{PLAYLIST_ID}\n", data)


def test_a_playlist_without_a_description_or_count(tmp_path):
    result = html(tmp_path, f"{PLAYLIST_ID}\n",
                  {PLAYLIST_ID: playlist("TESTVIDEO01", count=None, channel=None)})
    assert ('<div class="note d0 playlist-note"><p>Playlist <a href="' + PLAYLIST
            + '" target="_blank" rel="noopener noreferrer">Test playlist</a></p></div>'
            ) in result
    one = html(tmp_path, f"{PLAYLIST_ID}\n", {PLAYLIST_ID: playlist("TESTVIDEO01",
                                                                    channel_url=None)})
    assert "</a> · 1 video · Channel Channel</p>" in one        # a name without its link


def playlist_line(tmp_path, days: dict, count="same", entries=None) -> str:
    """The first line of a playlist's notes over pages published on `days`
    ({video id: "YYYY-MM-DD" or None for a page without the tag}); videos in
    `entries` but not in `days` have no page."""
    ids = entries or list(days)
    for video_id, day in days.items():
        page(tmp_path, video_id, published=day)
    data = playlist(*ids) | ({} if count == "same" else {"count": count})
    found = html(tmp_path, f"{PLAYLIST_ID}\n", {PLAYLIST_ID: data})
    return re.search(r'playlist-note"><p>(.*?)</p>', found).group(1)


def days_of(line: str) -> str | None:
    found = re.search(r'<span class="days">\((.*?)\)</span>', line)
    return found and found.group(1)


@pytest.mark.parametrize("days, entries, expected", [
    # oldest to newest, not first to last: the playlist is not in order
    ({"TESTVIDEO01": "2025-02-01", "TESTVIDEO02": "2024-05-03", "TESTVIDEO03": "2026-11-12"},
     None, "03.05.2024–12.11.2026"),
    ({"TESTVIDEO01": "2025-02-01"}, None, "01.02.2025"),                     # one video
    ({"TESTVIDEO01": "2025-02-01", "TESTVIDEO02": "2025-02-01"}, None, "01.02.2025"),  # one day
    # some days unknown: not downloaded, or a page without the tag
    ({"TESTVIDEO01": "2025-02-01", "TESTVIDEO02": "2024-05-03"},
     ["TESTVIDEO01", "TESTVIDEO02", "TESTVIDEO03"], "known range 03.05.2024–01.02.2025"),
    ({"TESTVIDEO01": "2025-02-01", "TESTVIDEO02": None}, None, "known 01.02.2025"),
    ({"TESTVIDEO01": "2025-02-01", "TESTVIDEO02": "13.11.2026"}, None, "known 01.02.2025"),
    ({"TESTVIDEO01": "2025-02-01"}, ["TESTVIDEO01", "TESTVIDEO02"], "known 01.02.2025"),
    ({"TESTVIDEO01": None}, ["TESTVIDEO01", "TESTVIDEO02"], None),          # none known
    # a video listed twice changes nothing
    ({"TESTVIDEO01": "2025-02-01"}, ["TESTVIDEO01", "TESTVIDEO01"], "01.02.2025"),
])
def test_the_span_of_a_playlists_publication_days(tmp_path, days, entries, expected):
    assert days_of(playlist_line(tmp_path, days, entries=entries)) == expected


def line_of(row_html: str) -> str:
    return re.search(r'<div class="line">(.*?)</div>', row_html).group(1)


def test_a_rows_line_starts_with_its_publication_day(tmp_path):
    """The user, 21.11.2026: the day first in the line, so the rest stays
    together; a row whose page does not tell it simply has none."""
    page(tmp_path, "TESTVIDEO01", published="2024-05-03")
    page(tmp_path, "TESTVIDEO02")                                    # no tag
    page(tmp_path, "TESTVIDEO03", published="03.05.2024")            # not readable
    video = tmp_path / "Page TESTVIDEO01 [TESTVIDEO01].webm"
    video.write_bytes(b"")
    (tmp_path / "own.webm").write_bytes(b"")
    found = rows(html(tmp_path, "TESTVIDEO01\nTESTVIDEO02\nTESTVIDEO03\nTESTVIDEO04\nown.webm\n"))
    assert line_of(found[0]).startswith("03.05.2024 · pl · <span title=")   # with its file
    without = rows(html(tmp_path, "!thumbnails: no\nTESTVIDEO01\n"))[0]
    assert line_of(without).startswith("03.05.2024 · 21:48 · pl · <span title=")  # duration
    assert line_of(found[1]).startswith("pl · ")
    assert line_of(found[2]).startswith("pl · ")
    assert "not downloaded" in line_of(found[3]) and "2024" not in found[3]
    assert not re.search(r"\d\d\.\d\d\.\d{4}", line_of(found[4]))  # own file: no page
    # only the page downloaded: the day too
    video.unlink()
    alone = rows(html(tmp_path, "!thumbnails: no\nTESTVIDEO01\n"))[0]
    assert line_of(alone).startswith("03.05.2024 · 21:48 · pl · <span class=\"miss\">only")


def test_no_dates(tmp_path):
    """!dates: no: neither the rows' days nor a playlist's span."""
    page(tmp_path, "TESTVIDEO01", published="2024-05-03")
    data = {PLAYLIST_ID: playlist("TESTVIDEO01")}
    found = html(tmp_path, f"!dates: no\n{PLAYLIST_ID}\n", data)
    assert "03.05.2024" not in found and 'class="days"' not in found
    assert line_of(rows(found)[0]).startswith("pl · ")
    assert "03.05.2024" in html(tmp_path, f"!dates: yes\n{PLAYLIST_ID}\n", data)


def test_details_of_ones_own_in_the_rows(tmp_path):
    """"= !date:" and "= !lang:" (the user, 22.11.2026): they win over the page's
    and give rows without a page what they lack, at the same places in the line."""
    page(tmp_path, "TESTVIDEO01", published="2024-05-03")             # page and file
    (tmp_path / "Page TESTVIDEO01 [TESTVIDEO01].webm").write_bytes(b"")
    page(tmp_path, "TESTVIDEO02")                                    # page only, no day
    (tmp_path / "Clip [TESTVIDEO03].webm").write_bytes(b"")           # file, no page
    (tmp_path / "own.webm").write_bytes(b"")
    text = ("= !date: 1.2.2020 !lang: en\nTESTVIDEO01\n= !date: 2021\nTESTVIDEO02\n"
            "= !date: 7.2022\n= !lang: de\nClip [TESTVIDEO03].webm\n"
            "= !date: 24.12.99\n= !lang: PL\nTESTVIDEO04\n"
            "= Holiday\n= !date: 03.08.2019 !lang: pl\nown.webm\n"
            "= !date: 2018\nmissing.mp4\nTESTVIDEO01\nown.webm\n")
    found = [line_of(row) for row in rows(html(tmp_path, text))]
    assert found[0].startswith("01.02.2020 · en · <span title=")
    assert found[1].startswith('2021 · pl · <span class="miss">only the page downloaded')
    assert found[2].startswith("07.2022 · de · <span title=")
    assert found[3].startswith('24.12.1999 · pl · <span class="miss">not downloaded')
    assert found[4].startswith("03.08.2019 · pl · <span title=")
    assert found[5].startswith('2018 · <span class="miss" title="missing.mp4">file not found')
    assert found[6].startswith("03.05.2024 · pl · ")                   # the same video, no details
    assert found[7].startswith("<span title=")                         # the same file, no details
    without = [line_of(row) for row in rows(html(tmp_path, "!thumbnails: no\n" + text))]
    assert without[0].startswith("01.02.2020 · 21:48 · en · ")        # the day before the duration
    hidden = [line_of(row) for row in rows(html(tmp_path, "!dates: no\n" + text))]
    assert hidden[0].startswith("en · ") and hidden[4].startswith("pl · ")
    assert not any(re.search(r"\d{4}", line) for line in hidden[:6])


def test_each_page_is_read_once_for_the_span_and_the_row(tmp_path, monkeypatch):
    read = []
    original = yt2html.read_page_metadata
    monkeypatch.setattr(yt2html, "read_page_metadata",
                        lambda path: read.append(path.name) or original(path))
    line = playlist_line(tmp_path, {"TESTVIDEO01": "2024-05-03", "TESTVIDEO02": "2026-11-12"})
    assert days_of(line) == "03.05.2024–12.11.2026"
    assert sorted(read) == ["Page TESTVIDEO01 [TESTVIDEO01].html",
                            "Page TESTVIDEO02 [TESTVIDEO02].html"]


def test_the_span_follows_the_count_in_grey(tmp_path):
    line = playlist_line(tmp_path, {"TESTVIDEO01": "2024-05-03", "TESTVIDEO02": "2026-11-12"})
    assert line.endswith('</a> · 2 videos <span class="days">(03.05.2024–12.11.2026)</span>'
                         ' · Channel <a href="https://www.youtube.com/@Channel" target="_blank" '
                         'rel="noopener noreferrer">Channel</a>')
    assert ".index .playlist-note .days{color:var(--muted)}" in index.INDEX_CSS
    # "known" against the videos YouTube listed, not its count (hidden videos count there)
    hidden = playlist_line(tmp_path, {"TESTVIDEO01": "2024-05-03"}, count=5)
    assert "· 5 videos <span class=\"days\">(03.05.2024)</span>" in hidden
    # no count: the span after the title
    alone = playlist_line(tmp_path, {"TESTVIDEO01": "2024-05-03"}, count=None)
    assert alone.startswith('Playlist <a href="') and alone.split(" · ")[0].endswith(
        '</a> <span class="days">(03.05.2024)</span>')


# The page -----------------------------------------------------------------------

def test_the_page(tmp_path):
    result = html(tmp_path, "# A & B\nTESTVIDEO01\n")
    assert '<meta name="yt2html:index" content="list">' in result
    assert "<title>A &amp; B</title>" in result and index.INDEX_CSS in result
    assert re.search(r'<footer class="footer">Index made with yt2html on \d\d\.\d\d\.\d{4}\.'
                     r'</footer>', result)
    assert "archived" not in body(result)


def test_the_player_is_shown_by_its_script(tmp_path):
    result = html(tmp_path, "TESTVIDEO01\n")
    # without JavaScript the panel stays hidden (the page player's rule) and the
    # index is a plain list; the script, the only one, opens it
    assert ".player{display:none}" in yt2html.CSS
    assert '<body>\n<aside class="player ix-player" aria-label="Video player">' in result
    assert result.count("<script>") == 1 and f"<script>{index.PLAYER_JS}</script>" in result
    assert "body.classList.add('player-open')" in index.PLAYER_JS


def test_the_player_panel():
    panel = index.render_player()
    controls = re.findall(r'<button type="button" data-do="(\w+)"[^>]*>([^<]*)</button>', panel)
    assert controls == [("previous", "|◀◀"), ("start", "|◀"), ("back", "−10 s"),
                        ("toggle", "▶"), ("forward", "+10 s"), ("end", "▶|"),
                        ("next", "▶▶|"), ("autoplay", "A")]
    assert '<div class="buttons ix-controls">' in panel
    assert ('data-do="previous" title="Previous file (Shift+P)" aria-label="Previous file"'
            in panel)
    assert 'data-do="next" title="Next file (Shift+N)" aria-label="Next file"' in panel
    assert 'data-do="toggle" class="main"' in panel
    assert ('data-do="autoplay" aria-pressed="false" title="Autoplay: play the next file when '
            'this one ends" aria-label="Autoplay">A</button>') in panel
    speeds = re.findall(r'data-speed="([\d.]+)" aria-pressed="(\w+)"', panel)
    assert speeds == [(v, str(v == "1").lower()) for v in yt2html.PLAYER_SPEEDS]
    assert '<div class="ix-failed" hidden><div>This file could not be played.</div>' in panel
    assert '<video preload="none"></video>' in panel
    # only the title under the buttons: no link, no transcript or audio rows
    assert panel.endswith('<div class="ix-playing"></div></aside>')
    assert "<a " not in panel and "follow" not in panel and "audio" not in panel.replace(
        "autoplay", "").replace("Autoplay", "")


def test_all_player_buttons_are_equal():
    """Eight buttons of 50 px, Play too; the row wraps, they never scale."""
    assert ".player.ix-player .ix-controls button{width:50px}" in index.INDEX_CSS
    assert ".player .buttons{display:flex;flex-wrap:wrap;gap:8px}" in yt2html.CSS


def test_the_playing_row_lights_only_its_thumbnail_and_title():
    assert ".index .row.now .name{color:var(--accent-text)}" in index.INDEX_CSS
    assert ".index .row.now .thumb{box-shadow:0 0 0 2px var(--accent)}" in index.INDEX_CSS
    assert ".row.now{" not in index.INDEX_CSS          # no background for the whole row


def test_no_weight_windows_shows_as_regular():
    """Segoe UI (Windows) has no 500: titles, numbers and durations at 500
    looked regular there; 600 is its Semibold, as on the video pages."""
    assert "font-weight:500" not in index.INDEX_CSS
    assert ".index .name{font-weight:600;" in index.INDEX_CSS
    assert ".index .row .num{font-weight:600}" in index.INDEX_CSS


def test_the_index_uses_only_colour_variables():
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", index.INDEX_CSS)


def test_the_kept_playlists_cannot_close_their_element(tmp_path):
    data = {PLAYLIST_ID: playlist("TESTVIDEO01", description="</script><b>x</b>")}
    result = html(tmp_path, f"{PLAYLIST_ID}\n", data)
    assert result.count("</script>") == 2          # the data and the player
    (tmp_path / "series.html").write_text(result, encoding="utf-8")
    assert index.kept_playlists(tmp_path / "series.html") == data


def test_kept_playlists_of_no_index(tmp_path):
    assert index.kept_playlists(tmp_path / "none.html") == {}
    (tmp_path / "broken.html").write_text(
        f'<script type="application/json" id="{index.DATA_ID}">{{nope</script>', encoding="utf-8")
    assert index.kept_playlists(tmp_path / "broken.html") == {}


# Durations of one's own files ------------------------------------------------------

def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mp4(path: Path, seconds: float, version: int = 0, moov_last: bool = True,
        scale: int = 1000, large: bool = False) -> Path:
    if version == 1:
        mvhd = bytes([1, 0, 0, 0]) + bytes(16) + struct.pack(">IQ", scale, int(seconds * scale))
    else:
        mvhd = bytes(4) + bytes(8) + struct.pack(">II", scale, int(seconds * scale))
    moov = box(b"moov", box(b"udta", b"x" * 5) + box(b"mvhd", mvhd + bytes(80)))
    media = (struct.pack(">I4sQ", 1, b"mdat", 16 + 300) + b"\0" * 300 if large  # 64-bit size
             else box(b"mdat", b"\0" * 300))
    parts = [box(b"ftyp", b"isom0000"), media, moov] if moov_last else \
        [box(b"ftyp", b"isom0000"), moov, media]
    path.write_bytes(b"".join(parts))
    return path


def ebml(element: int, payload: bytes, size: bytes | None = None) -> bytes:
    """An EBML element: its id, its size (8 bytes, unless given) and data."""
    head = element.to_bytes((element.bit_length() + 7) // 8, "big")
    return head + (size or (0x0100000000000000 | len(payload)).to_bytes(8, "big")) + payload


def webm(path: Path, duration: float, scale: int | None = None, double: bool = True,
         unknown_size: bool = False, cluster_first: bool = False) -> Path:
    info = b""
    if scale:
        info += ebml(0x2AD7B1, scale.to_bytes(4, "big"))
    info += ebml(0x4489, struct.pack(">d" if double else ">f", duration))
    children = ebml(0x114D9B74, b"\0" * 10)                        # SeekHead, skipped
    cluster = ebml(0x1F43B675, b"\0" * 20)
    children += (cluster + ebml(0x1549A966, info)) if cluster_first else (ebml(0x1549A966, info)
                                                                          + cluster)
    segment = ebml(0x18538067, children, b"\x01\xff\xff\xff\xff\xff\xff\xff" if unknown_size
                   else None)
    path.write_bytes(ebml(0x1A45DFA3, b"\x42\x86\x81\x01") + segment)
    return path


@pytest.mark.parametrize("make, seconds", [
    (lambda p: mp4(p / "a.mp4", 7.5), 7.5),
    (lambda p: mp4(p / "a.m4v", 3725, version=1), 3725),
    (lambda p: mp4(p / "a.mp4", 61, moov_last=False), 61),
    (lambda p: mp4(p / "a.mp4", 42, large=True), 42),
    (lambda p: webm(p / "a.webm", 65200.0), 65.2),               # in milliseconds (default)
    (lambda p: webm(p / "a.mkv", 2.5, scale=1_000_000_000, double=False), 2.5),
    (lambda p: webm(p / "a.webm", 1000.0, unknown_size=True), 1.0),
])
def test_duration_from_the_header(tmp_path, make, seconds):
    assert index.file_duration(make(tmp_path)) == pytest.approx(seconds)


def written(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("make", [
    lambda p: webm(p / "a.webm", 1000.0, cluster_first=True),     # no Info before the media
    lambda p: written(p / "a.mp4", b"\0\0\0\x10mdat" + bytes(8)),   # no moov
    lambda p: written(p / "a.mkv", b"\0garbage"),
    lambda p: written(p / "a.mp4", box(b"moov", box(b"trak", b""))),  # moov without mvhd
    lambda p: written(p / "a.webm", b""),
    lambda p: mp4(p / "a.mp4", 5, scale=0),                         # broken: no time scale
    lambda p: p / "missing.mp4",
])
def test_no_duration(tmp_path, make):
    assert index.file_duration(make(tmp_path)) is None


# Playlists read from YouTube or kept ------------------------------------------------

def test_the_playlist_is_read_each_time_and_kept(tmp_path, youtube, capsys):
    listing = write_list(tmp_path, f"{PLAYLIST_ID}\n")
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path)]) == 0
    kept = index.kept_playlists(tmp_path / "series.html")
    assert kept[PLAYLIST_ID] | {"read": ""} == {
        "title": "Test playlist", "channel": "Channel",
        "channel_url": "https://www.youtube.com/channel/UCx", "count": 3,   # by id, not @Channel
        "description": "About the playlist.", "entries": ["TESTVIDEO01", "TESTVIDEO02"],
        "read": ""}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", kept[PLAYLIST_ID]["read"])
    youtube.playlists[PLAYLIST_ID] = [entry(3)]             # changed on YouTube
    yt2html.main(["--index-only", listing, "-o", str(tmp_path)])
    assert index.kept_playlists(tmp_path / "series.html")[PLAYLIST_ID]["entries"] == ["TESTVIDEO03"]
    assert len(youtube.lists_read) == 2 and youtube.fetched == []


def test_without_youtube_the_kept_playlist_is_used(tmp_path, youtube, capsys):
    listing = write_list(tmp_path, f"{PLAYLIST_ID}\n")
    yt2html.main(["--index-only", listing, "-o", str(tmp_path)])
    youtube.playlists[PLAYLIST_ID] = "ERROR: [youtube:tab] x: Unable to download (offline)"
    capsys.readouterr()
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert re.search(rf"Playlist {PLAYLIST_ID} could not be read \(Unable to download \(offline\)\); "
                     r"using the list kept in the index \(read \d\d\.\d\d\.\d{4} \d\d:\d\d\)\.", out)
    result = (tmp_path / "series.html").read_text(encoding="utf-8")
    assert len(rows(result)) == 2 and "Test playlist" in result


def test_a_playlist_never_read(tmp_path, youtube, capsys):
    youtube.playlists = {}
    listing = write_list(tmp_path, f"TESTVIDEO01\n{PLAYLIST_ID}\n")
    for _ in range(2):          # the index keeps nothing for it, so neither does the next run
        assert yt2html.main(["--index-only", listing, "-o", str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert f"FAILED: playlist {PLAYLIST}: offline" in out
        assert "playlist could not be read" in (tmp_path / "series.html").read_text("utf-8")


def test_a_playlist_listed_twice_is_read_once(tmp_path, youtube):
    listing = write_list(tmp_path, f"# Twice\n{PLAYLIST_ID}\n{PLAYLIST}\n")
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path)]) == 0
    assert youtube.lists_read == [PLAYLIST]
    assert len(rows((tmp_path / "series.html").read_text("utf-8"))) == 4


# --index and --index-only in a run ---------------------------------------------------

def test_index_downloads_what_is_missing_and_makes_the_index(tmp_path, youtube, capsys):
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    page(out_dir, "TESTVIDEO03")
    listing = write_list(tmp_path, f"# Series\nTESTVIDEO03\n{PLAYLIST_ID}\nMy clip.mp4\n"
                                   "https://youtu.be/TESTVIDEO01\n")
    assert yt2html.main(["--index", listing, "-o", str(out_dir)]) == 0
    out = capsys.readouterr().out
    # the playlist first (before any download), each video once, own files never
    assert youtube.lists_read == [PLAYLIST]
    assert youtube.fetched == [url(1), url(2)]
    assert "Skipped (page exists)" in out
    assert "Done: 2 saved, 1 skipped (page exists), 0 failed." in out
    assert f"Saved index: {out_dir / 'series.html'}" in out
    assert "  line 4: file not found: My clip.mp4" in out
    result = (out_dir / "series.html").read_text(encoding="utf-8")
    assert ">Original title 1</a>" in result and ">Original title 2</a>" in result
    assert blocks(result) == ["row d0 1", "h2 d0 2 Test playlist", "note d0", "row d1 1",
                              "row d1 2", "row d0 3", "row d0 4"]


def test_take_with_an_index_writes_it_after_each_part(tmp_path, youtube, capsys):
    listing = write_list(tmp_path, f"# Series\n{PLAYLIST_ID}\nhttps://youtu.be/TESTVIDEO01\n")
    assert yt2html.main(["--index", listing, "-o", str(tmp_path), "--take", "1"]) == 0
    out = capsys.readouterr().out
    assert youtube.fetched == [url(1)]
    assert "Done: 1 saved, 0 skipped (page exists), 0 failed, 1 left for the next run." in out
    assert f"Saved index: {tmp_path / 'series.html'}" in out
    assert "not downloaded" in (tmp_path / "series.html").read_text(encoding="utf-8")
    assert yt2html.main(["--index", listing, "-o", str(tmp_path), "--take", "1"]) == 0
    assert youtube.fetched == [url(1), url(2)]
    assert "left for the next run" not in capsys.readouterr().out


def test_the_index_theme_comes_from_the_list_and_the_pages_theme_from_page(tmp_path, youtube):
    """!theme for the index, --page theme: for the video pages, apart."""
    listing = write_list(tmp_path, "!theme: light\nhttps://youtu.be/TESTVIDEO01\n")
    assert yt2html.main(["--index", listing, "-o", str(tmp_path), "--page", "theme:dark"]) == 0
    index = (tmp_path / "series.html").read_text(encoding="utf-8")
    video = next(p for p in tmp_path.glob("*TESTVIDEO01*.html")).read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light">' in index
    assert '<meta name="color-scheme" content="dark">' in video
    # without !theme: auto, whatever the pages have
    listing = write_list(tmp_path, "https://youtu.be/TESTVIDEO01\n")
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path)]) == 0
    assert ('<meta name="color-scheme" content="dark light">'
            in (tmp_path / "series.html").read_text(encoding="utf-8"))


def test_videos_of_a_playlist_know_their_playlist(tmp_path, youtube):
    listing = write_list(tmp_path, f"{PLAYLIST_ID}\n")
    yt2html.main(["--index", listing, "-o", str(tmp_path), "--json", "write"])
    saved = json.loads(next(tmp_path.glob("*TESTVIDEO02*.info.json")).read_text("utf-8"))
    assert saved[yt2html.PLAYLIST_KEY] == {"id": PLAYLIST_ID, "title": "Test playlist",
                                           "position": 2, "count": 2}


def test_a_failed_video_is_only_in_the_console(tmp_path, youtube, capsys):
    youtube.failing = {"TESTVIDEO02": "ERROR: [youtube] TESTVIDEO02: Private video. Sign in"}
    listing = write_list(tmp_path, "TESTVIDEO01\nTESTVIDEO02\n")
    assert yt2html.main(["--index", listing, "-o", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"FAILED: {url(2)}" in out and "  line 2: TESTVIDEO02 not downloaded\n" in out
    result = (tmp_path / "series.html").read_text(encoding="utf-8")
    assert "Private video" not in result and "not downloaded" in result


def test_an_unreadable_playlist_counts_as_failed(tmp_path, youtube, capsys):
    youtube.playlists = {}
    listing = write_list(tmp_path, f"{PLAYLIST_ID}\nTESTVIDEO01\n")
    assert yt2html.main(["--index", listing, "-o", str(tmp_path)]) == 1
    assert "Done: 1 saved, 0 skipped (page exists), 1 failed." in capsys.readouterr().out


def test_ctrl_c_still_makes_the_index(tmp_path, youtube, capsys):
    youtube.interrupt = {"TESTVIDEO02"}
    listing = write_list(tmp_path, "TESTVIDEO01\nTESTVIDEO02\nTESTVIDEO03\n")
    assert yt2html.main(["--index", listing, "-o", str(tmp_path)]) == 130
    out = capsys.readouterr().out
    assert "Interrupted (Ctrl+C)" in out and "Saved index" in out
    assert len(rows((tmp_path / "series.html").read_text(encoding="utf-8"))) == 3


@pytest.mark.parametrize("option", ["--index", "--index-only"])
def test_ctrl_c_while_reading_the_playlists(tmp_path, youtube, capsys, monkeypatch, option):
    """Before any download: the usual message, the earlier index untouched."""
    def interrupted(source, args):
        raise KeyboardInterrupt
    monkeypatch.setattr(yt2html, "fetch_playlist", interrupted)
    (tmp_path / "series.html").write_text("earlier index", encoding="utf-8")
    listing = write_list(tmp_path, f"TESTVIDEO01\n{PLAYLIST_ID}\n")
    assert yt2html.main([option, listing, "-o", str(tmp_path)]) == 130
    out = capsys.readouterr().out
    assert ("Interrupted (Ctrl+C) while reading the playlists: nothing was downloaded and the "
            "index was not changed.") in out
    assert youtube.fetched == []
    assert (tmp_path / "series.html").read_text(encoding="utf-8") == "earlier index"


def test_index_only_downloads_nothing(tmp_path, youtube, capsys):
    page(tmp_path, "TESTVIDEO01")
    listing = write_list(tmp_path, "!theme: light\nTESTVIDEO01\nTESTVIDEO02\n")
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path),
                         "--cookies", "firefox"]) == 0
    out = capsys.readouterr().out
    assert youtube.fetched == [] and "Done:" not in out
    assert "  line 3: TESTVIDEO02 not downloaded (--index downloads it)" in out
    result = (tmp_path / "series.html").read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light">' in result


def test_the_output_folder_is_made(tmp_path, youtube):
    listing = write_list(tmp_path, "TESTVIDEO01\n")
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path / "new" / "pages")]) == 0
    assert (tmp_path / "new" / "pages" / "series.html").is_file()


def test_an_index_that_cannot_be_written(tmp_path, youtube, capsys):
    listing = write_list(tmp_path, "TESTVIDEO01\n")
    (tmp_path / "series.html").mkdir()               # a folder in the way
    assert yt2html.main(["--index-only", listing, "-o", str(tmp_path)]) == 1
    assert f"FAILED: index {tmp_path / 'series.html'}: " in capsys.readouterr().out


def test_load_json_with_a_list(tmp_path, youtube, capsys, saved):
    folder = saved.parent
    listing = write_list(tmp_path, "TESTVIDEO01\nTESTVIDEO02\n")
    assert yt2html.main(["--json", "load", "--index", listing, "-o", str(folder)]) == 0
    out = capsys.readouterr().out
    assert youtube.fetched == [] and f"Reading {saved}" in out
    assert "No saved data for TESTVIDEO02; skipped." in out
    assert "Done: 1 saved, 0 skipped (page exists), 0 failed, 1 without saved data." in out
    assert (folder / "series.html").is_file()


# Options ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv, message", [
    (["--index", "a.txt", "--index-only", "a.txt"], "use one of them"),
    (["--index", "a.txt", url(1)], f"--index takes the videos from the list file; add {url(1)} "
                                   "to the file instead of giving it in the command"),
    (["--index-only", "a.txt", PLAYLIST], "--index-only takes the videos from the list file"),
    (["--index-only", "a.txt", "--page", "theme:dark", "--json", "load"],
     "--index-only downloads nothing and only makes the index, so it cannot be used with "
     "--json, --page (use --index FILE for that)"),
    (["--index-only", "a.txt", "--transcript"], "cannot be used with --transcript"),
    (["--index-only", "a.txt", "--page", "comments:0"], "cannot be used with --page"),
    (["--index-only", "a.txt", "--page", "theme:dark"], "cannot be used with --page"),
    (["--index-only", "a.txt", "--json", "refresh"], "cannot be used with --json ("),
    (["--index-only", "a.txt", "--take", "all"], "cannot be used with --take ("),
    (["--index"], "argument --index: expected one argument"),
])
def test_index_option_errors(capsys, argv, message):
    with pytest.raises(SystemExit) as stop:
        yt2html.parse_args(argv)
    assert stop.value.code == 2 and message in capsys.readouterr().err


@pytest.mark.parametrize("name, refused", [(name, key) for name, key in
                                           yt2html.INDEX_ONLY_REFUSES])
def test_every_refused_option(capsys, name, refused):
    value = {"page": ["--page", "comments:5"], "video": ["--video"],
             "transcript": ["--transcript"], "json": ["--json", "load"],
             "olang": ["--olang", "en"], "take": ["--take", "5"]}.get(refused, [name])
    with pytest.raises(SystemExit):
        yt2html.parse_args(["--index-only", "a.txt", *value])
    assert f"cannot be used with {name} (" in capsys.readouterr().err


def test_a_bad_list_stops_before_anything_is_downloaded(tmp_path, youtube, capsys):
    listing = write_list(tmp_path, "TESTVIDEO01\nnot a video\n")
    with pytest.raises(SystemExit) as stop:
        yt2html.main(["--index", listing, "-o", str(tmp_path)])
    assert stop.value.code == 2
    assert f"{listing}, line 2: not a video, playlist or video file" in capsys.readouterr().err
    assert youtube.fetched == [] and youtube.lists_read == []


def test_index_works_with_the_json_options(tmp_path, youtube):
    listing = write_list(tmp_path, "TESTVIDEO01\n")
    for parameter in ("write", "refresh", "load"):
        assert yt2html.parse_args(["--index", listing, "--json", parameter]).list.children


# --reverse (unofficial, the user's 20.11.2026: not in --help or the README) ----------

REVERSE_LIST = (f"# Series\n> Intro, see https://www.youtube.com/watch?v=OTHERVIDE01\n"
                f"## Part\n= My title\nTESTVIDEO01\n"
                f"> A note naming https://www.youtube.com/watch?v=OTHERVIDE02\n"
                f"{PLAYLIST_ID}\n### Sub\nMój klip & co.webm\n"
                f"> see https://www.youtube.com/watch?v=OTHERVIDE03\n"
                f"Missing & lost.mkv\nTESTVIDEO04\n"
                f"TESTVIDEO05\nTESTVIDEO01\nPLtest0987654321\n")
REVERSED = ["TESTVIDEO01", "TESTVIDEO02", "TESTVIDEO03", "Mój klip & co.webm",
            "Missing & lost.mkv", "TESTVIDEO04", "TESTVIDEO05", "TESTVIDEO01", "PLtest0987654321"]


def reverse_folder(tmp_path):
    page(tmp_path, "TESTVIDEO01")
    page(tmp_path, "TESTVIDEO02")
    page(tmp_path, "TESTVIDEO04", unavailable="removed")   # no YouTube link: its page's name
    (tmp_path / "Clip [TESTVIDEO03].mp4").write_bytes(b"x")  # a file, no page
    shutil.copy(Path(__file__).parent / "data" / "short.webm", tmp_path / "Mój klip & co.webm")
    return {PLAYLIST_ID: playlist("TESTVIDEO02", "TESTVIDEO03"), "PLtest0987654321": None}


@pytest.mark.parametrize("settings", ["", "!numbers: no\n!layout: cards\n",
                                      "!thumbnails: no\n!layout: compact\n"])
def test_reverse_lines_are_the_videos_in_order(tmp_path, settings):
    playlists = reverse_folder(tmp_path)
    result = html(tmp_path, settings + REVERSE_LIST, playlists)
    assert index.reverse_lines(result) == REVERSED      # links in notes are not videos
    assert "no longer on YouTube" in result and "file not found" in result
    assert "playlist could not be read" in result


def test_reverse_writes_a_list_file_that_reads_back(tmp_path, capsys):
    playlists = reverse_folder(tmp_path)
    (tmp_path / "series.html").write_text(html(tmp_path, REVERSE_LIST, playlists), "utf-8")
    target = tmp_path / "back.txt"
    assert yt2html.main(["--reverse", str(tmp_path / "series.html"), str(target)]) == 0
    assert target.read_bytes() == ("\n".join(REVERSED) + "\n").encode("utf-8")   # no BOM
    assert f"Saved list: {target} (9 videos)" in capsys.readouterr().out
    listing = index.read_list_file(str(target))
    assert [row.ref for row in listing.children] == [
        "TESTVIDEO01", "TESTVIDEO02", "TESTVIDEO03", "Mój klip & co.webm", "Missing & lost.mkv",
        "TESTVIDEO04", "TESTVIDEO05", "TESTVIDEO01", "PLtest0987654321"]
    # the name taken: "-1" before the extension; both taken: an error, nothing written
    assert yt2html.main(["--reverse", str(tmp_path / "series.html"), str(target)]) == 0
    other = tmp_path / "back-1.txt"
    assert other.read_text("utf-8") == target.read_text("utf-8")
    assert f"Note: {target} exists, so the list went to {other}." in capsys.readouterr().out
    with pytest.raises(SystemExit):
        yt2html.main(["--reverse", str(tmp_path / "series.html"), str(target)])
    assert f"'{target}' and '{other}' exist; give another name" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.glob("back*")) == ["back-1.txt", "back.txt"]


def test_reverse_makes_a_missing_folder(tmp_path, capsys):
    playlists = reverse_folder(tmp_path)
    (tmp_path / "series.html").write_text(html(tmp_path, REVERSE_LIST, playlists), "utf-8")
    target = tmp_path / "new" / "deeper" / "back.txt"
    assert yt2html.main(["--reverse", str(tmp_path / "series.html"), str(target)]) == 0
    assert target.read_text("utf-8").splitlines() == REVERSED


REVERSED_DETAILS = """\
= Mine
= !date: 03.08.2019 !lang: en
Mój klip & co.webm

= My title
= !date: 03.05.2024 !lang: pl
TESTVIDEO01

= Title of TESTVIDEO02
= !lang: pl
TESTVIDEO02

= Clip
TESTVIDEO03

= Mój klip & co
Mój klip & co.webm

= Missing & lost
Missing & lost.mkv

= Title of TESTVIDEO04
= !lang: pl
TESTVIDEO04

TESTVIDEO05

= Title of TESTVIDEO01
= !date: 03.05.2024 !lang: pl
TESTVIDEO01

PLtest0987654321
"""


def test_reverse_details_write_what_the_rows_show(tmp_path, capsys):
    """The user, 22.11.2026: each video under "= title" and "= !date: … !lang: …"
    with what its row shows (the user knows which were his own); nothing for
    what a row does not show; a row showing only the id has no title."""
    playlists = reverse_folder(tmp_path)
    page(tmp_path, "TESTVIDEO01", published="2024-05-03")
    text = "= Mine\n= !date: 3.8.19 !lang: EN\nMój klip & co.webm\n" + REVERSE_LIST
    (tmp_path / "series.html").write_text(html(tmp_path, text, playlists), "utf-8")
    target = tmp_path / "back.txt"
    assert yt2html.main(["--reverse-details", str(tmp_path / "series.html"), str(target)]) == 0
    assert target.read_bytes() == REVERSED_DETAILS.encode("utf-8")       # no BOM, LF
    assert f"Saved list: {target} (10 videos)" in capsys.readouterr().out
    # it reads back, and the index made from it shows the same
    again = html(tmp_path, target.read_text("utf-8"), playlists)
    assert index.reverse_details(again) == REVERSED_DETAILS.splitlines()
    # !dates: no: the index shows no dates, so none are written
    (tmp_path / "plain.html").write_text(html(tmp_path, "!dates: no\n" + text, playlists), "utf-8")
    assert yt2html.main(["--reverse-details", str(tmp_path / "plain.html"),
                         str(tmp_path / "plain.txt")]) == 0
    written = (tmp_path / "plain.txt").read_text("utf-8")
    assert "!date" not in written and "= !lang: en\nMój klip" in written


@pytest.mark.parametrize("settings", ["!numbers: no\n!layout: cards\n",
                                      "!thumbnails: no\n!layout: compact\n"])
def test_reverse_details_in_every_layout(tmp_path, settings):
    playlists = reverse_folder(tmp_path)
    page(tmp_path, "TESTVIDEO01", published="2024-05-03")
    text = "= Mine\n= !date: 3.8.19 !lang: EN\nMój klip & co.webm\n" + REVERSE_LIST
    assert (index.reverse_details(html(tmp_path, settings + text, playlists))
            == REVERSED_DETAILS.splitlines())


@pytest.mark.parametrize("make, args, message", [
    (None, ["series.htm", "x.txt"], "--reverse takes the index page itself, e.g. index.html"),
    (None, ["none.html", "x.txt"], "cannot read index page"),
    ("<html><main class=\"page\">a video page</main>", ["page.html", "x.txt"],
     "'{page}' is not an index page made by yt2html"),
    ('<main class="page index layout-plain">\n<h1 class="title">T</h1>\n</main>',
     ["page.html", "x.txt"], "no videos in"),
])
def test_reverse_errors(tmp_path, capsys, make, args, message):
    if make:
        (tmp_path / args[0]).write_text(make, "utf-8")
    with pytest.raises(SystemExit):
        yt2html.main(["--reverse", *(str(tmp_path / a) for a in args)])
    assert message.format(page=tmp_path / args[0]) in capsys.readouterr().err   # "\\" on Windows
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.parametrize("extra, named", [(["URL"], "a URL"), (["-o", "out"], "-o"),
                                          (["--page", "theme:dark"], "--page"),
                                          (["--page"], "--page")])
def test_reverse_takes_nothing_else_and_is_not_in_help(capsys, extra, named):
    for option in ("--reverse", "--reverse-details"):
        with pytest.raises(SystemExit):
            yt2html.main([option, "a.html", "b.txt", *extra])
        assert (f"{option} only writes the list of an index page, so it cannot be used with "
                f"{named}") in capsys.readouterr().err
    with pytest.raises(SystemExit):
        yt2html.main(["--help"])
    assert "--reverse" not in capsys.readouterr().out


def test_the_two_reverses_do_not_go_together(tmp_path, capsys):
    with pytest.raises(SystemExit):
        yt2html.main(["--reverse", "a.html", "b.txt", "--reverse-details", "a.html", "c.txt"])
    assert "--reverse only writes the list of an index page, so it cannot be used with " \
           "--reverse-details" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        yt2html.main(["--reverse-details", "a.htm", "b.txt"])
    assert "--reverse-details takes the index page itself" in capsys.readouterr().err
