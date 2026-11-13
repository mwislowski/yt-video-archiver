"""The author's info cards (--no-cards)."""

import datetime as dt

import pytest
from conftest import (FakeYDL, cards_collection, channel_card, infocards_item, link_card,
                      playlist_card, people_item, structured_description, video_card, watch_page)

import yt2html


def page_data(contents, cues):
    data = {"engagementPanels": [structured_description([infocards_item(*contents)])]}
    if cues is not None:
        data["cards"] = cards_collection(*cues)
    return data


def test_all_kinds_of_cards():
    data = page_data([channel_card(), video_card(), playlist_card(), link_card()],
                     [(66854, "Check out Nonstop Eurotrip!"),
                      (100549, "The Largest Rail System In the World"),
                      (14684, "Adding a Level Creator to My Game! | Devlog #1"),
                      (26801, "Click here to download!")])
    playlist, link, channel, video = yt2html.parse_cards(data)        # sorted by time
    assert channel == {
        "type": "channel", "title": "Nonstop Eurotrip",
        "url": "https://www.youtube.com/channel/UCBqWe9KKYUavknEaaZLk7cQ",
        "image": "https://img.test/channel-224", "details": ["165K subscribers"],
        "start": 66.854, "message": "Check out Nonstop Eurotrip!"}
    assert video["url"] == "https://www.youtube.com/watch?v=0KMYAEIXVzA"
    assert video["details"] == ["RMTransit", "22:25", "840K views · 4 years ago"]
    assert video["message"] == ""                       # same as the title
    assert playlist["url"] == ("https://www.youtube.com/playlist?"
                               "list=PLCGaK2yqfY2J-3SZSNY6f7b2_PWbuIXHa")
    assert playlist["details"] == ["Semag Games", "6 videos"]
    assert playlist["message"] == "Adding a Level Creator to My Game! | Devlog #1"
    assert link["url"].startswith("https://www.youtube.com/redirect?event=infocard")
    assert link["details"] == ["Password manager"] and link["start"] == 26.801


def test_cards_without_time_keep_youtube_order():
    data = page_data([video_card("AAAAAAAAAAA", "First"), video_card("BBBBBBBBBBB", "Second")], None)
    assert [c["title"] for c in yt2html.parse_cards(data)] == ["First", "Second"]


def test_card_with_an_unreadable_time_goes_last():
    data = page_data([video_card("AAAAAAAAAAA", "Broken"), video_card("BBBBBBBBBBB", "Later")],
                     [("not a number", ""), (90000, "")])
    assert [(c["title"], c["start"]) for c in yt2html.parse_cards(data)] == \
        [("Later", 90.0), ("Broken", None)]


def test_times_only_when_they_match_the_cards():
    data = page_data([video_card(), channel_card()], [(1000, "x")])   # one cue, two cards
    assert [c["start"] for c in yt2html.parse_cards(data)] == [None, None]
    assert [c["start"] for c in yt2html.parse_cards(page_data([video_card()], None))] == [None]


def test_unknown_lockups_and_no_cards():
    assert yt2html.parse_cards(page_data([{"somethingNewRenderer": {}}, video_card()],
                                         [(1, "a"), (2, "b")]))[0]["start"] == 0.002
    assert yt2html.parse_cards({"engagementPanels": []}) == []


def test_link_subtitle_equal_to_title_is_not_repeated():
    card = yt2html.parse_card(link_card("NordPass", "NordPass"))
    assert card["details"] == []


def render(info):
    return yt2html.render_page(info, None, {}, dt.date(2026, 11, 7))


def test_section_after_chapters_and_before_people(info):
    info[yt2html.CARDS_KEY] = yt2html.parse_cards(page_data(
        [channel_card(), video_card()], [(66854, "Check out Nonstop Eurotrip!"), (100549, "x")]))
    info[yt2html.EXTRAS_KEY] = yt2html.parse_description_extras(
        {"engagementPanels": [structured_description([people_item([("A", "b")])])]})
    html = render(info)
    assert html.index('id="chapters"') < html.index('id="info-cards"') < html.index('<div class="people-list">')
    assert "<h2>Info cards</h2>" in html
    assert ('href="https://www.youtube.com/watch?v=TESTVIDEO01&amp;t=66s"' in html
            and ">1:06</a>" in html)
    assert "Channel · 165K subscribers · “Check out Nonstop Eurotrip!”" in html
    assert 'class="card-image round img-placeholder"' in html     # channel, image not embedded
    assert "Video · RMTransit · 22:25 · 840K views · 4 years ago · “x”" in html


def test_card_without_time(info):
    info[yt2html.CARDS_KEY] = yt2html.parse_cards(page_data([video_card()], None))
    assert '<li><span class="time"></span>' in render(info)


def test_card_images_are_embedded(info):
    info[yt2html.CARDS_KEY] = yt2html.parse_cards(page_data([video_card()], [(1, "")]))
    assert "https://img.test/0KMYAEIXVzA-224" in yt2html.extra_image_urls(info)
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 7), extra_images={
        "https://img.test/0KMYAEIXVzA-224": "data:image/jpeg;base64,AAAA"})
    assert '<img class="card-image" src="data:image/jpeg;base64,AAAA" alt="">' in html


