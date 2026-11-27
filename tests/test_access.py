"""The video's state on YouTube (availability, age_limit, live_status): the
Access tile, the On YouTube row of About this copy, --json refresh keeping
the earlier state; and the facts' tiles, all of one size."""

import datetime as dt
import re

import pytest

import yt2html

DAY = dt.date(2026, 11, 5)
BUILT = dt.datetime(2026, 11, 27, 12, 0)


def render(info) -> str:
    return yt2html.render_page(info, None, {}, DAY, built=BUILT)


def facts(html: str) -> str:
    start = html.index('<div class="facts">')
    return html[start:html.index('<section class="description', start)]


def tiles(html: str) -> list[tuple[str, str, str | None]]:
    """(label, value, corner) of each tile after the Player tile."""
    return [(label, value, corner) for corner, value, label in re.findall(
        r'<div class="fact[^"]*">(?:<div class="corner">([^<]*)</div>)?'
        r'<div class="value">([^<]*)</div><div class="label">([^<]*)</div></div>', facts(html))]


def on_youtube(html: str) -> str:
    match = re.search(r"<dt>On YouTube</dt><dd>(.*?)</dd>", html)
    assert match
    return match.group(1)


@pytest.mark.parametrize("availability, age, value, corner", [
    ("unlisted", 0, "Unlisted", None), ("private", 0, "Private", None),
    ("subscriber_only", 0, "Members", None), ("premium_only", 0, "Premium", None),
    ("needs_auth", 18, "Sign-in", "18+"), (None, 18, "Sign-in", "18+"),
    ("unlisted", None, "Unlisted", None)])
def test_the_access_tile_after_published(info, availability, age, value, corner):
    info.update(availability=availability, age_limit=age)
    labels = [t[0] for t in tiles(render(info))]
    assert labels[labels.index("Published") + 1] == "Access"
    assert ("Access", value, corner or "") in tiles(render(info))
    # the same construction as every tile: value above, kind below
    assert yt2html.access_tile(info) == yt2html.fact("Access", value, corner=corner or "")


@pytest.mark.parametrize("state", [{"availability": "public", "age_limit": 0},
                                   {"availability": None}, {"age_limit": 0}, {}])
def test_no_access_tile_for_a_plainly_public_video_or_an_unknown_state(info, state):
    info.update(state)
    assert "Access" not in [t[0] for t in tiles(render(info))]


def test_the_corner_mark_is_kept_clear_of_the_value(info):
    info.update(availability="needs_auth", age_limit=18)
    assert ('<div class="fact marked"><div class="corner">18+</div><div class="value">Sign-in'
            in render(info))
    css = render(info)
    assert ".fact.marked .value{margin-right:22px}" in css and "color:var(--age)" in css
    # within the 10 px margin, not in it (the user, 27.11.2026)
    assert ".fact .corner{position:absolute;top:12px;right:10px;" in css


@pytest.mark.parametrize("state, text", [
    ({"availability": "public", "age_limit": 0, "live_status": "not_live"},
     "public; no age restriction; not broadcast live"),
    ({"availability": "unlisted", "age_limit": 0, "live_status": "was_live"},
     "unlisted; no age restriction; broadcast live as a stream or a premiere"),
    ({"availability": "subscriber_only", "age_limit": 0, "live_status": "post_live"},
     "members only; no age restriction; broadcast live as a stream or a premiere"),
    ({"availability": "premium_only", "age_limit": 0, "live_status": "is_upcoming"},
     "YouTube Premium only; no age restriction; scheduled to be broadcast live"),
    ({"availability": "needs_auth", "age_limit": 18, "live_status": "not_live"},
     "sign-in required: age-restricted (18+), so whether public or unlisted is not known; "
     "not broadcast live"),
    ({"availability": "private", "age_limit": 13, "live_status": "is_live"},
     "private; age-restricted (13+); live during the copy"),
    ({"availability": None, "live_status": "not_live"},
     "who could watch it not known; age restriction not known; not broadcast live"),
    ({}, "not recorded in the saved data"),
])
def test_the_on_youtube_row(info, state, text):
    info.update(state)
    html = render(info)
    assert on_youtube(html) == text
    rows = re.findall(r"<dt>(.*?)</dt>", html)
    assert rows[:3] == ["Downloaded", "On YouTube", "Page built"]


