"""Numbers, dates, durations, file names and turning text into links."""

from pathlib import Path

import pytest

import yt2html


@pytest.mark.parametrize("value, expected", [
    (987, "987"), (1000, "1K"), (1234, "1.23K"), (12345, "12.3K"), (123456, "123K"),
    (1_230_000, "1.23M"), (45_600_000, "45.6M"), (2_100_000_000, "2.1B"), (None, "—"),
])
def test_format_compact_like_youtube(value, expected):
    assert yt2html.format_compact(value) == expected


def test_format_int_uses_no_break_space():
    assert yt2html.format_int(23_456_789) == "23 456 789"
    assert yt2html.format_int(None) == "—"


@pytest.mark.parametrize("seconds, expected", [(59, "0:59"), (597, "9:57"), (3725, "1:02:05")])
def test_format_duration(seconds, expected):
    assert yt2html.format_duration(seconds) == expected


def test_published_date_falls_back_to_upload_date():
    assert yt2html.published_date({"upload_date": "20080529"}) == "29.05.2008"
    assert yt2html.published_date({}) == "—"


def test_resize_avatar_url():
    url = "https://yt3.ggpht.com/ytc/abc=s88-c-k-c0x00ffffff-no-rj"
    assert yt2html.resize_avatar_url(url) == "https://yt3.ggpht.com/ytc/abc=s80-c-k-c0x00ffffff-no-rj"


def test_output_file_name_is_sanitised_like_yt_dlp(info, tmp_path):
    path = yt2html.output_path(info, tmp_path)
    assert path.parent == tmp_path
    assert path.name.endswith("[TESTVIDEO01].html")
    for forbidden in ':"|/<>?':
        assert forbidden not in path.name


def test_default_output_folder_is_current_folder():
    assert Path(yt2html.DEFAULT_OUTPUT_DIR) == Path(".")


class TestLinkify:
    def render(self, text, duration=600):
        return yt2html.linkify(text, "VID", duration)

    def test_url_with_trailing_punctuation(self):
        html = self.render("See https://example.org/page.")
        assert '<a href="https://example.org/page"' in html
        assert html.endswith("</a>.")

    def test_url_in_parentheses_keeps_balanced_brackets(self):
        html = self.render("(https://en.wikipedia.org/wiki/Foo_(bar))")
        assert 'href="https://en.wikipedia.org/wiki/Foo_(bar)"' in html

    def test_www_address_gets_https(self):
        assert 'href="https://www.example.com/x?a=1"' in self.render("www.example.com/x?a=1")

    def test_timestamps_link_to_the_video(self):
        html = self.render("at 1:23 and 1:02:03")
        assert 'href="https://www.youtube.com/watch?v=VID&amp;t=83s"' in html
        assert "t=3723s" not in html  # longer than the video

    def test_invalid_timestamp_is_plain_text(self):
        assert "<a" not in self.render("99:99")

    def test_hashtags_but_not_numbers(self):
        html = self.render("#Animation #3D #1")
        assert 'href="https://www.youtube.com/hashtag/animation"' in html
        assert 'href="https://www.youtube.com/hashtag/3d"' in html
        assert ">#1<" not in html

    def test_handles_but_not_email_addresses(self):
        html = self.render("Mail info@example.org or @SomeChannel.")
        assert 'href="https://www.youtube.com/@SomeChannel"' in html
        assert "@example.org" in html and 'href="https://www.youtube.com/@example' not in html

    def test_html_is_escaped(self):
        html = self.render("<script>alert(1)</script> & more")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html and "&amp; more" in html
