"""The index player in a real browser: Chromium, clicked with Playwright;
also the layouts of the index (!layout), measured where the browser puts them.

Run with: uv run --group browser pytest --browser (once before:
uv run --group browser playwright install chromium). The video is a real
1.5 s WebM (VP8, which Playwright's Chromium plays); a file that cannot be
played is a few bytes of text with a video extension.
"""

import shutil
from pathlib import Path

import pytest

import yt2html_index as index

pytestmark = pytest.mark.browser

VIDEO = Path(__file__).parent / "data" / "short.webm"
PLAYING = ("() => { const v = document.querySelector('video');"
           " return !v.paused && v.currentTime > 0; }")


@pytest.fixture
def open_index(chromium, tmp_path):
    """open_index(list text, files=("a.webm", ...), broken=(...)) -> the page:
    the index of the list built over tmp_path, opened from the disk."""
    contexts = []

    def open_it(text: str, files=(), broken=(), javascript=True, width=1280, height=720):
        for name in files:
            shutil.copy(VIDEO, tmp_path / name)
        for name in broken:
            (tmp_path / name).write_text("not a video", encoding="utf-8")
        listing = tmp_path / "series.txt"
        listing.write_text(text, encoding="utf-8")
        html, _ = index.render_index(index.read_list_file(str(listing)), {}, tmp_path)
        page_path = tmp_path / "series.html"
        page_path.write_text(html, encoding="utf-8")
        context = chromium.new_context(java_script_enabled=javascript,
                                       viewport={"width": width, "height": height},
                                       reduced_motion="reduce")
        contexts.append(context)
        page = context.new_page()
        page.goto(page_path.as_uri())
        return page

    yield open_it
    for context in contexts:
        context.close()


def thumb(page, n: int):
    """The thumbnail of the n-th row (from 1)."""
    return page.locator(".index .row").nth(n - 1).locator(".thumb, .play")


def button(page, name: str):
    return page.locator(f'.ix-player [data-do="{name}"]')


def disabled(page) -> dict[str, bool]:
    return page.evaluate("""() => Object.fromEntries(
        [...document.querySelectorAll('.ix-player button, .ix-player input')]
          .map(b => [b.dataset.do || b.dataset.speed || 'seek', b.disabled]))""")


def playing_title(page) -> str:
    return page.locator(".ix-playing").text_content()


def now_row(page) -> int:
    """The number (from 1) of the highlighted row, 0 for none."""
    return page.evaluate("""() => [...document.querySelectorAll('.index .row')]
        .findIndex(r => r.classList.contains('now')) + 1""")


def wait_title(page, title: str):
    page.wait_for_function(
        "t => document.querySelector('.ix-playing').textContent === t", arg=title)


# Before the first play ------------------------------------------------------------