def test_refresh_keeps_the_earlier_state(info):
    info.update(availability="public", age_limit=0, live_status="is_live")
    yt2html.refresh_saved(info, {"availability": "unlisted", "age_limit": 0,
                                 "live_status": "was_live"})
    today = dt.date.today()
    assert info["availability"] == "unlisted" and info["live_status"] == "was_live"
    assert info[yt2html.PREVIOUS_KEY] == [{"until": today.isoformat(), "availability": "public"},
                                          {"until": today.isoformat(), "live_status": "is_live"}]
    day = today.strftime(yt2html.DATE_FORMAT)
    assert on_youtube(render(info)) == (
        f"unlisted (public until {day}); no age restriction; "
        f"broadcast live as a stream or a premiere (live during the copy until {day})")
    assert ("Access", "Unlisted", "") in tiles(render(info))     # the tile: the state now


def test_refresh_takes_a_state_the_saved_data_lacked_without_an_earlier_one(info):
    """Data saved without these fields (yt2html 0.13.2): no "not known until"."""
    yt2html.refresh_saved(info, {"availability": "public", "age_limit": 0})
    assert info["availability"] == "public" and yt2html.PREVIOUS_KEY not in info
    yt2html.refresh_saved(info, {"availability": None})          # not given now: kept
    assert info["availability"] == "public"


def test_every_tile_is_made_by_fact(info):
    """One construction for every tile, the Player tile's button too."""
    info.update(availability="needs_auth", age_limit=18)
    info[yt2html.UPDATES_KEY] = [{"date": "2026-11-20", "time": "10:00", "refreshed": True}]
    html = facts(render(info))
    assert len(re.findall(r'class="fact[ "]', html)) == len(tiles(render(info))) + 1  # + Player
    assert yt2html.PLAYER_TILE.startswith('<button class="fact play"')


@pytest.mark.parametrize("number, long", [(999_999_999, False), (1_000_000_000, True),
                                          (999_999_999_999, True)])
def test_a_number_of_a_billion_or_more_in_a_smaller_type(number, long):
    """A step, not a gradual shrinking (the user, 27.11.2026)."""
    tile = yt2html.fact("Views", yt2html.format_int(number))
    assert tile.startswith('<div class="fact long">' if long else '<div class="fact">')
    assert yt2html.fact("Comments", "1.2K").startswith('<div class="fact">')
    # only the program's own numbers: another text with as many digits stays (cut if long)
    assert yt2html.fact("Comments", "1,234,567,890").startswith('<div class="fact">')
    assert ".fact.long .value{font-size:12px}" in yt2html.CSS


@pytest.mark.browser
def test_no_text_widens_a_tile(info, chromium, tmp_path):
    """Every tile of one size (the user, 27.11.2026): a longer text, by
    mistake or from YouTube (a short count like "1,2 tys."), is cut;
    999 999 999 999 views (smaller type) and 999 999 999 likes fit whole, and
    Members beside the 18+ mark."""
    info.update(view_count=999_999_999_999, like_count=999_999_999,
                availability="subscriber_only", age_limit=18)
    info[yt2html.YT_COUNT_KEY] = "an unexpectedly long text from YouTube, by mistake"
    info[yt2html.UPDATES_KEY] = [{"date": "2026-11-20", "time": "10:00", "refreshed": True}]
    path = tmp_path / "Title [TESTVIDEO01].html"
    path.write_text(render(info), encoding="utf-8")
    for width in (1280, 360):
        page = chromium.new_page(viewport={"width": width, "height": 800})
        page.goto(path.as_uri())
        page.evaluate("""() => {   // the Player tile as with a video next to the page
            document.body.classList.add('has-video');
            document.querySelector('.fact.play .label').textContent =
                '2160p60 HDR · en-US · a label far too long for the tile'; }""")
        boxes = page.evaluate("""() => [...document.querySelectorAll('.facts .fact')]
            .filter(t => t.offsetParent).map(t => {
                const r = t.getBoundingClientRect(), v = t.querySelector('.value');
                return [t.querySelector('.label').textContent, r.width, r.height,
                        v.scrollWidth <= v.clientWidth]; })""")
        page.close()
        assert {(w, h) for _, w, h, _ in boxes} == {(120, 60)}, boxes
        whole = {label: fits for label, _, _, fits in boxes}
        assert whole["Views"] and whole["Likes"] and whole["Access"]
        assert not whole["Comments"]                              # cut, not widened
        assert len(boxes) == 9            # Player, the five, Access, Snapshot, Refreshed
