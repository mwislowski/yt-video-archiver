"""--batch-file: reading URLs from a text file."""

import pytest

import yt2html

PLAYLIST = "https://www.youtube.com/playlist?list=PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l"
CHANNEL = "https://www.youtube.com/@channel"


def url(n: int) -> str:
    return f"https://www.youtube.com/watch?v=TESTVIDEO0{n}"


@pytest.fixture
def fake_fetch(monkeypatch, info):
    """fetch_info without YouTube; records the URLs it was called with.

    A playlist URL returns playlist data, as yt-dlp does."""
    calls = []

    def fetch(source, args):
        calls.append(source)
        if "playlist" in source:
            return {"_type": "playlist", "id": "PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l"}
        return {**info, "id": yt2html.video_id_from_url(source)}

    monkeypatch.setattr(yt2html, "fetch_info", fetch)
    return calls


def write_list(tmp_path, text, name="list.txt", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return str(path)


def test_comments_blank_lines_and_spaces_are_ignored(tmp_path, capsys):
    path = write_list(tmp_path, f"# Season 1\n\n  {url(1)}  \n\t\n#{url(9)}\n{url(2)}")
    args = yt2html.parse_args(["-a", path])
    assert args.sources == [url(1), url(2)]
    assert f"Read 2 URLs from {path}." in capsys.readouterr().out


def test_byte_order_mark_and_windows_line_ends(tmp_path):
    path = write_list(tmp_path, f"{url(1)}\r\n{url(2)}\r\n", encoding="utf-8-sig")
    assert yt2html.parse_args(["-a", path]).sources == [url(1), url(2)]


def test_bare_video_ids(tmp_path):
    path = write_list(tmp_path, "TESTVIDEO01\n")
    assert yt2html.parse_args(["-a", path]).sources == ["TESTVIDEO01"]


def test_command_line_first_then_files_in_order(tmp_path):
    first = write_list(tmp_path, url(2), "first.txt")
    second = write_list(tmp_path, url(3), "second.txt")
    args = yt2html.parse_args(["-a", second, url(1), "--batch-file", first])
    assert args.sources == [url(1), url(3), url(2)]


def test_same_video_in_other_url_forms_is_kept_once(tmp_path, capsys):
    path = write_list(tmp_path, "\n".join([
        url(1),
        "https://youtu.be/TESTVIDEO01?si=abc",
        "TESTVIDEO01",
        url(2),
        url(2),
    ]))
    args = yt2html.parse_args([url(2), "-a", path])
    assert args.sources == [url(2), url(1)]
    assert f"Read 5 URLs from {path} (4 duplicates ignored)." in capsys.readouterr().out


def test_duplicates_on_the_command_line(capsys):
    args = yt2html.parse_args([url(1), "https://youtu.be/TESTVIDEO01"])
    assert args.sources == [url(1)]
    assert "Ignored 1 duplicate URL given on the command line." in capsys.readouterr().out


def test_identical_playlist_urls_are_kept_once(tmp_path):
    path = write_list(tmp_path, f"{PLAYLIST}\n{PLAYLIST}\n")
    assert yt2html.parse_args(["-a", path]).sources == [PLAYLIST]


@pytest.mark.parametrize("content, message", [
    (None, "cannot read batch file"),
    ("", "contains no URLs"),
    ("# only a comment\n\n", "contains no URLs"),
])
def test_unusable_batch_file_stops_before_fetching(tmp_path, capsys, fake_fetch,
                                                   content, message):
    path = tmp_path / "list.txt"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        yt2html.main([url(1), "-a", str(path), "-o", str(tmp_path)])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert fake_fetch == []


def test_batch_file_that_is_not_utf8(tmp_path, capsys):
    path = write_list(tmp_path, f"{url(1)}  # zażółć", encoding="cp1250")
    with pytest.raises(SystemExit):
        yt2html.parse_args(["-a", path])
    assert "is not UTF-8 text" in capsys.readouterr().err


def test_no_url_and_no_batch_file(capsys):
    with pytest.raises(SystemExit):
        yt2html.parse_args([])
    assert "give at least one URL or a batch file" in capsys.readouterr().err


def test_bad_lines_fail_and_the_rest_is_archived(tmp_path, capsys, fake_fetch):
    path = write_list(tmp_path, f"{url(1)}\n{CHANNEL}\n{url(2)}\n")
    code = yt2html.main(["-a", path, "-o", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert fake_fetch == [url(1), url(2)]       # the channel is not even fetched
    assert f"FAILED: {CHANNEL}: this is a channel URL" in out
    assert "Done: 2 saved, 0 skipped (page exists), 1 failed." in out
    assert len(list(tmp_path.glob("*.html"))) == 2


def test_running_the_same_list_again_archives_only_what_is_missing(tmp_path, capsys,
                                                                   fake_fetch):
    path = write_list(tmp_path, f"{url(1)}\n{url(2)}\n")
    (tmp_path / "Done before [TESTVIDEO01].html").write_text("old")
    yt2html.main(["-a", path, "-o", str(tmp_path)])
    assert fake_fetch == [url(2)]
    assert "Done: 1 saved, 1 skipped (page exists), 0 failed." in capsys.readouterr().out
    fake_fetch.clear()
    yt2html.main(["-a", path, "-o", str(tmp_path)])
    assert fake_fetch == []
    assert "Done: 0 saved, 2 skipped (page exists), 0 failed." in capsys.readouterr().out


def test_batch_file_is_not_changed(tmp_path, fake_fetch):
    text = f"# my list\n{url(1)}\n{CHANNEL}\n"
    path = write_list(tmp_path, text)
    yt2html.main(["-a", path, "-o", str(tmp_path / "pages")])
    assert (tmp_path / "list.txt").read_text(encoding="utf-8") == text


def test_from_json_paths_in_a_batch_file(tmp_path, capsys, info_file):
    path = write_list(tmp_path, f"{info_file}\n{info_file}\n")
    out_dir = tmp_path / "pages"
    assert yt2html.main(["--from-json", "-a", path, "-o", str(out_dir)]) == 0
    assert f"Read 2 files from {path} (1 duplicate ignored)." in capsys.readouterr().out
    assert len(list(out_dir.glob("*.html"))) == 1
