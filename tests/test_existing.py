"""Skipping videos that already have a page, --overwrite and safe writing."""

import pytest

import yt2html

URL = "https://www.youtube.com/watch?v=TESTVIDEO01"


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=Z8lf5oWKfj0", "Z8lf5oWKfj0"),
    ("https://youtu.be/Z8lf5oWKfj0?si=abc", "Z8lf5oWKfj0"),
    ("https://www.youtube.com/shorts/Z8lf5oWKfj0", "Z8lf5oWKfj0"),
    ("https://m.youtube.com/watch?v=Z8lf5oWKfj0&t=10s&list=PLx", "Z8lf5oWKfj0"),
    ("https://www.youtube.com/watch?v=hz6-3-7GGyI", "hz6-3-7GGyI"),
    ("Z8lf5oWKfj0", "Z8lf5oWKfj0"),
    ("https://www.youtube.com/playlist?list=PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l", None),
    ("https://www.youtube.com/@channel", None),
    ("https://example.test/x", None),
])
def test_video_id_from_url(url, expected):
    assert yt2html.video_id_from_url(url) == expected


def test_existing_page_found_by_id_in_any_name(tmp_path):
    (tmp_path / "042 My own name [TESTVIDEO01].html").write_text("x")
    (tmp_path / "Other [TESTVIDEO02].html").write_text("x")
    (tmp_path / "Unfinished [TESTVIDEO03].html.part").write_text("x")
    sub = tmp_path / "season 1"
    sub.mkdir()
    (sub / "In a subfolder [TESTVIDEO04].html").write_text("x")
    assert yt2html.existing_page(tmp_path, "TESTVIDEO01").name == "042 My own name [TESTVIDEO01].html"
    assert yt2html.existing_page(tmp_path, "TESTVIDEO03") is None  # unfinished
    assert yt2html.existing_page(tmp_path, "TESTVIDEO04") is None  # subfolders are not searched
    assert yt2html.existing_page(tmp_path, None) is None
    assert yt2html.existing_page(tmp_path / "missing", "TESTVIDEO01") is None


@pytest.fixture
def fake_fetch(monkeypatch, info):
    """fetch_info without YouTube; records how often it was called."""
    calls = []

    def fetch(url, args):
        calls.append(url)
        return {**info, "id": yt2html.video_id_from_url(url) or info["id"]}

    monkeypatch.setattr(yt2html, "fetch_info", fetch)
    return calls


def test_existing_page_is_skipped_without_fetching(capsys, tmp_path, fake_fetch):
    page = tmp_path / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    code = yt2html.main([URL, "-o", str(tmp_path)])
    assert code == 0
    assert fake_fetch == []                      # nothing downloaded
    assert page.read_text() == "old"             # nothing changed
    assert "Skipped (page exists)" in capsys.readouterr().out


def test_new_video_is_saved(capsys, tmp_path, fake_fetch):
    assert yt2html.main([URL, "-o", str(tmp_path)]) == 0
    assert fake_fetch == [URL]
    assert len(list(tmp_path.glob("*[[]TESTVIDEO01[]].html"))) == 1
    assert not list(tmp_path.glob("*.part"))     # the temporary file is gone


def test_overwrite_replaces_the_existing_file_under_its_name(capsys, tmp_path, fake_fetch):
    page = tmp_path / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    assert yt2html.main([URL, "-o", str(tmp_path), "--overwrite"]) == 0
    assert fake_fetch == [URL]
    assert page.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert [p.name for p in tmp_path.iterdir()] == ["Renamed [TESTVIDEO01].html"]
    assert "Replaced page" in capsys.readouterr().out


def test_skip_also_after_fetching_when_url_has_no_id(capsys, tmp_path, fake_fetch):
    page = tmp_path / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    assert yt2html.main(["https://example.test/unusual-link", "-o", str(tmp_path)]) == 0
    assert page.read_text() == "old"


def test_from_json_always_replaces(capsys, tmp_path, info_file):
    page = tmp_path / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    assert yt2html.main(["--from-json", str(info_file), "-o", str(tmp_path)]) == 0
    assert page.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_summary_counts_skipped(capsys, tmp_path, fake_fetch):
    (tmp_path / "Done [TESTVIDEO01].html").write_text("old")
    yt2html.main([URL, "https://www.youtube.com/watch?v=TESTVIDEO02", "-o", str(tmp_path)])
    assert "Done: 1 saved, 1 skipped (page exists), 0 failed." in capsys.readouterr().out


def test_interrupted_write_leaves_no_page(tmp_path, monkeypatch):
    target = tmp_path / "Title [TESTVIDEO01].html"

    def broken_replace(src, dst):
        raise KeyboardInterrupt  # e.g. Ctrl+C just before the rename

    monkeypatch.setattr(yt2html.os, "replace", broken_replace)
    with pytest.raises(KeyboardInterrupt):
        yt2html.write_atomically(target, "<html>")
    assert not target.exists()
    assert yt2html.existing_page(tmp_path, "TESTVIDEO01") is None
