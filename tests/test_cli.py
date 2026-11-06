"""The command line: options, --version, --from-json and what gets saved."""

import json

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
    assert "<script" not in html
    assert 'src="http' not in html and "url(http" not in html  # no external images


def test_failure_of_one_source_does_not_stop_others(capsys, info_file, tmp_path):
    code, out = run(capsys, "--from-json", str(tmp_path / "missing.json"), str(info_file),
                    "-o", str(tmp_path / "o"))
    assert code == 1
    assert "FAILED" in out and "Done: 1 saved, 0 skipped (page exists), 1 failed." in out
