"""Comment order, threads and the comments section."""

import re

import yt2html
from conftest import make_comments


def ids(comments):
    return [c["id"] for c in comments]


def test_top_level_oldest_first_with_pinned_in_its_place():
    roots, _ = yt2html.build_comment_tree(make_comments())
    # yt-dlp order is pinned, newest ... oldest; the page shows oldest first,
    # and the pinned comment (1 year old) moves to its estimated place.
    assert ids(roots) == ["N3", "P", "N2", "N1"]


def test_equal_estimated_times_keep_youtube_order_reversed():
    # YouTube's relative times are coarse ("1 year ago"), so many comments get
    # the same estimate; their exact order comes from YouTube (newest first).
    comments = [{"id": i, "parent": "root", "timestamp": 100} for i in ("c", "b", "a")]
    roots, _ = yt2html.build_comment_tree(comments)
    assert ids(roots) == ["a", "b", "c"]


def test_replies_oldest_first_and_nested():
    _, children = yt2html.build_comment_tree(make_comments())
    assert ids(children["N2"]) == ["N2r1", "N2r2"]
    assert ids(children["N2r2"]) == ["N2r2a"]


def test_missing_timestamp_keeps_position():
    items = [{"id": "a", "timestamp": 10}, {"id": "b", "timestamp": None}, {"id": "c", "timestamp": 20}]
    assert ids(yt2html.chronological(items)) == ["a", "b", "c"]


def test_orphan_reply_is_shown_at_top_level():
    comments = [{"id": "x", "parent": "missing", "timestamp": 1}]
    roots, _ = yt2html.build_comment_tree(comments)
    assert ids(roots) == ["x"]


def test_page_renders_threads_in_order(info):
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    order = re.findall(r'id="comment-([^"]+)"', html)
    assert order == ["N3", "P", "N2", "N2r1", "N2r2", "N2r2a", "N1"]
    assert "📌 Pinned by Channel" in html
    assert "nested reply &lt;b&gt;&amp;&lt;/b&gt;" in html


def test_avatar_embedded_once_per_author(info):
    images = {c["author_thumbnail"]: "data:image/png;base64,AAAA"
              for c in info["comments"] if c.get("author_thumbnail")}
    html = yt2html.render_page(info, None, images, yt2html.dt.date(2026, 11, 5))
    # @Channel commented twice and is also the channel avatar: one CSS class
    assert html.count("data:image/png;base64,AAAA") == len(images)
    # the comment without an avatar gets a letter placeholder
    assert '--hue:' in html


def test_comment_count_note(info):
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    assert "7 downloaded (YouTube shows 9)" in html
    assert "No comments." not in html


def test_no_comments_note_only_when_really_none(info):
    info.update(comments=[], comment_count=0)
    info[yt2html.YT_COUNT_KEY] = "0"
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    assert "No comments." in html


def test_limited_download_does_not_claim_no_comments(info):
    info.update(comments=[], comment_count=0)
    info[yt2html.LIMIT_KEY] = 0
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    assert "0 downloaded (YouTube shows 9)" in html
    assert "No comments." not in html
    assert "--max-comments" not in html  # no CLI parameters on the page


def test_no_comments_section_with_no_comments_option(info):
    del info["comments"]
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    assert "<h2>Comments</h2>" not in html


def test_comments_turned_off(info):
    info["comments"] = None
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5))
    assert "Comments are turned off or unavailable." in html