def test_the_whole_player_is_greyed_until_the_first_play(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    assert page.locator(".ix-player").is_visible()
    assert all(disabled(page).values())      # every button, A and the speeds too
    assert playing_title(page) == "" and now_row(page) == 0
    assert page.locator(".ix-failed").is_hidden()


def test_without_files_the_player_stays_greyed(open_index):
    page = open_index("Missing.webm\nTESTVIDEO01\n")
    assert page.locator(".ix-player").is_visible() and all(disabled(page).values())
    thumb(page, 1).click(force=True)
    assert playing_title(page) == "" and page.locator("[data-file]").count() == 0


def test_without_javascript_the_index_is_a_plain_list(open_index):
    page = open_index("a.webm\n", files=["a.webm"], javascript=False)
    assert page.locator(".ix-player").is_hidden()
    assert page.locator(".index .row").is_visible()


# Playing ----------------------------------------------------------------------------

def test_a_thumbnail_plays_its_file(open_index):
    page = open_index("= First\na.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    assert playing_title(page) == "First" and now_row(page) == 1
    state = disabled(page)
    assert state["previous"] and not state["next"]          # the first file
    assert not any(state[k] for k in ("start", "back", "toggle", "forward", "end",
                                      "autoplay", "seek", "1"))
    assert button(page, "toggle").get_attribute("aria-label") == "Pause"
    # the highlight: the frame round the thumbnail and the title in the accent
    # colour, never the whole row (its notes would light up too; user, 19.11.2026)
    looks = page.evaluate("""() => { const row = document.querySelector('.index .row.now');
        const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent-text');
        const probe = document.createElement('span'); probe.style.color = accent;
        document.body.appendChild(probe);
        const out = {row: getComputedStyle(row).backgroundColor,
                     title: getComputedStyle(row.querySelector('.name')).color,
                     accent: getComputedStyle(probe).color,
                     ring: getComputedStyle(row.querySelector('.thumb')).boxShadow};
        probe.remove(); return out; }""")
    assert looks["row"] == "rgba(0, 0, 0, 0)" and looks["title"] == looks["accent"]
    assert looks["ring"] != "none"


def test_the_file_in_the_player_again_does_nothing(open_index):
    page = open_index("a.webm\n", files=["a.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    button(page, "toggle").click()
    at = page.evaluate("document.querySelector('video').currentTime")
    thumb(page, 1).click()
    page.wait_for_timeout(300)
    assert page.evaluate("document.querySelector('video').paused")
    assert page.evaluate("document.querySelector('video').currentTime") == at


def test_previous_and_next_skip_rows_without_a_file(open_index):
    page = open_index("a.webm\nMissing.webm\nTESTVIDEO01\nb.webm\n",
                      files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    button(page, "next").click()
    wait_title(page, "b")
    assert now_row(page) == 4 and disabled(page)["next"] and not disabled(page)["previous"]
    button(page, "previous").click()
    wait_title(page, "a")
    assert now_row(page) == 1 and disabled(page)["previous"]


# Previous and next keep playing or waiting (user's decision 19.11.2026) --------------

PAUSED = "() => document.querySelector('video').paused"


def waiting_at_start(page) -> bool:
    """Stopped at 0:00, with the duration read (the first frame shown)."""
    return page.evaluate("""() => { const v = document.querySelector('video');
        return v.paused && v.currentTime === 0 && v.readyState >= 1
          && document.querySelector('.ix-player .total').textContent !== '0:00'; }""")


def test_previous_and_next_go_on_playing(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function(PLAYING)
    button(page, "previous").click()
    wait_title(page, "a")
    page.wait_for_function(PLAYING)


def test_stopped_previous_and_next_wait_at_the_start(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    button(page, "toggle").click()               # paused in the middle
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function("() => document.querySelector('video').readyState >= 1")
    page.wait_for_timeout(300)
    assert waiting_at_start(page)
    assert button(page, "toggle").get_attribute("aria-label") == "Play"
    page.keyboard.press("Shift+P")               # the keys the same
    wait_title(page, "a")
    page.wait_for_function("() => document.querySelector('video').readyState >= 1")
    page.wait_for_timeout(300)
    assert waiting_at_start(page)
    button(page, "toggle").click()               # and it plays from there
    page.wait_for_function(PLAYING)


def test_after_to_the_start_next_waits(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    button(page, "start").click()
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function("() => document.querySelector('video').readyState >= 1")
    page.wait_for_timeout(300)
    assert waiting_at_start(page)


def test_after_the_end_next_waits(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()                       # Autoplay off: the file ends and stops
    page.wait_for_function("() => document.querySelector('video').ended", timeout=10000)
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function("() => document.querySelector('video').readyState >= 1")
    page.wait_for_timeout(300)
    assert waiting_at_start(page)


@pytest.mark.parametrize("playing", [True, False])
def test_after_a_failed_file_the_state_from_before_it(open_index, playing):
    page = open_index("a.webm\nbroken.webm\nb.webm\n", files=["a.webm", "b.webm"],
                      broken=["broken.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    if not playing:
        button(page, "toggle").click()
    button(page, "next").click()
    page.wait_for_selector(".ix-failed:not([hidden])")
    assert button(page, "toggle").text_content() == "▶"
    button(page, "next").click()
    wait_title(page, "b")
    if playing:
        page.wait_for_function(PLAYING)
    else:
        page.wait_for_function("() => document.querySelector('video').readyState >= 1")
        page.wait_for_timeout(300)
        assert waiting_at_start(page)


def test_a_failed_file_from_its_thumbnail_counts_as_playing(open_index):
    """A thumbnail always means play: after it fails, next plays."""
    page = open_index("a.webm\nbroken.webm\nb.webm\n", files=["a.webm", "b.webm"],
                      broken=["broken.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    button(page, "toggle").click()               # stopped on a
    thumb(page, 2).click()
    page.wait_for_selector(".ix-failed:not([hidden])")
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function(PLAYING)


def test_keys(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    page.keyboard.press("Shift+N")                  # nothing before the first play
    assert playing_title(page) == ""
    thumb(page, 1).click()                      # the thumbnail keeps the focus
    page.wait_for_function(PLAYING)
    page.keyboard.press("Shift+N")
    wait_title(page, "b")
    page.keyboard.press("Shift+P")
    wait_title(page, "a")
    page.wait_for_function(PLAYING)
    page.keyboard.press(" ")
    page.wait_for_function("() => document.querySelector('video').paused")


def test_to_the_start_always_stops(open_index):
    page = open_index("a.webm\n", files=["a.webm"])
    thumb(page, 1).click()
    page.wait_for_function(PLAYING)
    button(page, "start").click()
    assert page.evaluate("[document.querySelector('video').paused, "
                         "document.querySelector('video').currentTime]") == [True, 0]


def test_the_speed_stays_from_file_to_file(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    page.locator('.ix-player [data-speed="2"]').click()
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function(PLAYING)
    assert page.evaluate("document.querySelector('video').playbackRate") == 2


# A file that cannot be played -------------------------------------------------------

def test_a_file_that_cannot_be_played(open_index):
    page = open_index("a.webm\n= Broken one\nbroken.webm\nb.webm\n",
                      files=["a.webm", "b.webm"], broken=["broken.webm"])
    thumb(page, 2).click()
    page.wait_for_selector(".ix-failed:not([hidden])")
    assert page.locator(".ix-failed").inner_text() == "This file could not be played.\nBroken one"
    assert playing_title(page) == "Broken one"
    assert "failed" in page.locator(".index .row").nth(1).get_attribute("class")
    state = disabled(page)
    assert all(state[k] for k in ("start", "back", "toggle", "forward", "end", "seek"))
    assert not state["previous"] and not state["next"]
    button(page, "next").click()
    wait_title(page, "b")
    assert page.locator(".ix-failed").is_hidden()
    button(page, "previous").click()            # skips the failed file
    wait_title(page, "a")
    thumb(page, 2).click()                      # remembered: does nothing
    page.wait_for_timeout(300)
    assert playing_title(page) == "a"


# Autoplay ---------------------------------------------------------------------------

def test_without_autoplay_the_end_stops(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    page.wait_for_function("() => document.querySelector('video').ended", timeout=10000)
    page.wait_for_timeout(300)
    assert playing_title(page) == "a" and button(page, "toggle").text_content() == "▶"


def test_autoplay_skips_failed_files_and_rows_without_a_file(open_index):
    page = open_index("a.webm\nbroken.webm\nMissing.webm\nb.webm\n",
                      files=["a.webm", "b.webm"], broken=["broken.webm"])
    thumb(page, 2).click()                      # known to fail from now on
    page.wait_for_selector(".ix-failed:not([hidden])")
    thumb(page, 1).click()
    button(page, "autoplay").click()
    assert button(page, "autoplay").get_attribute("aria-pressed") == "true"
    wait_title(page, "b")
    page.wait_for_function(PLAYING)


def test_autoplay_stops_at_a_new_error(open_index):
    page = open_index("a.webm\nbroken.webm\nb.webm\n",
                      files=["a.webm", "b.webm"], broken=["broken.webm"])
    thumb(page, 1).click()
    button(page, "autoplay").click()
    page.wait_for_selector(".ix-failed:not([hidden])", timeout=10000)
    page.wait_for_timeout(500)
    assert playing_title(page) == "broken" and now_row(page) == 2


# Scrolling --------------------------------------------------------------------------

NOTES = "".join(f"> Paragraph {n} of a long note.\n" for n in range(60))


def in_view(page, n: int) -> bool:
    return page.evaluate("""n => { const r = document.querySelectorAll('.index .row')[n - 1]
        .getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }""", n)


def test_next_brings_its_row_into_view(open_index):
    page = open_index(f"a.webm\n{NOTES}b.webm\n", files=["a.webm", "b.webm"])
    thumb(page, 1).click()
    assert not in_view(page, 2)
    button(page, "next").click()
    wait_title(page, "b")
    page.wait_for_function("() => scrollY > 0")
    assert in_view(page, 2)
    # with a margin below it, not more than needed (the row stays at the bottom)
    gap = page.evaluate("""() => innerHeight - document.querySelectorAll('.index .row')[1]
        .getBoundingClientRect().bottom""")
    assert 40 <= gap <= 56


def test_autoplay_scrolls_only_if_the_row_that_ended_was_in_view(open_index):
    # the notes introduce chapters (notes under a file would belong to its row)
    page = open_index(f"a.webm\n## Two\n{NOTES}b.webm\n## Three\n{NOTES}c.webm\n",
                      files=["a.webm", "b.webm", "c.webm"])
    thumb(page, 1).click()
    button(page, "autoplay").click()
    page.evaluate("window.scrollTo(0, 900)")    # reading the notes: a and b out of view
    wait_title(page, "b")
    page.wait_for_timeout(300)
    assert page.evaluate("scrollY") == 900
    page.evaluate("document.querySelectorAll('.index .row')[1].scrollIntoView()")
    wait_title(page, "c")                       # b was in view: c is brought into view
    page.wait_for_function("() => { const r = document.querySelectorAll('.index .row')[2]"
                           ".getBoundingClientRect(); return r.bottom <= innerHeight; }")


def test_in_a_narrow_window_the_row_comes_out_from_under_the_panel(open_index):
    page = open_index(f"a.webm\n{NOTES}b.webm\n", files=["a.webm", "b.webm"],
                      width=600, height=900)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    thumb(page, 2).click()
    button(page, "previous").click()
    wait_title(page, "a")
    page.wait_for_timeout(300)
    top = page.evaluate("document.querySelectorAll('.index .row')[0].getBoundingClientRect().top")
    panel = page.evaluate("document.querySelector('.ix-player').getBoundingClientRect().bottom")
    assert 40 <= top - panel <= 56                 # with a margin above it


def test_enter_on_a_thumbnail_plays_it(open_index):
    page = open_index("a.webm\nb.webm\n", files=["a.webm", "b.webm"])
    assert thumb(page, 2).get_attribute("aria-label") == "Play: b"
    thumb(page, 2).focus()
    page.keyboard.press("Enter")
    wait_title(page, "b")
    page.wait_for_function(PLAYING)
    page.keyboard.press(" ")                    # on the file in the player: nothing
    page.wait_for_timeout(300)
    assert not page.evaluate("document.querySelector('video').paused")


# Layouts (!layout) ----------------------------------------------------------------------

LONG = "A title long enough to wrap " * 6
LAYOUT_LIST = (f"## Part, also long enough to wrap: {LONG}\n> Of the chapter.\n= {LONG}\na.webm\n"
               "> Of the file, long enough to wrap. " + "Words " * 60 + "\nb.webm\n")
MEASURE = """() => {
    const box = el => el && el.getBoundingClientRect().toJSON();
    const style = el => el && getComputedStyle(el);
    const lines = el => { const range = document.createRange();   // the lines of its text
        range.selectNodeContents(el); return [...range.getClientRects()].map(r => r.toJSON()); };
    const line = el => el && lines(el)[0];
    const probe = document.createElement('span');
    probe.style.backgroundColor =
        getComputedStyle(document.documentElement).getPropertyValue('--panel');
    document.body.appendChild(probe);
    const panel = getComputedStyle(probe).backgroundColor; probe.remove();
    const rows = [...document.querySelectorAll('.index .row')].map(row => ({
        thumb: box(row.querySelector('.thumb, .play')), info: box(row.querySelector('.info')),
        note: box(row.querySelector('.note')),
        infoBg: style(row.querySelector('.info')).backgroundColor,
        infoMin: style(row.querySelector('.info')).minHeight,
        noteBg: row.querySelector('.note') && style(row.querySelector('.note')).backgroundColor,
        num: box(row.querySelector('.num')),
        numText: line(row.querySelector('.num')),
        infoPad: parseFloat(style(row.querySelector('.info')).paddingLeft),
        title: line(row.querySelector('.name')), titleLines: lines(row.querySelector('.name'))}));
    const heading = document.querySelector('.index .chapter');
    const chapter = getComputedStyle(document.querySelector('.index>.note')).backgroundColor;
    return {panel, rows, chapter,
        heading: {box: box(heading), num: line(heading.querySelector('.num')),
                  text: lines(heading.querySelector('.text'))},
        chapterNote: box(document.querySelector('.index>.note'))}; }"""
CLEAR = "rgba(0, 0, 0, 0)"


def test_plain_puts_the_note_under_the_thumbnail_without_a_frame(open_index):
    found = open_index(LAYOUT_LIST, files=["a.webm", "b.webm"]).evaluate(MEASURE)  # the default
    row = found["rows"][0]
    assert row["note"]["left"] == row["thumb"]["left"]           # from the thumbnail's edge
    assert row["note"]["top"] - row["thumb"]["bottom"] == 6      # under it, as close as in compact
    assert abs(row["note"]["right"] - row["info"]["right"]) < 1  # to the right, as the title
    assert row["noteBg"] == CLEAR and found["chapter"] == CLEAR
    assert found["rows"][1]["infoBg"] == CLEAR


def test_compact_puts_the_note_beside_the_thumbnail_without_a_frame(open_index):
    found = open_index("!layout: compact\n" + LAYOUT_LIST, files=["a.webm", "b.webm"]
                       ).evaluate(MEASURE)
    row = found["rows"][0]
    assert row["note"]["left"] == row["info"]["left"] > row["thumb"]["right"]
    assert row["note"]["top"] < row["thumb"]["bottom"]
    assert row["noteBg"] == CLEAR and found["chapter"] == CLEAR and row["infoBg"] == CLEAR


def test_cards_frame_every_row_at_least_as_tall_as_its_thumbnail(open_index):
    found = open_index("!layout: cards\n" + LAYOUT_LIST, files=["a.webm", "b.webm"]
                       ).evaluate(MEASURE)
    long, short = found["rows"]
    assert long["infoBg"] == short["infoBg"] == found["panel"] != CLEAR  # also without a note
    assert short["info"]["height"] == short["thumb"]["height"]      # as tall as the thumbnail
    assert long["info"]["height"] > long["thumb"]["height"]         # grows with the note
    assert long["note"]["left"] > long["info"]["left"]              # the note inside the frame
    assert long["noteBg"] == CLEAR                                  # one frame, not two
    assert found["chapter"] == found["panel"]                       # chapters' notes framed


def test_without_thumbnails_the_round_button_takes_their_place(open_index):
    plain = open_index("!thumbnails: no\n" + LAYOUT_LIST, files=["a.webm", "b.webm"]
                       ).evaluate(MEASURE)["rows"][0]
    assert plain["note"]["left"] == plain["thumb"]["left"] and plain["thumb"]["width"] == 28
    assert plain["note"]["top"] >= plain["thumb"]["bottom"]
    cards = open_index("!thumbnails: no\n!layout: cards\n" + LAYOUT_LIST,
                       files=["a.webm", "b.webm"]).evaluate(MEASURE)["rows"][1]
    assert cards["infoMin"] == "28px"                               # the button's height


def title_height(row):
    return row["titleLines"][-1]["bottom"] - row["titleLines"][0]["top"]


@pytest.mark.parametrize("layout", ["plain", "compact", "cards"])
@pytest.mark.parametrize("thumbnails", ["yes", "no"])
@pytest.mark.parametrize("width", [1280, 1240, 1200])
def test_the_number_changes_nothing_but_the_title(open_index, layout, thumbnails, width):
    """The user, 19.11.2026: with numbers every layout looks as without them;
    the number stands before the title, which wraps under its own first letter.
    The number takes room on each line of the title, so a long title may need
    one line more: then the row is taller by that line and by nothing else
    (21.11.2026: Segoe UI on Windows hit such a width at 1280 px and 1240 px;
    with the cloud's Roboto 1240 px gives the extra line in a frame taller than
    the thumbnail, 1200 px in plain one shorter, so every case is tested here)."""
    head = f"!layout: {layout}\n!thumbnails: {thumbnails}\n"
    found = open_index(head + LAYOUT_LIST, files=["a.webm", "b.webm"], width=width
                       ).evaluate(MEASURE)
    plain = open_index(head + "!numbers: no\n" + LAYOUT_LIST, files=["a.webm", "b.webm"],
                       width=width).evaluate(MEASURE)
    shift = 0                                    # what the title lines above added
    for row, without in zip(found["rows"], plain["rows"]):
        extra = title_height(row) - title_height(without)        # 0 or the title's lines more
        moved = lambda box: {**box, "y": box["y"] - shift, "top": box["top"] - shift,
                             "bottom": box["bottom"] - shift}
        assert moved(row["thumb"]) == without["thumb"]           # the same place and size
        info = moved(row["info"])
        assert info == {**without["info"], "height": without["info"]["height"] + extra,
                        "bottom": without["info"]["bottom"] + extra}
        if layout == "plain":                                    # the note under the row
            moved_note = moved(row["note"]) if row["note"] else None
            if moved_note:
                assert moved_note["top"] - without["note"]["top"] == max(
                    info["bottom"], row["thumb"]["bottom"] - shift) - max(
                    without["info"]["bottom"], without["thumb"]["bottom"])
        elif row["note"]:                                        # the note under the title
            assert moved(row["note"])["top"] - without["note"]["top"] == extra
        if row["note"]:
            assert (row["note"]["left"], row["note"]["width"], row["note"]["height"]) == (
                without["note"]["left"], without["note"]["width"], without["note"]["height"])
        # The rows below move by what the taller of thumbnail and frame gained: all of
        # the extra line, or less when the frame was shorter than the thumbnail
        shift += (max(row["info"]["height"], row["thumb"]["height"])
                  - max(without["info"]["height"], without["thumb"]["height"]))
        assert row["numText"]["left"] == row["info"]["left"] + row["infoPad"]
        assert row["numText"]["top"] == row["title"]["top"]      # on the title's first line
        assert row["title"]["left"] - row["numText"]["right"] == 7      # .5em: about two spaces
        assert row["info"]["left"] - row["thumb"]["right"] == 12
        assert without["title"]["left"] == row["numText"]["left"]
    long = found["rows"][0]["titleLines"]
    assert len(long) > 1 and {r["left"] for r in long} == {long[0]["left"]}   # number hangs out
    if thumbnails == "no" and layout == "cards":   # the button on the title line (in plain and
        row = found["rows"][1]                     # compact at the top of the row, 4 px lower)
        line = (row["title"]["top"] + row["title"]["bottom"]) / 2
        assert abs((row["thumb"]["top"] + row["thumb"]["bottom"]) / 2 - line) <= 2


def test_a_chapter_number_hangs_out_and_its_note_stays_at_its_indent(open_index):
    found = open_index(LAYOUT_LIST, files=["a.webm", "b.webm"]).evaluate(MEASURE)
    heading, text = found["heading"], found["heading"]["text"]
    assert heading["num"]["left"] == heading["box"]["left"]
    assert text[0]["left"] - heading["num"]["right"] == 8.5         # .5em of 17 px
    assert len(text) > 1 and {r["left"] for r in text} == {text[0]["left"]}
    assert found["chapterNote"]["left"] == heading["box"]["left"]    # not indented
    assert found["rows"][0]["thumb"]["left"] - heading["box"]["left"] == 20   # the indent only
    without = open_index("!numbers: no\n" + LAYOUT_LIST, files=["a.webm", "b.webm"]
                         ).evaluate(MEASURE)["heading"]["text"]
    assert without[0]["left"] == heading["box"]["left"]


EDGES = """() => {
    const box = sel => document.querySelector(sel).getBoundingClientRect().toJSON();
    const rows = [...document.querySelectorAll('.index .row')].map(row => ({
        thumb: row.querySelector('.thumb, .play').getBoundingClientRect().toJSON(),
        info: row.querySelector('.info').getBoundingClientRect().toJSON()}));
    return {title: box('.index h1.title'), intro: box('.index>.note'),
            chapter: box('.index .chapter'), rows}; }"""


@pytest.mark.parametrize("layout", ["plain", "compact", "cards"])
@pytest.mark.parametrize("thumbnails", ["yes", "no"])
@pytest.mark.parametrize("width, indent", [(1280, 20), (500, 12)])
def test_rows_line_up_with_the_title_and_indent_exactly(open_index, layout, thumbnails,
                                                        width, indent):
    """The user, 21.11.2026: videos right under the title start where the title
    and its note start (the rows' old 8 px padding pushed them right), and a
    chapter's videos are indented by the indent alone."""
    found = open_index(f"!layout: {layout}\n!thumbnails: {thumbnails}\n# Series\n> Intro.\n"
                       "a.webm\n## Part\nb.webm\n", files=["a.webm", "b.webm"],
                       width=width).evaluate(EDGES)
    top, part = found["rows"]
    assert top["thumb"]["left"] == found["title"]["left"] == found["intro"]["left"]
    assert top["info"]["right"] == found["intro"]["right"]          # to the same right edge
    assert part["thumb"]["left"] - found["chapter"]["left"] == indent
    assert part["info"]["right"] == found["intro"]["right"]


def test_plain_without_numbers_keeps_the_note_under_the_thumbnail(open_index):
    row = open_index("!numbers: no\n" + LAYOUT_LIST, files=["a.webm", "b.webm"]
                     ).evaluate(MEASURE)["rows"][0]
    assert row["note"]["left"] == row["thumb"]["left"]
    assert row["info"]["left"] - row["thumb"]["right"] == 12
    assert abs(row["note"]["right"] - row["info"]["right"]) < 1


def test_in_a_narrow_window_the_cards_follow_the_smaller_thumbnail(open_index):
    short = open_index("!layout: cards\n" + LAYOUT_LIST, files=["a.webm", "b.webm"],
                       width=500).evaluate(MEASURE)["rows"][1]
    assert short["thumb"]["height"] == 54
    assert short["infoMin"] == "54px" and short["info"]["height"] >= 54
