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


@pytest.mark.parametrize("url, playlist, channel", [
    ("https://www.youtube.com/playlist?list=PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l",
     "PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l", False),
    ("https://m.youtube.com/playlist/?list=PLx1234567890abc&si=x", "PLx1234567890abc", False),
    ("PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l", "PL22egh3ok4cP0T7UZRmP6TMLErZYWMN-l", False),
    ("https://www.youtube.com/@channel", None, True),
    ("https://www.youtube.com/@channel/playlists", None, True),
    ("https://www.youtube.com/channel/UCxyz/videos", None, True),
    ("https://www.youtube.com/watch?v=Z8lf5oWKfj0&list=PLx", None, False),  # a video
    ("https://youtu.be/Z8lf5oWKfj0", None, False),
    ("Z8lf5oWKfj0", None, False),
    ("https://example.test/playlist?list=PLx", None, False),
])
def test_playlist_and_channel_urls(url, playlist, channel):
    assert yt2html.playlist_id_from_url(url) == playlist
    assert yt2html.is_channel_url(url) is channel


def test_channel_url_fails_without_any_request(capsys, tmp_path, fake_fetch):
    code = yt2html.main(["https://www.youtube.com/@channel", "-o", str(tmp_path)])
    assert code == 1 and fake_fetch == []
    assert "this is a channel URL" in capsys.readouterr().out


def test_unrecognised_playlist_is_not_extracted(monkeypatch, tmp_path, capsys):
    """A URL that turns out to be a playlist only after the request: yt-dlp
    must return just the list (extract_flat), and the page is not made."""
    used = {}

    class RecordingYDL:
        def __init__(self, opts):
            used.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            return {"_type": "playlist", "id": "PLx", "entries": [{"url": "u"}]}

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", RecordingYDL)
    monkeypatch.setattr(yt2html, "add_watch_page_data",
                        lambda *a: pytest.fail("the watch page must not be read"))
    code = yt2html.main(["https://example.test/unusual-link", "-o", str(tmp_path)])
    assert code == 1
    assert used["extract_flat"] == "in_playlist" and used["noplaylist"] is True
    assert "leads to a playlist or channel, not a single video" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_existing_page_with_the_id_anywhere_and_any_case(tmp_path, capsys):
    (tmp_path / "Title [TESTVIDEO01] (copy).HTML").write_text("x")
    (tmp_path / "Title [TESTVIDEO01].info.json").write_text("x")      # not a page
    (tmp_path / "Title [TESTVIDEO01].mp4").write_text("x")            # not a page
    assert yt2html.existing_page(tmp_path, "TESTVIDEO01").name == "Title [TESTVIDEO01] (copy).HTML"
    assert yt2html.existing_page(tmp_path, "TESTVIDEO0") is None       # the whole id in brackets


def test_several_files_with_one_id_get_a_note_once(tmp_path, capsys):
    (tmp_path / "A [TESTVIDEO01].html").write_text("x")
    (tmp_path / "B [TESTVIDEO01].html").write_text("x")
    yt2html._SEVERAL_NOTED.clear()
    assert yt2html.existing_page(tmp_path, "TESTVIDEO01").name == "A [TESTVIDEO01].html"
    assert yt2html.existing_page(tmp_path, "TESTVIDEO01").name == "A [TESTVIDEO01].html"
    out = capsys.readouterr().out
    assert out.count("Note: several pages for [TESTVIDEO01]") == 1
    assert "A [TESTVIDEO01].html, B [TESTVIDEO01].html; using A [TESTVIDEO01].html" in out


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
    assert yt2html.main([URL, "-o", str(tmp_path), "--page", "ovr"]) == 0
    assert fake_fetch == [URL]
    assert page.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert [p.name for p in tmp_path.iterdir()] == ["Renamed [TESTVIDEO01].html"]
    assert "Replaced page" in capsys.readouterr().out


def test_skip_also_after_fetching_when_url_has_no_id(capsys, tmp_path, fake_fetch):
    page = tmp_path / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    assert yt2html.main(["https://example.test/unusual-link", "-o", str(tmp_path)]) == 0
    assert page.read_text() == "old"


def test_load_json_always_replaces(capsys, tmp_path, info_file):
    page = info_file.parent / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    assert yt2html.main(["--json", "load", str(info_file), "-o", str(info_file.parent)]) == 0
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