def test_no_section_without_cards(info):
    assert 'id="info-cards"' not in render(info)
    info[yt2html.CARDS_KEY] = []
    assert 'id="info-cards"' not in render(info)


def test_watch_page_cards_are_read(info, monkeypatch):
    data = page_data([video_card()], [(5000, "x")])
    ydl = FakeYDL(webpage=watch_page(data, {}))
    yt2html.add_watch_page_data(ydl, info)
    assert info[yt2html.CARDS_KEY][0]["start"] == 5.0


def test_no_cards_does_nothing_for_them(info, monkeypatch):
    monkeypatch.setattr(yt2html, "parse_cards", lambda data: pytest.fail("cards were read"))
    ydl = FakeYDL(webpage=watch_page(page_data([video_card()], [(5000, "x")]), {}))
    info.pop(yt2html.CARDS_KEY, None)
    yt2html.add_watch_page_data(ydl, info, cards=False)
    assert yt2html.CARDS_KEY not in info


def test_unreadable_watch_page_gives_no_cards(info):
    class Broken(FakeYDL):
        def urlopen(self, url):
            raise OSError("offline")

    yt2html.add_watch_page_data(Broken(), info)
    assert info[yt2html.CARDS_KEY] is None


def test_no_cards_option_hides_saved_cards(capsys, tmp_path, info):
    import json
    info[yt2html.CARDS_KEY] = yt2html.parse_cards(page_data([video_card()], [(1, "")]))
    saved = tmp_path / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    yt2html.main(["--from-json", str(saved), "-o", str(tmp_path / "a")])
    yt2html.main(["--from-json", str(saved), "-o", str(tmp_path / "b"), "--no-cards"])
    assert 'id="info-cards"' in next((tmp_path / "a").glob("*.html")).read_text("utf-8")
    assert 'id="info-cards"' not in next((tmp_path / "b").glob("*.html")).read_text("utf-8")


def test_fetch_info_passes_no_cards(monkeypatch, info):
    seen = {}
    monkeypatch.setattr(yt2html, "add_watch_page_data",
                        lambda ydl, i, cards=True: seen.update(cards=cards))

    class Fake:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download): return dict(info)
        def sanitize_info(self, i): return i

    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", Fake)
    yt2html.fetch_info("u", yt2html.parse_args(["u", "--no-cards"]))
    assert seen == {"cards": False}


LEAVE_PAGE = ('<html><a id="yt-logo" href="/"></a><a id="invalid-token-redirect-goto-site-button" '
              'class="yt-pill-button" role="button" href="https://www.nordpass.com/?a=1&amp;b=2">'
              'Go to site</a><a id="redirect-backto-safety-button" href="/">Back</a></html>')


class RedirectYDL:
    """urlopen for the redirect pages behind link cards."""

    def __init__(self, page=LEAVE_PAGE, error=None):
        self.page, self.error, self.urls = page, error, []

    def urlopen(self, url):
        from conftest import FakeResponse
        self.urls.append(url)
        if self.error:
            raise self.error
        return FakeResponse(self.page.encode())


def link_cards():
    return yt2html.parse_cards(page_data([link_card(), video_card()], [(1000, ""), (2000, "")]))


def test_link_card_target_is_resolved(capsys):
    cards = link_cards()
    ydl = RedirectYDL()
    yt2html.resolve_card_links(ydl, cards)
    link, video = cards
    assert ydl.urls == ["https://www.youtube.com/redirect?event=infocard&redir_token=TOKEN"]
    assert link["url"] == "https://www.nordpass.com/?a=1&b=2"
    assert link["details"] == ["Password manager", "nordpass.com"]
    assert video["url"] == "https://www.youtube.com/watch?v=0KMYAEIXVzA"     # not touched
    assert "Reading the target of 1 info card link" in capsys.readouterr().out


@pytest.mark.parametrize("ydl", [
    RedirectYDL(page="<html>Something else</html>"),
    RedirectYDL(page=LEAVE_PAGE.replace("https://www.nordpass.com/?a=1&amp;b=2", "javascript:x")),
    RedirectYDL(error=OSError("HTTP Error 404")),
])
def test_unresolved_link_keeps_the_redirect(capsys, ydl):
    cards = link_cards()
    yt2html.resolve_card_links(ydl, cards)
    assert cards[0]["url"].startswith("https://www.youtube.com/redirect?")
    assert cards[0]["details"] == ["Password manager"]
    assert "kept as a YouTube redirect" in capsys.readouterr().out


def test_no_requests_without_link_cards():
    ydl = RedirectYDL()
    yt2html.resolve_card_links(ydl, yt2html.parse_cards(page_data([video_card()], [(1, "")])))
    assert ydl.urls == []


def test_watch_page_resolves_links_unless_no_cards(info):
    data = page_data([link_card()], [(1000, "")])

    class Pages(RedirectYDL):
        def urlopen(self, url):
            from conftest import FakeResponse
            self.urls.append(url)
            return FakeResponse((watch_page(data, {}) if "/watch?" in url else LEAVE_PAGE).encode())

    ydl = Pages()
    yt2html.add_watch_page_data(ydl, info)
    assert info[yt2html.CARDS_KEY][0]["url"] == "https://www.nordpass.com/?a=1&b=2"
    ydl = Pages()
    yt2html.add_watch_page_data(ydl, info, cards=False)
    assert not any("redirect" in u for u in ydl.urls)
