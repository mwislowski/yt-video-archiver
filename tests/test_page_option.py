"""--page: the page's parameters (ovr, comments, snap, cards, avatars, theme)."""
import json

import pytest

import yt2html


def args_of(*argv):
    return yt2html.parse_args(["URL", *argv])


# Parsing -------------------------------------------------------------------------

def test_without_the_option_the_defaults():
    args = args_of()
    assert args.page is None and not args.overwrite
    assert args.comment_limit == yt2html.DEFAULT_COMMENTS == 1000
    assert args.max_comments is None and not args.no_comments   # nothing narrows saved data
    assert not (args.no_snapshot or args.no_cards or args.no_avatars) and args.theme == "auto"


def test_the_option_alone_changes_nothing():
    args = args_of("--page")
    assert args.page is not None
    assert (args.overwrite, args.comment_limit, args.max_comments, args.theme) == (
        False, yt2html.DEFAULT_COMMENTS, None, "auto")


def test_every_parameter_in_any_case():
    args = args_of("--page", "OVR,Comments:200,snap:NO,cards:no,Avatars:No,theme:Dark")
    assert args.overwrite and args.comment_limit == args.max_comments == 200
    assert args.no_snapshot and args.no_cards and args.no_avatars and args.theme == "dark"
    # the defaults written out: the same as without them
    args = args_of("--page", "snap:yes,cards:yes,avatars:yes,theme:auto")
    assert not (args.no_snapshot or args.no_cards or args.no_avatars) and args.theme == "auto"


@pytest.mark.parametrize("value, no_comments, limit, narrow", [
    ("comments:no", True, None, None),     # not asked for
    ("comments:0", False, 0, 0),           # asked for, none taken
    ("comments:5", False, 5, 5),
    ("comments:all", False, None, None),   # all: no limit, nothing narrowed either
])
def test_comments(value, no_comments, limit, narrow):
    args = args_of("--page", value)
    assert (args.no_comments, args.comment_limit, args.max_comments) == (no_comments, limit, narrow)


@pytest.mark.parametrize("value, message", [
    ("ovr,ovr", "'ovr' is given more than once"),
    ("theme:dark,theme:light", "'theme' is given more than once"),
    ("comments:5,comments:all", "'comments' is given more than once"),
    ("ovr,,snap:no", "empty item in 'ovr,,snap:no' (check the commas)"),
    ("comments:many", "'comments:many': comments is no, a number (0 too) or all"),
    ("comments:-1", "comments is no, a number (0 too) or all"),
    ("snap:off", "'snap:off': snap is yes or no"),
    ("cards", "'cards' is not a parameter"),
    ("theme:blue", "'theme:blue': theme is auto, light or dark"),
    ("overwrite", "'overwrite' is not a parameter (use ovr, comments:N, snap:no, cards:no, "
                  "avatars:no or theme:dark); the video's address goes before --page"),
    ("ovr:yes", "'ovr:yes' is not a parameter"),
    ("https://youtu.be/TESTVIDEO01", "the video's address goes before --page"),
])
def test_errors(capsys, value, message):
    with pytest.raises(SystemExit) as stop:
        yt2html.parse_args(["URL", "--page", value])
    assert stop.value.code == 2 and message in capsys.readouterr().err


def test_the_option_once(capsys):
    # argparse would keep the last one and drop "ovr" without a word
    with pytest.raises(SystemExit):
        yt2html.parse_args(["URL", "--page", "ovr", "--page", "theme:dark"])
    assert ("--page is given more than once; put all its parameters in one, comma-separated"
            in capsys.readouterr().err)


@pytest.mark.parametrize("option", ["--overwrite", "--no-comments", "--max-comments", "--no-cards",
                                    "--no-avatars", "--no-snapshot", "--theme"])
def test_the_old_options_are_gone(capsys, option):
    # a clean change: nothing interprets them any more
    with pytest.raises(SystemExit):
        yt2html.parse_args(["URL", option, *(["5"] if option == "--max-comments" else
                                             ["dark"] if option == "--theme" else [])])
    assert "unrecognized arguments" in capsys.readouterr().err


