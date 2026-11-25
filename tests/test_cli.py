"""The command line: options, --version, --json load and what gets saved."""

import io
import json
import sys
from pathlib import Path

import pytest

import yt2html


def run(capsys, *argv):
    code = yt2html.main(list(argv))
    return code, capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        yt2html.parse_args(["--version"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"yt2html {yt2html.__version__} (yt-dlp ")


def test_defaults():
    args = yt2html.parse_args(["URL"])
    assert args.theme == "auto"
    assert args.transcript == []                     # no option: no transcript
    assert args.output_dir == "."
    assert yt2html.parse_args(["URL", "--transcript"]).transcript == [("plain", None)]
    assert yt2html.parse_args(["URL", "--transcript", "--page", "theme:dark"]).transcript == [("plain", None)]


def test_transcript_option_and_the_address(capsys):
    # with versions the order is free
    args = yt2html.parse_args(["--transcript", "plain,timed", "URL1", "URL2"])
    assert args.sources == ["URL1", "URL2"]
    # alone, the address goes first; else it is taken as the versions
    assert yt2html.parse_args(["URL1", "--transcript"]).sources == ["URL1"]
    with pytest.raises(SystemExit):
        yt2html.parse_args(["--transcript", "https://www.youtube.com/watch?v=x"])
    assert "the video's address goes before --transcript" in capsys.readouterr().err


def test_invalid_transcript_option_is_rejected(capsys):
    with pytest.raises(SystemExit):
        yt2html.parse_args(["URL", "--transcript", "plain,plain"])
    assert "given more than once" in capsys.readouterr().err


@pytest.mark.parametrize("options, message", [
    (["--transcript", "timed", "--transcript", "dual:pl"],
     "--transcript is given more than once; put all its parameters in one, comma-separated"),
    (["--transcript", "--transcript", "srt"], "--transcript is given more than once"),
    (["--video", "720p", "--video", "audio:pl"],
     "--video is given more than once; put all its parameters in one, comma-separated"),
    (["--video", "--video"], "--video is given more than once"),
    (["--olang", "pl", "--olang", "en"], "--olang is given more than once\n"),
    (["-o", "a", "-o", "b"], "-o is given more than once\n"),
    (["-o", "a", "--output-dir", "a"], "--output-dir is given more than once\n"),
    (["--cookies", "firefox", "--cookies", "chrome"], "--cookies is given more than once\n"),
    (["--index", "a.txt", "--index", "b.txt"], "--index is given more than once\n"),
    (["--index-only", "a.txt", "--index-only", "b.txt"], "--index-only is given more than once\n")])
def test_every_option_at_most_once(capsys, options, message):
    """README: a second one is an error, not a replacement (argparse would keep
    the last: --video 720p --video audio:pl downloaded 1080p). Options with a
    default (--transcript [], -o the current folder) count too."""
    sources = [] if options[0].startswith("--index") else ["URL"]
    with pytest.raises(SystemExit):
        yt2html.parse_args(sources + options)
    assert message in capsys.readouterr().err


def test_once_leaves_nothing_behind():
    args = yt2html.parse_args(["URL", "--transcript", "timed", "--video", "720p", "-o", "out",
                               "--olang", "pl", "--json", "write"])
    assert args.transcript == [("timed", None)] and args.output_dir == "out"
    assert yt2html.ONCE_GIVEN not in vars(args)


def test_load_json_saves_in_current_folder(capsys, info_file, tmp_path, monkeypatch):
    work = info_file.parent
    monkeypatch.chdir(work)
    code, out = run(capsys, "--json", "load", info_file.name)
    assert code == 0
    pages = list(work.glob("*.html"))
    assert len(pages) == 1 and pages[0].name.endswith("[TESTVIDEO01].html")
    html = pages[0].read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html and "05.11.2026" in html  # snapshot date from the file


def test_output_the_code_page_cannot_show(info, tmp_path, monkeypatch):
    # On Windows, output sent to a file or a pipe (> log.txt, | Tee-Object) uses the
    # code page, which has no "｜" (yt-dlp's stand-in for "|" in file names); on
    # 22.11.2026 such a run stopped at "Saved page:" and never reached --video
    info["title"] = "War ｜｜ Ring"
    (tmp_path / "pages").mkdir()
    data = tmp_path / "pages" / "test.info.json"
    data.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    code = yt2html.main(["--json", "load", str(data), "-o", str(tmp_path / "pages")])
    sys.stdout.flush()
    out = raw.getvalue().decode("cp1252")
    assert code == 0 and "Saved page: " in out and "War ?? Ring" in out
    assert [p.name for p in (tmp_path / "pages").glob("*.html")] == ["War ｜｜ Ring [TESTVIDEO01].html"]


def test_no_output_stream(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)        # pythonw and other windowless starts
    yt2html.readable_output()


def test_output_folder_option(capsys, info_file, tmp_path):
    out_dir = tmp_path / "pages"
    code, _ = run(capsys, "--json", "load", str(info_file), "-o", str(out_dir))
    assert code == 0 and len(list(out_dir.glob("*.html"))) == 1


def test_bilingual_without_saved_translation_saves_nothing(capsys, info_file, tmp_path):
    out_dir = tmp_path / "pages"
    code, out = run(capsys, "--json", "load", str(info_file), "-o", str(out_dir),
                    "--transcript", "dual:de")
    assert code == 1
    assert "no dual:de in the data (no transcript translation or subtitles of it)" in out
    assert not list(out_dir.glob("*.html"))


def test_no_transcript_without_the_option(capsys, info_file, tmp_path):
    # saved in the data, but not asked for: not on the page
    out_dir = tmp_path / "pages"
    run(capsys, "--json", "load", str(info_file), "-o", str(out_dir))
    html = next(out_dir.glob("*.html")).read_text("utf-8")
    assert 'class="transcript' not in html and "<dt>Transcript</dt><dd>left out</dd>" in html
    run(capsys, "--json", "load", str(info_file), "-o", str(out_dir), "--transcript")
    assert '<section class="transcript original"' in next(out_dir.glob("*.html")).read_text("utf-8")


def test_bilingual_missing_at_download_is_a_note(capsys, info, tmp_path):
    # YouTube had no German when the data was saved: rebuilt without it, no error
    info[yt2html.TRANSCRIPT_KEY]["missing"] = {"de": {
        "reason": "YouTube offers no translation of this transcript into 'de' (available: pl)",
        "refused": False, "date": "2026-11-05"}}
    out_dir = tmp_path / "pages"
    out_dir.mkdir()
    saved = out_dir / "x.info.json"
    saved.write_text(json.dumps(info), encoding="utf-8")
    code, _ = run(capsys, "--json", "load", str(saved), "-o", str(out_dir),
                  "--transcript", "dual:de,dual:pl")
    html = next(out_dir.glob("*.html")).read_text("utf-8")
    assert code == 0 and html.count('class="transcript bilingual"') == 1 and 'data-lang="de"' not in html
    assert ("; 1 version: dual with Polish (auto-translated); not available: dual:de "
            "(YouTube offers no translation of this transcript into &#x27;de&#x27; (available: pl))"
            in html)


def test_bilingual_for_a_video_without_captions(capsys, info, tmp_path, monkeypatch):
    # downloaded now: no captions at all is a note, the page is made
    def fetch(source, args):
        return {**info, yt2html.TRANSCRIPT_KEY: None,
                yt2html.TRANSCRIPT_MISSING_KEY: "the video has no captions"}
    monkeypatch.setattr(yt2html, "fetch_info", fetch)
    monkeypatch.setattr(yt2html, "fetch_images", lambda *a, **k: {})
    monkeypatch.setattr(yt2html, "fetch_thumbnail", lambda info: None)
    code, _ = run(capsys, "https://www.youtube.com/watch?v=TESTVIDEO01", "-o", str(tmp_path),
                  "--transcript", "dual:pl")
    html = next(tmp_path.glob("*.html")).read_text("utf-8")
    assert code == 0 and "<dt>Transcript</dt><dd>none: the video has no captions</dd>" in html


def test_bilingual_with_saved_translation(capsys, info_file, tmp_path):
    out_dir = tmp_path / "pages"
    code, _ = run(capsys, "--json", "load", str(info_file), "-o", str(out_dir),
                  "--transcript", "plain,dual:pl")
    html = next(out_dir.glob("*.html")).read_text(encoding="utf-8")
    assert code == 0 and "transcript bilingual" in html


def test_page_is_self_contained(capsys, info_file, tmp_path, monkeypatch):
    monkeypatch.setattr(yt2html, "fetch_image", lambda url: "data:image/png;base64,AAAA")
    out_dir = tmp_path / "pages"
    run(capsys, "--json", "load", str(info_file), "-o", str(out_dir))
    html = next(out_dir.glob("*.html")).read_text(encoding="utf-8")
    assert html.count("<script") == 1 and "<script src" not in html  # only the built-in player
    assert 'src="http' not in html and "url(http" not in html  # no external images


def test_failure_of_one_source_does_not_stop_others(capsys, info_file, tmp_path):
    code, out = run(capsys, "--json", "load", str(info_file.parent / "missing.json"),
                    str(info_file), "-o", str(info_file.parent))
    assert code == 1
    assert "FAILED" in out and "Done: 1 saved, 0 skipped (page exists), 1 failed." in out


# --json load sources: patterns, video ids and page names

def saved(folder, name="Title [TESTVIDEO01].info.json", info=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(info or {"id": "TESTVIDEO01", "title": "Title"}), encoding="utf-8")
    return path


def test_load_json_pattern_is_expanded_with_literal_brackets(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path / "data")
    saved(tmp_path / "data", "Other [TESTVIDEO02].info.json", {"id": "TESTVIDEO02", "title": "O"})
    code, out = run(capsys, "--json", "load", "data/*[TESTVIDEO01]*", "-o", "data")
    assert code == 0 and len(list((tmp_path / "data").glob("*.html"))) == 1
    code, out = run(capsys, "--json", "load", "*.info.json", "-o", "data")   # in -o
    assert code == 0 and len(list((tmp_path / "data").glob("*.html"))) == 2


def test_load_json_pattern_without_matches(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "--json", "load", "*nothing*.info.json")
    assert code == 1 and "FAILED: *nothing*.info.json: no file matches" in out


def test_load_json_by_video_id_or_name_in_the_output_folder(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path / "out")
    for source in ("TESTVIDEO01", "[TESTVIDEO01]", "Title [TESTVIDEO01].html",
                   "Title [TESTVIDEO01].info.json", "out/Title [TESTVIDEO01]"):
        code, out = run(capsys, "--json", "load", source, "-o", "out")
        assert code == 0, (source, out)
        assert f"Reading {Path('out') / 'Title [TESTVIDEO01].info.json'}" in out
    assert (tmp_path / "out" / "Title [TESTVIDEO01].html").exists()


def test_load_json_explains_what_was_looked_for(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "--json", "load", "TESTVIDEO09", "-o", "out")
    assert code == 1
    assert "no saved data for TESTVIDEO09 in out (looked for *[TESTVIDEO09]*.info.json)" in out
    code, out = run(capsys, "--json", "load", "missing.json")
    assert "FAILED: missing.json: no such file: missing.json" in out


def test_an_id_is_looked_up_in_the_output_folder(capsys, tmp_path, monkeypatch):
    # a file of that name in the current folder is not the output folder's: not read
    monkeypatch.chdir(tmp_path)
    saved(tmp_path, "TESTVIDEO01", {"id": "TESTVIDEO01", "title": "From the file"})
    saved(tmp_path / "out", "Lookup [TESTVIDEO01].info.json", {"id": "TESTVIDEO01", "title": "L"})
    code, out = run(capsys, "--json", "load", "TESTVIDEO01", "-o", "out")
    assert code == 0 and f"Reading {Path('out') / 'Lookup [TESTVIDEO01].info.json'}\n" in out



def test_load_json_finds_saved_data_with_the_id_anywhere(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path / "out", "Title [TESTVIDEO01] (copy).INFO.JSON")
    for source in ("TESTVIDEO01", "My video [TESTVIDEO01].mp4", "Renamed [TESTVIDEO01] x.html"):
        code, out = run(capsys, "--json", "load", source, "-o", "out")
        assert code == 0, (source, out)


def test_load_json_takes_no_playlist_ids(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "--json", "load", "Kurs [PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa].html")
    assert code == 1 and "no such file: Kurs [PLeBgIYTbh-CzUlJnPXenyingrNSXQgFoa].html" in out



@pytest.mark.parametrize("options, message", [
    (["--json", "load", "--json", "write"], "--json is given more than once\n"),
    (["--json"], "argument --json: expected one argument"),
    (["--json", "update"], "'update' is not a parameter (use write, refresh, load, merge or show)"),
    (["--json", "merge", "--video"], "--json merge only merges SRT files into the saved data "
     "and makes no page, so it cannot be used with --video"),
    (["--json", "merge", "--page", "ovr", "--index", "list.txt"], "so it cannot be used with "
     "--page, --index"),
    (["--json", "merge", "--page", "theme:dark"], "so it cannot be used with --page"),
    (["--json", "merge", "--transcript", "plain,dual:pl"], "so it cannot be used with "
     "--transcript; make the page then with: yt2html ID --json load --transcript "
     "plain,dual:CODE"),
    (["--json", "merge", "--index-only", "list.txt"], "so it cannot be used with --index-only"),
    (["--json", "merge", "--video-formats"], "--video-formats makes no page, so it cannot be "
     "used with --json merge"),
    (["--json", "refresh", "--page", "ovr"], "--page ovr downloads everything again from YouTube "
     "and --json refresh uses the saved data; use one of them"),
    (["--json", "refresh", "--video-formats"], "cannot be used with --json refresh"),
    (["--json", "load", "--page", "ovr"], "--page ovr downloads everything again from YouTube "
     "and --json load uses the saved data; use one of them"),
    (["--json", "write", "--video-formats"], "--video-formats makes no page, so it cannot be "
     "used with --json write"),
    (["--json", "load", "--video"], "--video needs YouTube, so it cannot be used with --json load"),
])
def test_one_json_option_at_a_time(capsys, options, message):
    with pytest.raises(SystemExit):
        yt2html.parse_args(["x.info.json", *options])
    assert message in capsys.readouterr().err


# Sources in the command ------------------------------------------------------

def test_duplicates_on_the_command_line(capsys):
    args = yt2html.parse_args(["https://www.youtube.com/watch?v=TESTVIDEO01",
                               "https://youtu.be/TESTVIDEO01?si=abc", "TESTVIDEO01"])
    assert args.sources == ["https://www.youtube.com/watch?v=TESTVIDEO01"]
    assert "Ignored 2 duplicate URLs given on the command line." in capsys.readouterr().out


def test_nothing_to_do(capsys):
    with pytest.raises(SystemExit):
        yt2html.parse_args([])
    assert "give at least one URL, or a list file with --index FILE" in capsys.readouterr().err


def test_the_batch_file_option_is_gone(capsys, tmp_path):
    # replaced by --index FILE (archiving from a list always makes its index)
    with pytest.raises(SystemExit):
        yt2html.parse_args(["-a", str(tmp_path / "list.txt")])
    assert "unrecognized arguments: -a" in capsys.readouterr().err