@pytest.mark.parametrize("text, expected", [
    ("Title [Z8lf5oWKfj0].html", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    ("042 My: own name [Z8lf5oWKfj0]", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    (r"D:\Videos\Title [hz6-3-7GGyI].HTML", "https://www.youtube.com/watch?v=hz6-3-7GGyI"),
    ("Python nauka [PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa]",
     "https://www.youtube.com/playlist?list=PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa"),
    ("Python nauka [PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa].html",
     "https://www.youtube.com/playlist?list=PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa"),
    ("Notes [draft].html", "Notes [draft].html"),                     # not an id
    # any file name with the id in square brackets, wherever it is
    ("Title [Z8lf5oWKfj0].info.json", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    ("Title [Z8lf5oWKfj0].mp4", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    ("Title [Z8lf5oWKfj0] (copy).html", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    ("Song [Remix] [Z8lf5oWKfj0]", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    ("[Z8lf5oWKfj0] Title [Remix].html", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
    ("Old [hz6-3-7GGyI] new [Z8lf5oWKfj0].html", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),  # last
    (r"D:\Kurs [PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa]\Notes [draft].html",
     r"D:\Kurs [PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa]\Notes [draft].html"),  # folders do not count
    ("https://example.test/x [Z8lf5oWKfj0]", "https://example.test/x [Z8lf5oWKfj0]"),
    ("https://www.youtube.com/watch?v=Z8lf5oWKfj0", "https://www.youtube.com/watch?v=Z8lf5oWKfj0"),
])
def test_source_from_page_name(text, expected):
    assert yt2html.source_from_page_name(text) == expected


def test_page_name_in_the_command():
    args = yt2html.parse_args(["My page [TESTVIDEO01].html", "Other [TESTVIDEO02].mp4"])
    assert args.sources == ["https://www.youtube.com/watch?v=TESTVIDEO01",
                            "https://www.youtube.com/watch?v=TESTVIDEO02"]


def test_page_name_is_not_changed_with_load_json():
    args = yt2html.parse_args(["--json", "load", "Title [TESTVIDEO01].html"])
    assert args.sources == ["Title [TESTVIDEO01].html"]


def test_overwrite_a_page_given_by_its_name(capsys, tmp_path, fake_fetch):
    page = tmp_path / "Renamed [TESTVIDEO01].html"
    page.write_text("old")
    assert yt2html.main([page.name, "-o", str(tmp_path), "--page", "ovr"]) == 0
    assert fake_fetch == ["https://www.youtube.com/watch?v=TESTVIDEO01"]
    assert page.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


# --------------------------------------------------------------------------
# The images of the existing page, for the page made again (review A5)
# --------------------------------------------------------------------------

AVATAR = "https://yt3.ggpht.com/ytc/author=s88-c-k-c0x00ffffff-no-rj"
PHOTO = "https://yt3.ggpht.com/person=s88"
ELSEWHERE = "https://img.example.org/b.jpg"


@pytest.fixture
def pictured(saved):
    """Saved data whose images are on YouTube's servers, one person's photo
    elsewhere."""
    import json
    data = json.loads(saved.read_text(encoding="utf-8"))
    for comment in data["comments"]:
        if comment.get("author_thumbnail"):
            comment["author_thumbnail"] = AVATAR
    data[yt2html.EXTRAS_KEY] = [{"type": "people", "title": "People", "people": [
        {"name": "Person A", "image": PHOTO}, {"name": "Person B", "image": ELSEWHERE}]}]
    saved.write_text(json.dumps(data), encoding="utf-8")
    return saved


def images_served(monkeypatch, online=True):
    """fetch_image faked: each address gives its own image; the addresses asked."""
    import base64
    asked = []

    def fetch(url):
        asked.append(url)
        return f"data:image/jpeg;base64,{base64.b64encode(url.encode()).decode()}" if online else None

    monkeypatch.setattr(yt2html, "fetch_image", fetch)
    return asked


def picture(url):
    import base64
    return f"data:image/jpeg;base64,{base64.b64encode(url.encode()).decode()}"


def load(capsys, saved, *more):
    from test_update import run
    return run(capsys, str(saved), "-o", str(saved.parent), "--json", "load", *more)


def ovr(capsys, saved, monkeypatch):
    """--page ovr, YouTube faked by the saved data."""
    import json
    from test_update import run
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda url, args: json.loads(saved.read_text(encoding="utf-8")))
    return run(capsys, URL, "-o", str(saved.parent), "--page", "ovr")


def the_page(folder):
    return next(folder.glob("*.html")).read_text(encoding="utf-8")


def test_the_page_made_again_takes_its_images_from_the_old_page(capsys, pictured, monkeypatch):
    asked = images_served(monkeypatch)
    assert load(capsys, pictured)[0] == 0
    assert {yt2html.resize_avatar_url(AVATAR), PHOTO, ELSEWHERE} <= set(asked)
    first = the_page(pictured.parent)
    assert picture(yt2html.resize_avatar_url(AVATAR)) in first and picture(PHOTO) in first
    kept = yt2html.images_of_page(next(pictured.parent.glob("*.html")))["keyed"]
    assert kept[yt2html.image_key(AVATAR)] == picture(yt2html.resize_avatar_url(AVATAR))
    assert kept[yt2html.image_key(PHOTO)] == picture(PHOTO)
    # made again: only the image of another server is asked for
    asked.clear()
    code, out = load(capsys, pictured)
    assert code == 0 and asked == [ELSEWHERE]
    assert "Thumbnail and cover from the existing page." in out
    assert "1 avatars from the existing page." in out
    assert "Downloading 0" not in out
    assert yt2html.image_key(AVATAR) == "be8d546d09be"   # 12 hex digits of SHA-256 of the address
    assert the_page(pictured.parent).count(picture(PHOTO)) == 1


def test_offline_the_images_stay_instead_of_placeholders(capsys, pictured, monkeypatch):
    """Review A5: a page made again without a connection (--json load, or
    after a long --video) lost the avatars and the other images to
    placeholders; only the thumbnail and the cover came from the old page."""
    images_served(monkeypatch)
    assert load(capsys, pictured)[0] == 0
    images_served(monkeypatch, online=False)
    code, out = ovr(capsys, pictured, monkeypatch)       # everything asked for: nothing comes
    assert code == 0
    html = the_page(pictured.parent)
    for url in (yt2html.resize_avatar_url(AVATAR), PHOTO, ELSEWHERE):
        assert picture(url) in html
    assert "could not be downloaded; kept from the existing page." in out
    assert "placeholders used" not in out


def test_page_ovr_asks_for_every_image_again(capsys, pictured, monkeypatch):
    asked = images_served(monkeypatch)
    assert load(capsys, pictured)[0] == 0
    asked.clear()
    assert ovr(capsys, pictured, monkeypatch)[0] == 0
    assert {yt2html.resize_avatar_url(AVATAR), PHOTO, ELSEWHERE} <= set(asked)
    assert any("maxresdefault" in url for url in asked)     # the thumbnail too


def test_without_the_old_page_s_image_a_placeholder(capsys, pictured, monkeypatch):
    images_served(monkeypatch, online=False)
    code, out = load(capsys, pictured)
    assert code == 0 and "could not be downloaded (placeholders used)." in out
    assert "kept from the existing page" not in out
    assert yt2html.images_of_page(next(pictured.parent.glob("*.html"))).get("keyed") is None


def test_offline_an_image_of_another_server_stays(capsys, pictured, monkeypatch):
    images_served(monkeypatch)
    assert load(capsys, pictured)[0] == 0
    asked = images_served(monkeypatch, online=False)
    code, out = load(capsys, pictured)
    assert code == 0 and asked == [ELSEWHERE]          # asked for first, then the page's
    assert "1 could not be downloaded; kept from the existing page." in out
    assert picture(ELSEWHERE) in the_page(pictured.parent)


def test_a_refresh_asks_for_the_thumbnail_and_the_cover_again(capsys, pictured, monkeypatch):
    """Their address stays when the channel changes the picture; the
    avatars and the other images keep theirs (taken from the page)."""
    import json
    from test_update import run
    images_served(monkeypatch)
    assert load(capsys, pictured)[0] == 0
    asked = images_served(monkeypatch)
    monkeypatch.setattr(yt2html, "fetch_info",
                        lambda url, args: json.loads(pictured.read_text(encoding="utf-8")))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(pictured.parent), "--json", "refresh")
    assert code == 0, out
    assert any("maxresdefault" in url for url in asked) and ELSEWHERE in asked
    assert yt2html.resize_avatar_url(AVATAR) not in asked and PHOTO not in asked


def test_only_images_of_youtube_s_servers_are_taken_first():
    for url in ("https://i.ytimg.com/vi/X/mqdefault.jpg", AVATAR, PHOTO,
                "https://yt3.googleusercontent.com/abc=s176"):
        assert yt2html.on_youtube(url), url
    for url in (ELSEWHERE, "https://ytimg.com.example.org/x.jpg", "https://notytimg.com/x.jpg",
                "not a url"):
        assert not yt2html.on_youtube(url), url


def test_the_image_notes_follow_the_page_made_again(capsys, pictured, monkeypatch):
    """Commit 6 of the series (notes 5.4): offline, what does not come is
    noted in the page; made again later, the same note keeps its first day;
    once the images come, the notes go (the page keeps {})."""
    import datetime as dt
    import json
    page_notes = lambda: yt2html.page_image_notes(next(pictured.parent.glob("*.html")))
    images_served(monkeypatch, online=False)
    assert load(capsys, pictured)[0] == 0
    notes = page_notes()
    assert {"thumbnail", "cover", "avatars", "images"} <= set(notes)
    assert all(n["date"] == dt.date.today().isoformat() and n["again"] for n in notes.values())
    # made again, still offline: the same notes, their first day kept
    page = next(pictured.parent.glob("*.html"))
    text = page.read_text(encoding="utf-8").replace(dt.date.today().isoformat(), "2026-11-01")
    page.write_text(text, encoding="utf-8")
    assert load(capsys, pictured)[0] == 0
    assert {n["date"] for n in page_notes().values()} == {"2026-11-01"}
    # online: they come, the notes go; the data are not written (load)
    before = pictured.read_bytes()
    images_served(monkeypatch)
    assert load(capsys, pictured)[0] == 0
    assert page_notes() == {} and pictured.read_bytes() == before