def test_ovr_and_the_saved_data_are_opposites(capsys):
    for parameter in ("load", "refresh"):
        with pytest.raises(SystemExit):
            yt2html.parse_args(["x.info.json", "--json", parameter, "--page", "ovr"])
        assert (f"--page ovr downloads everything again from YouTube and --json {parameter} "
                "uses the saved data; use one of them") in capsys.readouterr().err
    # with --json write the fresh data replace the saved ones: fine
    args = yt2html.parse_args(["URL", "--json", "write", "--page", "ovr"])
    assert args.overwrite and args.write_json and not args.update_json


# The download -----------------------------------------------------------------------

@pytest.fixture
def fetched(monkeypatch):
    """The yt-dlp options fetch_info() uses and the data it returns."""
    seen = {}

    class Fake:
        def __init__(self, opts): seen["opts"] = opts
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def extract_info(self, url, download): return {"id": "TESTVIDEO01", "comments": []}
        def sanitize_info(self, info): return info

    monkeypatch.setattr(yt2html.yt_dlp, "YoutubeDL", Fake)
    monkeypatch.setattr(yt2html, "add_watch_page_data", lambda ydl, info, cards: None)

    def fetch(*argv):
        info = yt2html.fetch_info("URL", args_of(*argv))
        return seen["opts"], info
    return fetch


@pytest.mark.parametrize("argv, asked, limit", [
    ((), "1000", 1000),                                 # the default limit
    (("--page", "comments:all"), "all", None),
    (("--page", "comments:200"), "200", 200),
    (("--page", "comments:0"), "0", 0),
])
def test_the_limit_asked_of_youtube_and_saved(fetched, argv, asked, limit):
    opts, info = fetched(*argv)
    assert opts["getcomments"] and opts["extractor_args"]["youtube"]["max_comments"][0] == asked
    assert info[yt2html.LIMIT_KEY] == limit


def test_no_comments_asks_for_none(fetched):
    opts, info = fetched("--page", "comments:no")
    assert not opts["getcomments"] and yt2html.LIMIT_KEY not in info


# Saved data --------------------------------------------------------------------------

def many_comments(n):
    return [{"id": f"c{i}", "text": f"Comment {i}", "author": "@a", "timestamp": 1700000000 - i,
             "parent": "root"} for i in range(n)]


def build(capsys, tmp_path, info, *options):
    out_dir = tmp_path / f"p{len(list(tmp_path.glob('p*')))}"
    out_dir.mkdir()
    saved = out_dir / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    assert yt2html.main(["--json", "load", str(saved), "-o", str(out_dir), *options]) == 0
    capsys.readouterr()
    return next(out_dir.glob("*.html")).read_text("utf-8")


def test_the_default_limit_does_not_narrow_saved_comments(capsys, tmp_path, info):
    # saved with all comments, before the default limit: a page from them keeps all
    info.update(comments=many_comments(1200), comment_count=1200)
    info[yt2html.LIMIT_KEY] = None
    html = build(capsys, tmp_path, info)
    assert "Comment 1199" in html and "--page comments:all" in html
    html = build(capsys, tmp_path, info, "--page", "comments:1000")
    assert "Comment 999" in html and "Comment 1000" not in html


def test_refresh_says_when_the_default_limit_cut_the_comments(capsys, tmp_path, info, monkeypatch):
    saved = tmp_path / "Title [TESTVIDEO01].info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    fresh = {**json.loads(json.dumps(info)), "comments": many_comments(1000),
             "comment_count": 1000, yt2html.LIMIT_KEY: yt2html.DEFAULT_COMMENTS,
             yt2html.YT_COUNT_KEY: "5000"}
    monkeypatch.setattr(yt2html, "fetch_info", lambda source, args: json.loads(json.dumps(fresh)))
    note = ("Comments refreshed up to the default limit of 1,000; --page comments:all "
            "refreshes all of them.")
    assert yt2html.main(["TESTVIDEO01", "-o", str(tmp_path), "--json", "refresh"]) == 0
    assert note in capsys.readouterr().out
    # a limit given on purpose: no advice
    assert yt2html.main(["TESTVIDEO01", "-o", str(tmp_path), "--json", "refresh",
                         "--page", "comments:1000"]) == 0
    assert note not in capsys.readouterr().out
