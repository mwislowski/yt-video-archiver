"""The command line: options, --version, --from-json and what gets saved."""

import json
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
    assert args.transcript == [("plain", None)]
    assert args.output_dir == "."


def test_transcript_option_does_not_swallow_urls():
    args = yt2html.parse_args(["--transcript", "plain,timestamps", "URL1", "URL2"])
    assert args.sources == ["URL1", "URL2"]


def test_invalid_transcript_option_is_rejected(capsys):
    with pytest.raises(SystemExit):
        yt2html.parse_args(["URL", "--transcript", "plain,plain"])
    assert "given more than once" in capsys.readouterr().err


def test_from_json_saves_in_current_folder(capsys, info_file, tmp_path, monkeypatch):
    work = tmp_path / "videos"
    work.mkdir()
    monkeypatch.chdir(work)
    code, out = run(capsys, "--from-json", str(info_file))
    assert code == 0
    pages = list(work.glob("*.html"))
    assert len(pages) == 1 and pages[0].name.endswith("[TESTVIDEO01].html")
    html = pages[0].read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html and "05.11.2026" in html  # snapshot date from the file


def test_output_folder_option(capsys, info_file, tmp_path):
    out_dir = tmp_path / "pages"
    code, _ = run(capsys, "--from-json", str(info_file), "-o", str(out_dir))
    assert code == 0 and len(list(out_dir.glob("*.html"))) == 1


def test_bilingual_without_saved_translation_saves_nothing(capsys, info_file, tmp_path):
    out_dir = tmp_path / "pages"
    code, out = run(capsys, "--from-json", str(info_file), "-o", str(out_dir),
                    "--transcript", "bilingual:de")
    assert code == 1
    assert "no transcript translation into 'de'" in out
    assert not out_dir.exists() or not list(out_dir.glob("*"))


def test_bilingual_with_saved_translation(capsys, info_file, tmp_path):
    out_dir = tmp_path / "pages"
    code, _ = run(capsys, "--from-json", str(info_file), "-o", str(out_dir),
                  "--transcript", "plain,bilingual:pl")
    html = next(out_dir.glob("*.html")).read_text(encoding="utf-8")
    assert code == 0 and "transcript bilingual" in html


def test_page_is_self_contained(capsys, info_file, tmp_path, monkeypatch):
    monkeypatch.setattr(yt2html, "fetch_image", lambda url: "data:image/png;base64,AAAA")
    out_dir = tmp_path / "pages"
    run(capsys, "--from-json", str(info_file), "-o", str(out_dir))
    html = next(out_dir.glob("*.html")).read_text(encoding="utf-8")
    assert html.count("<script") == 1 and "<script src" not in html  # only the built-in player
    assert 'src="http' not in html and "url(http" not in html  # no external images


def test_failure_of_one_source_does_not_stop_others(capsys, info_file, tmp_path):
    code, out = run(capsys, "--from-json", str(tmp_path / "missing.json"), str(info_file),
                    "-o", str(tmp_path / "o"))
    assert code == 1
    assert "FAILED" in out and "Done: 1 saved, 0 skipped (page exists), 1 failed." in out


# --from-json sources: patterns, video ids and page names

def saved(folder, name="Title [TESTVIDEO01].info.json", info=None):
    import json
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(info or {"id": "TESTVIDEO01", "title": "Title"}), encoding="utf-8")
    return path


def test_from_json_pattern_is_expanded_with_literal_brackets(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path / "data")
    saved(tmp_path / "data", "Other [TESTVIDEO02].info.json", {"id": "TESTVIDEO02", "title": "O"})
    code, out = run(capsys, "--from-json", "data/*[TESTVIDEO01]*", "-o", "pages")
    assert code == 0 and len(list((tmp_path / "pages").glob("*.html"))) == 1
    code, out = run(capsys, "--from-json", "data/*.info.json", "-o", "pages2")
    assert code == 0 and len(list((tmp_path / "pages2").glob("*.html"))) == 2


def test_from_json_pattern_without_matches(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "--from-json", "data/*nothing*.info.json")
    assert code == 1 and "FAILED: data/*nothing*.info.json: no file matches" in out


def test_from_json_by_video_id_or_name_in_the_output_folder(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path / "out")
    for source in ("TESTVIDEO01", "[TESTVIDEO01]", "Title [TESTVIDEO01].html",
                   "Title [TESTVIDEO01].info.json", "elsewhere/Title [TESTVIDEO01]"):
        code, out = run(capsys, "--from-json", source, "-o", "out")
        assert code == 0, (source, out)
        assert f"Reading {Path('out') / 'Title [TESTVIDEO01].info.json'}" in out
    assert (tmp_path / "out" / "Title [TESTVIDEO01].html").exists()


def test_from_json_explains_what_was_looked_for(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "--from-json", "TESTVIDEO09", "-o", "out")
    assert code == 1
    assert "no saved data for TESTVIDEO09 in out (looked for *[TESTVIDEO09].info.json)" in out
    code, out = run(capsys, "--from-json", "missing.json")
    assert "FAILED: missing.json: no such file: missing.json" in out


def test_an_existing_file_wins_over_the_lookup(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path, "TESTVIDEO01", {"id": "TESTVIDEO01", "title": "From the file"})
    saved(tmp_path / "out", "Lookup [TESTVIDEO01].info.json", {"id": "TESTVIDEO01", "title": "L"})
    code, out = run(capsys, "--from-json", "TESTVIDEO01", "-o", "out")
    assert code == 0 and "Reading TESTVIDEO01\n" in out



def test_from_json_patterns_in_a_batch_file(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved(tmp_path / "data")
    saved(tmp_path / "data", "Other [TESTVIDEO02].info.json", {"id": "TESTVIDEO02", "title": "O"})
    (tmp_path / "list.txt").write_text("data/*.info.json\n", encoding="utf-8")
    code, out = run(capsys, "--from-json", "-a", "list.txt", "-o", "pages")
    assert code == 0 and len(list((tmp_path / "pages").glob("*.html"))) == 2
