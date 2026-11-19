"""The list file of --index FILE / --index-only FILE: its format and checks."""

from pathlib import Path

import pytest

import yt2html_index as index
from yt2html_index import Chapter, Item

PLAYLIST_ID = "PLtest1234567890"


def read(tmp_path, text: str, name="list.txt", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return index.read_list_file(str(path))


def error(tmp_path, text: str, name="list.txt") -> str:
    with pytest.raises(index.ListFileError) as caught:
        read(tmp_path, text, name)
    return str(caught.value)


def kinds(children) -> list:
    return [(c.kind, c.ref) if isinstance(c, Item) else c for c in children]


# The simplest list ------------------------------------------------------------

def test_the_simplest_list_has_no_syntax(tmp_path):
    listing = read(tmp_path, "TESTVIDEO01\n\n  https://youtu.be/TESTVIDEO02  \n\t\n")
    assert kinds(listing.children) == [("video", "TESTVIDEO01"), ("video", "TESTVIDEO02")]
    assert listing.title == ""
    assert listing.settings == {"numbers": "yes", "thumbnails": "yes",
                                "layout": "plain"}                     # the defaults
    assert [i.line for i in listing.items()] == [1, 3]


def test_byte_order_mark_and_windows_line_ends(tmp_path):
    listing = read(tmp_path, "# Title\r\nTESTVIDEO01\r\n", encoding="utf-8-sig")
    assert listing.title == "Title" and kinds(listing.children) == [("video", "TESTVIDEO01")]


def test_a_list_without_elements_is_no_error(tmp_path):
    assert read(tmp_path, "# Only a title\n// and a comment\n").children == []


@pytest.mark.parametrize("name, page", [
    ("seria.txt", "seria.html"), ("seria.md", "seria.html"), ("seria", "seria.html"),
    ("my.list.txt", "my.list.html"),
])
def test_the_index_is_named_after_the_list(name, page):
    assert index.index_path(f"D:/Lists/{name}", Path("out")) == Path("out") / page


@pytest.mark.parametrize("text, message", [
    (None, "cannot read list file"),
])
def test_unreadable_list_file(tmp_path, text, message):
    with pytest.raises(index.ListFileError, match=message):
        index.read_list_file(str(tmp_path / "missing.txt"))


def test_list_file_that_is_not_utf8(tmp_path):
    path = tmp_path / "list.txt"
    path.write_bytes("// zażółć\n".encode("cp1250"))
    with pytest.raises(index.ListFileError, match="is not UTF-8 text"):
        index.read_list_file(str(path))


@pytest.mark.parametrize("name", ["seria.html", "Seria.HTM"])
def test_a_page_is_not_a_list(tmp_path, name):
    (tmp_path / name).write_text("TESTVIDEO01", encoding="utf-8")
    with pytest.raises(index.ListFileError, match="is a page; give the list file"):
        index.read_list_file(str(tmp_path / name))


# Lines with links ------------------------------------------------------------

@pytest.mark.parametrize("line, found", [
    ("https://www.youtube.com/watch?v=TESTVIDEO01", ("video", "TESTVIDEO01")),
    ("https://youtu.be/TESTVIDEO01?si=x", ("video", "TESTVIDEO01")),
    ("https://www.youtube.com/shorts/TESTVIDEO01", ("video", "TESTVIDEO01")),
    ("www.youtube.com/watch?v=TESTVIDEO01", ("video", "TESTVIDEO01")),
    # a video in a playlist is the video, as in yt-dlp
    (f"https://www.youtube.com/watch?v=TESTVIDEO01&list={PLAYLIST_ID}", ("video", "TESTVIDEO01")),
    (f"https://www.youtube.com/playlist?list={PLAYLIST_ID}", ("playlist", PLAYLIST_ID)),
    (PLAYLIST_ID, ("playlist", PLAYLIST_ID)),
    ("Some title [TESTVIDEO01].html", ("video", "TESTVIDEO01")),
    ("Some title [TESTVIDEO01].mp4", ("video", "TESTVIDEO01")),     # a video file: its video
    (r"D:\Videos\Some title [TESTVIDEO01].html", ("video", "TESTVIDEO01")),
    ("TESTVIDEO01", ("video", "TESTVIDEO01")),
    ("-ESTVIDEO01", ("video", "-ESTVIDEO01")),                       # ids may start with "-"
    ("My holiday.mp4", ("file", "My holiday.mp4")),
    ("clip.WEBM", ("file", "clip.WEBM")),
    ("clip.m4v", ("file", "clip.m4v")),
    ("clip.mkv", ("file", "clip.mkv")),
])
def test_what_a_line_refers_to(line, found):
    assert index.link_line(line) == found


@pytest.mark.parametrize("line", ["clip.avi", "Videos/clip.mp4", r"Videos\clip.mp4",
                                  "song.mp3", "hello world", "TESTVIDEO0"])
def test_lines_that_are_not_understood(line):
    assert index.link_line(line) is None


def test_a_line_that_is_not_understood_stops_with_its_number(tmp_path):
    message = error(tmp_path, "TESTVIDEO01\n\nhello world\n")
    assert message.endswith("list.txt, line 3: not a video, playlist or video file: hello world "
                            "(comments start with //)")


@pytest.mark.parametrize("line, message", [
    ("https://www.youtube.com/@channel", "a channel address"),
    ("https://example.org/video", "not the address of a YouTube video or playlist"),
])
def test_addresses_that_are_no_video_or_playlist(tmp_path, line, message):
    assert f"line 1: {message}" in error(tmp_path, line + "\n")


# Comments, title, chapters ---------------------------------------------------------

def test_comments(tmp_path):
    listing = read(tmp_path, "// a comment\n  // indented\nTESTVIDEO01 \n//TESTVIDEO02\n")
    assert kinds(listing.children) == [("video", "TESTVIDEO01")]


def test_title(tmp_path):
    assert read(tmp_path, "#   My series  \nTESTVIDEO01\n").title == "My series"
    assert read(tmp_path, "#\nTESTVIDEO01\n").title == ""            # empty: the file's name


def test_a_second_title_is_an_error(tmp_path):
    assert error(tmp_path, "# One\n#\n").endswith(
        "line 2: a second title (the first is on line 1); chapters are ## to ####, "
        "comments start with //")


@pytest.mark.parametrize("line", ["#Title", "##Part", "#1 favourite"])
def test_a_hash_needs_a_space(tmp_path, line):
    assert "line 1: not a title or chapter" in error(tmp_path, line)


def test_chapters_and_their_levels(tmp_path):
    listing = read(tmp_path, "## One\nTESTVIDEO01\n### One.one\nTESTVIDEO02\n#### Deep\n"
                             "## Two\n####\nTESTVIDEO03\n")
    one, two = listing.children
    assert (one.level, one.text, one.line) == (1, "One", 1)
    assert kinds(one.children)[0] == ("video", "TESTVIDEO01")
    sub = one.children[1]
    assert (sub.level, sub.text) == (2, "One.one")
    assert isinstance(sub.children[1], Chapter) and sub.children[1].level == 3
    # a skipped level stays as written, an empty heading too
    assert two.children[0].level == 3 and two.children[0].text == ""
    assert kinds(two.children[0].children) == [("video", "TESTVIDEO03")]


def test_a_chapter_closes_deeper_ones(tmp_path):
    listing = read(tmp_path, "## A\n### A1\n## B\nTESTVIDEO01\n")
    assert [c.text for c in listing.children] == ["A", "B"]
    assert kinds(listing.children[1].children) == [("video", "TESTVIDEO01")]


def test_no_fourth_level(tmp_path):
    assert error(tmp_path, "TESTVIDEO01\n##### Too deep\n").endswith(
        "line 2: too deep: chapters go from ## to #### (three levels)")


# Settings -----------------------------------------------------------------------

def test_settings_in_the_header(tmp_path):
    """"!key: value"; any case, the space after ":" optional."""
    listing = read(tmp_path, "!numbers:no\n# Title\n> An introduction.\n// comment\n\n"
                             "! Thumbnails : No\n!LAYOUT: Cards\nTESTVIDEO01\n")
    assert listing.settings == {"numbers": "no", "thumbnails": "no", "layout": "cards"}
    listing = read(tmp_path, "!numbers: yes\n!layout: compact\nTESTVIDEO01\n")
    assert listing.settings == {"numbers": "yes", "thumbnails": "yes", "layout": "compact"}


@pytest.mark.parametrize("text, message", [
    ("!tumbnails: no\n", "line 1: unknown setting: !tumbnails: no "
                         "(known: !numbers, !thumbnails, !layout)"),
    ("!no-numbers\n", "line 1: unknown setting: !no-numbers (known: !numbers, !thumbnails, "
                      "!layout)"),
    ("!layout: boxes\n", "line 1: !layout: boxes: !layout is one of: plain, compact, cards "
                         "(e.g. !layout: cards)"),
    ("!numbers: maybe\n", "line 1: !numbers: maybe: !numbers is one of: yes, no "
                          "(e.g. !numbers: no)"),
    ("!numbers\n", "line 1: !numbers: !numbers is one of: yes, no"),
    ("!numbers: no\n!Numbers: yes\n", "line 2: !numbers is given twice"),
    ("TESTVIDEO01\n!numbers: no\n", "line 2: !numbers: no: settings go at the top of the file"),
    ("## Part\n!thumbnails: no\n", "line 2: !thumbnails: no: settings go at the top"),
])
def test_setting_errors(tmp_path, text, message):
    assert message in error(tmp_path, text)


# Notes -------------------------------------------------------------------------

def test_notes_under_a_video_belong_to_it(tmp_path):
    listing = read(tmp_path, "> Intro\nTESTVIDEO01\n> First.\n\n>Second.\n>\n// x\n"
                             "TESTVIDEO02\n")
    intro, first, second = listing.children
    assert intro == "Intro"
    assert first.notes == ["First.", "Second."] and second.notes == []


def test_notes_after_a_heading_introduce_the_chapter(tmp_path):
    listing = read(tmp_path, "TESTVIDEO01\n## Part\n> About the part.\nTESTVIDEO02\n")
    video, part = listing.children
    assert video.notes == []
    assert kinds(part.children) == ["About the part.", ("video", "TESTVIDEO02")]


def test_notes_after_a_playlist_follow_it(tmp_path):
    listing = read(tmp_path, f"> Before.\n{PLAYLIST_ID}\n> After.\nMy clip.mp4\n> Of the clip.\n")
    assert kinds(listing.children) == ["Before.", ("playlist", PLAYLIST_ID), "After.",
                                       ("file", "My clip.mp4")]
    assert listing.children[3].notes == ["Of the clip."]


# Titles of one's own: "=" ------------------------------------------------------------

def test_a_title_above_a_line(tmp_path):
    listing = read(tmp_path, f"=  My title \nTESTVIDEO01\n= The course\n{PLAYLIST_ID}\n"
                             "= Holiday\nclip.mp4\nTESTVIDEO02\n")
    assert [i.title for i in listing.items()] == ["My title", "The course", "Holiday", None]


@pytest.mark.parametrize("text, message", [
    ("= Title\n\nTESTVIDEO01\n", "line 1: a '=' title must stand directly above"),
    ("= Title\n// comment\nTESTVIDEO01\n", "line 1: a '=' title must stand directly above"),
    ("= Title\n> note\nTESTVIDEO01\n", "line 1: a '=' title must stand directly above"),
    ("= Title\n## Part\n", "line 1: a '=' title must stand directly above"),
    ("= One\n= Two\nTESTVIDEO01\n", "line 1: a '=' title must stand directly above a video, "
                                    "playlist or file (two '=' lines in a row)"),
    ("TESTVIDEO01\n= At the end\n", "line 2: a '=' title must stand directly above"),
    ("=\nTESTVIDEO01\n", "line 1: an empty '=' title"),
])
def test_title_errors(tmp_path, text, message):
    assert message in error(tmp_path, text)


def test_the_list_file_is_not_changed(tmp_path):
    text = "# My list\nTESTVIDEO01\n> note\n"
    read(tmp_path, text)
    assert (tmp_path / "list.txt").read_text(encoding="utf-8") == text
