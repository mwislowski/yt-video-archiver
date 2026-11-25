"""--json merge: a translation of one's own (an SRT file) merged into the saved data."""

import argparse
import datetime as dt
import json
import re

import pytest

import yt2html

TODAY = dt.date.today()
STARTS = (0, 6000, 120000)   # the transcript's lines in the test data (conftest.info)


def run(capsys, *argv):
    code = yt2html.main(list(argv))
    return code, capsys.readouterr().out


def stamp(ms):
    return yt2html.srt_time(ms)


def own_srt(folder, language, texts=("Hallo", "Zeilen", "[Musik]"), starts=STARTS,
            name="Title [TESTVIDEO01]", kind="own"):
    path = folder / f"{name}.{language}.{kind}.srt"
    blocks = [f"{i}\n{stamp(s)} --> {stamp(s + 2000)}\n{t}\n"
              for i, (s, t) in enumerate(zip(starts, texts), 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


@pytest.fixture
def saved(tmp_path, info):
    """Saved data next to the files: the transcript in English with YouTube's
    translation into Polish."""
    info[yt2html.TRANSLATIONS_KEY] = {"de": "German", "pl": "Polish", "fr": "French"}
    path = tmp_path / "Title [TESTVIDEO01].info.json"
    path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return path


def data(path):
    return json.loads(path.read_text(encoding="utf-8"))


def row(html, name):
    return re.search(rf"<dt>{name}</dt><dd>(.*?)</dd>", html, re.S).group(1)


def page(folder):
    return next(folder.glob("*[[]TESTVIDEO01[]].html")).read_text(encoding="utf-8")


def test_merge_adds_the_translation_and_leaves_the_page(capsys, tmp_path, saved):
    before = data(saved)
    before[yt2html.TRANSCRIPT_KEY]["missing"] = {"de": {"reason": "refused", "refused": True,
                                                        "date": "2026-11-14"}}
    saved.write_text(json.dumps(before), encoding="utf-8")
    srt = own_srt(tmp_path, "de")
    code, out = run(capsys, "--json", "merge", str(srt), "-o", str(tmp_path))
    assert code == 0
    after = data(saved)
    transcript = after[yt2html.TRANSCRIPT_KEY]
    # in a place of its own, beside YouTube's translations (25.11.2026)
    assert transcript["own"]["de"] == {
        "language": "de", "name": "German",
        "cues": [{"start": 0.0, "text": "Hallo"}, {"start": 6.0, "text": "Zeilen"},
                 {"start": 120.0, "text": "[Musik]"}],
        "own": {"file": srt.name, "date": TODAY.isoformat()}}
    assert "de" not in transcript["translations"]
    # YouTube's refusal stays noted: .translated still asks for it
    assert transcript["missing"]["de"]["refused"]
    assert transcript["translations"]["pl"] == before[yt2html.TRANSCRIPT_KEY]["translations"]["pl"]
    assert after[yt2html.UPDATES_KEY][-1]["added"] == ["own translation de"]
    assert not list(tmp_path.glob("*.html"))
    assert f"Merging {srt.name} into {saved}" in out
    assert f"Updated metadata: {saved} (own translation into 'de', 3 lines)" in out
    assert (f'The page is unchanged; to show the translation: yt2html "{saved.name}" '
            f'-o "{tmp_path}" --json load --transcript plain,dual:de') in out
    # --json write then asks YouTube for nothing in that language, unless asked
    # for its translation by name
    args = argparse.Namespace(transcript=[("plain", None), ("dual", "de"), ("plain", "de")])
    assert yt2html.missing_for_update(after, args) == (False, [])
    args = argparse.Namespace(transcript=[("dual", "de.translated")])
    assert yt2html.missing_for_update(after, args) == (False, ["de"])


def test_the_page_then_from_the_data(capsys, tmp_path, saved):
    # --json merge makes no page; --json load shows the translation
    srt = own_srt(tmp_path, "de")
    assert run(capsys, "--json", "merge", str(srt), "-o", str(tmp_path))[0] == 0
    code, out = run(capsys, saved.name, "-o", str(tmp_path), "--json", "load",
                    "--transcript", "plain,dual:de")
    assert code == 0 and "Saved page:" in out
    html = page(tmp_path)
    assert "German, own translation" in html and "auto-translated" not in html
    assert "<span class=\"text\">Zeilen</span>" in html
    assert row(html, "Transcript").endswith("2 versions: plain, dual with German "
                                            "(own translation)")
    assert row(html, "Page built").startswith(TODAY.strftime(yt2html.DATE_FORMAT))
    # the command makes the rest of the copy: the translation is not YouTube's
    command = re.search(r'<code class="command">(.*?)</code>', html).group(1)
    assert command.endswith("--transcript")
    assert "bilingual" not in command
    # a page without dual:de does not show it
    run(capsys, saved.name, "-o", str(tmp_path), "--json", "load",
        "--transcript", "plain,dual:pl")
    html = page(tmp_path)
    assert "own translation" not in html and "Polish, auto-translated" in html


def test_an_own_translation_replaces_an_earlier_own_one(capsys, tmp_path, saved):
    run(capsys, "--json", "merge", str(own_srt(tmp_path, "de")), "-o", str(tmp_path))
    earlier = data(saved)
    earlier[yt2html.TRANSCRIPT_KEY]["own"]["de"]["own"]["date"] = "2026-11-01"
    saved.write_text(json.dumps(earlier), encoding="utf-8")
    code, out = run(capsys, "--json", "merge", str(own_srt(tmp_path, "de", ("A", "B", "C"))),
                    "-o", str(tmp_path))
    assert code == 0
    assert "Replaces the own translation into 'de' added on 01.11.2026." in out
    translation = data(saved)[yt2html.TRANSCRIPT_KEY]["own"]["de"]
    assert [c["text"] for c in translation["cues"]] == ["A", "B", "C"]
    assert translation["own"]["date"] == TODAY.isoformat()


@pytest.mark.parametrize("make, message", [
    (lambda folder: own_srt(folder, "en"),
     "'en' is the language of the transcript itself (English, auto-generated); corrections to "
     "it go in 'Title [TESTVIDEO01].en.fixed.srt'"),
    (lambda folder: own_srt(folder, "en-GB"), "'en-GB' is the language of the transcript itself"),
    (lambda folder: own_srt(folder, "de", ("a", "b")),
     "2 lines in the file, 3 in the transcript (English, auto-generated): not made from the "
     "transcript's captions"),
    (lambda folder: own_srt(folder, "de", starts=(0, 6001, 120000)),
     "line 2 starts at 00:00:06,001, the transcript's at 00:00:06,000 (English, "
     "auto-generated): not made from the transcript's captions"),
    (lambda folder: own_srt(folder, "de", ("a", "", "c")), "line 2 of Title [TESTVIDEO01].de.own.srt has no text"),
    (lambda folder: own_srt(folder, "qq"), "Title [TESTVIDEO01].qq.own.srt: 'qq' is not a "
     "language code (e.g. pl, en, pt-BR)"),
    (lambda folder: own_srt(folder, "de", name="Title"), "Title.de.own.srt: no [VIDEO_ID] in the name"),
    (lambda folder: own_srt(folder, "de", name="Other [TESTVIDEO02]"),
     "no saved data for TESTVIDEO02 next to the file (looked for *[TESTVIDEO02]*.info.json in"),
    (lambda folder: folder / "missing.de.own.srt", "no such file: "),
    (lambda folder: folder / "*.xx.own.srt", "no file matches "),
])
def test_nothing_is_merged_when_the_file_does_not_fit(capsys, tmp_path, saved, make, message):
    before = saved.read_bytes()
    code, out = run(capsys, "--json", "merge", str(make(tmp_path)), "-o", str(tmp_path))
    assert code == 1 and message in out and "FAILED" in out
    assert saved.read_bytes() == before


def test_names_that_are_no_translation_of_ones_own(capsys, tmp_path, saved):
    for name in ("Title [TESTVIDEO01].de.srt", "Title [TESTVIDEO01].de.translated.srt",
                 "Title [TESTVIDEO01].own.srt"):
        (tmp_path / name).write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
        code, out = run(capsys, "--json", "merge", str(tmp_path / name), "-o", str(tmp_path))
        assert code == 1 and (f"{name}: the name ends with '.CODE.own.srt' (a translation of "
                              "your own, e.g. 'Title [ID].pl.own.srt') or '.CODE.fixed.srt' (the "
                              "transcript with your corrections, e.g. 'Title [ID].en.fixed.srt')") in out


def test_data_without_a_transcript(capsys, tmp_path, saved):
    for value in (None, "absent"):
        info = data(saved)
        if value is None:
            info[yt2html.TRANSCRIPT_KEY] = None
        else:
            del info[yt2html.TRANSCRIPT_KEY]
        saved.write_text(json.dumps(info), encoding="utf-8")
        code, out = run(capsys, "--json", "merge", str(own_srt(tmp_path, "de")),
                        "-o", str(tmp_path))
        assert code == 1 and ("the saved data have no transcript to translate (download it "
                              "with --json write --transcript first)") in out


def test_srt_files_as_editors_write_them(tmp_path):
    path = tmp_path / "x.srt"
    # a byte order mark, Windows line ends, two lines of text, a block without its number,
    # a full stop before the milliseconds, extra blank lines
    path.write_bytes("﻿1\r\n00:00:00,000 --> 00:00:02,000\r\nfirst\r\n  second \r\n\r\n\r\n"
                     "00:00:06.000 --> 00:00:08,000\r\nthird\r\n\r\n3\r\n"
                     "01:02:03,450 --> 01:02:05,000\r\nfourth\r\n".encode("utf-8"))
    assert yt2html.srt_cues(path) == [{"start": 0.0, "text": "first second"},
                                      {"start": 6.0, "text": "third"},
                                      {"start": 3723.45, "text": "fourth"}]
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n\nnot a block\nat all\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match=r"x.srt is not an SRT file \(block 2: 'not a block'\)"):
        yt2html.srt_cues(path)
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="x.srt is not an SRT file"):
        yt2html.srt_cues(path)
    path.write_text("1\n2\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not an SRT file"):   # the timing line comes second at most
        yt2html.srt_cues(path)


def test_a_pattern_merges_every_file(capsys, tmp_path, saved):
    own_srt(tmp_path, "de")
    own_srt(tmp_path, "FR")   # any case of letters; the code as YouTube writes it
    (tmp_path / "Title [TESTVIDEO01].FR.own.srt").rename(tmp_path / "Title [TESTVIDEO01].FR.OWN.SRT")
    code, out = run(capsys, "--json", "merge", str(tmp_path / "*.own.srt"),
                    str(tmp_path / "*.OWN.SRT"), "-o", str(tmp_path))
    assert code == 0 and "Done: 2 saved, 0 skipped (page exists), 0 failed." in out
    transcript = data(saved)[yt2html.TRANSCRIPT_KEY]
    assert set(transcript["translations"]) == {"pl"} and set(transcript["own"]) == {"de", "fr"}
    assert transcript["own"]["fr"]["name"] == "French"
    # one run of its own for each file (the order differs between systems: Windows
    # matches names in any case, so there both patterns find both files, taken once)
    assert sorted(u["added"] for u in data(saved)[yt2html.UPDATES_KEY]) == [
        ["own translation de"], ["own translation fr"]]


def test_cookies_note(capsys, tmp_path):
    yt2html.parse_args(["--json", "merge", "x.de.own.srt", "--cookies", "firefox"])
    assert ("Note: --cookies has no effect with --json merge (nothing is "
            "downloaded).") in capsys.readouterr().out


# On the page ------------------------------------------------------------------

def own_info(info, **own):
    transcript = info[yt2html.TRANSCRIPT_KEY]
    for language, day in own.items():
        transcript["translations"][language] = {
            "language": language, "name": language.upper(), "cues": transcript["cues"],
            "own": {"file": f"x.{language}.own.srt", "date": day}}
    return info


def test_the_line_of_own_translations():
    # by the day added, then by the code
    assert yt2html.own_translations_line([("fr", "2026-11-22"), ("de", "2026-11-25"),
                                          ("en", "2026-11-22")]) == (
        'own translations: <span class="chosen">en</span>, <span class="chosen">fr</span> '
        '(added 22.11.2026), <span class="chosen">de</span> (added 25.11.2026)')


def test_own_translations_under_the_languages_table(info):
    from test_about import video_formats_example
    info["formats"] = video_formats_example()
    own_info(info, de="2026-11-22", fr="2026-11-25")
    spec = [("plain", None), ("dual", "fr"), ("dual", "pl"), ("dual", "de")]
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5), transcript_spec=spec)
    formats = re.search(r'<details class="formats-info">(.*?)</details>', html, re.S).group(1)
    lines = re.findall(r'<div class="translation">(.*?)</div>', formats)
    assert lines[-1] == ('own translations: <span class="chosen">de</span> (added 22.11.2026), '
                         '<span class="chosen">fr</span> (added 25.11.2026)')
    assert row(html, "Transcript").endswith(
        "4 versions: plain, dual with FR (own translation), dual with Polish "
        "(auto-translated), dual with DE (own translation)")
    assert html.count(", own translation</span>") == 2 and "Polish, auto-translated" in html
    # only YouTube's translation in the command
    assert '<code class="command">yt2html TESTVIDEO01 --page comments:all,cards:no --transcript plain,dual:pl</code>' in html
    # what the page does not show is not named: neither in the line nor in the command
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5),
                               transcript_spec=[("dual", "pl")])
    assert "own translation" not in html and len(re.findall(
        r'<div class="translation">', html)) == 1
    assert "--transcript dual:pl</code>" in html


def test_only_an_own_translation_shown(info):
    own_info(info, de="2026-11-22")
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5),
                               transcript_spec=[("dual", "de")])
    assert row(html, "Transcript").endswith("1 version: dual with DE (own translation)")
    # the command does not claim a version the page does not have
    assert '<code class="command">yt2html TESTVIDEO01 --page comments:all,cards:no</code>' in html


def test_no_own_line_without_formats(info):
    own_info(info, de="2026-11-22")
    html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5),
                               transcript_spec=[("dual", "de")])
    assert "none in the data" in html and "own translations:" not in html


def test_own_beside_youtube_s_translation(capsys, tmp_path, saved):
    # the saved data have YouTube's Polish: one's own goes beside it (25.11.2026)
    code, out = run(capsys, "--json", "merge", str(own_srt(tmp_path, "pl", ("Raz", "Dwa", "Trzy"))),
                    "-o", str(tmp_path))
    assert code == 0
    transcript = data(saved)[yt2html.TRANSCRIPT_KEY]
    assert "pl" in transcript["translations"] and "pl" in transcript["own"]
    run(capsys, saved.name, "-o", str(tmp_path), "--json", "load", "--transcript",
        "dual:pl,dual:pl.translated,plain:pl")
    html = page(tmp_path)
    # without a kind: one's own first; by name: YouTube's
    assert row(html, "Transcript").endswith(
        "3 versions: dual with Polish (own translation), dual with Polish (auto-translated), "
        "plain in Polish (own translation)")
    assert html.count('data-lang="pl"') == 3 and "Raz" in html


def test_own_translations_saved_before(capsys, tmp_path, saved):
    # data saved before 25.11.2026 keep one's own among the translations: read
    # as one's own, moved to its place when merged again
    old = data(saved)
    old[yt2html.TRANSCRIPT_KEY]["translations"]["de"] = {
        "language": "de", "name": "German", "cues": [{"start": 0.0, "text": "Alt"}],
        "own": {"file": "x.de.own.srt", "date": "2026-11-20"}}
    saved.write_text(json.dumps(old), encoding="utf-8")
    transcript = data(saved)[yt2html.TRANSCRIPT_KEY]
    assert set(yt2html.own_of(transcript)) == {"de"}
    assert set(yt2html.translations_of(transcript)) == {"pl"}
    assert yt2html.version_source(transcript, "dual", "de") == "de.own"
    code, out = run(capsys, "--json", "merge", str(own_srt(tmp_path, "de")), "-o", str(tmp_path))
    assert "Replaces the own translation into 'de' added on 20.11.2026." in out
    transcript = data(saved)[yt2html.TRANSCRIPT_KEY]
    assert "de" not in transcript["translations"] and transcript["own"]["de"]["cues"][0]["text"] == "Hallo"


def test_youtube_s_translation_does_not_drop_an_own_one_saved_before(capsys, tmp_path, saved, youtube):
    old = data(saved)
    transcript = old[yt2html.TRANSCRIPT_KEY]
    transcript["translations"] = {"pl": {
        "language": "pl", "name": "Polish", "cues": [{"start": 0.0, "text": "Moje"}],
        "own": {"file": "x.pl.own.srt", "date": "2026-11-20"}}}
    transcript.pop("missing", None)
    saved.write_text(json.dumps(old), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(tmp_path), "--json", "write",
                    "--transcript", "dual:pl.translated")
    assert code == 0
    after = data(saved)[yt2html.TRANSCRIPT_KEY]
    assert after["own"]["pl"]["cues"][0]["text"] == "Moje"
    assert "own" not in after["translations"]["pl"] and after["translations"]["pl"]["cues"]


# The transcript with one's own corrections: "Title [ID].en.fixed.srt" (25.11.2026) ---------

def fixed_srt(folder, texts=("Hello and welcome!", "Lines", "[music]"), **kw):
    return own_srt(folder, "en", texts, kind="fixed", **kw)


def test_corrections_of_the_transcript(capsys, tmp_path, saved):
    before = data(saved)[yt2html.TRANSCRIPT_KEY]
    srt = fixed_srt(tmp_path)
    code, out = run(capsys, "--json", "merge", str(srt), "-o", str(tmp_path))
    assert code == 0
    transcript = data(saved)[yt2html.TRANSCRIPT_KEY]
    # YouTube's text stays; the corrections beside it
    assert transcript["cues"] == before["cues"]
    assert transcript["fixed"] == {"cues": [{"start": 0.0, "text": "Hello and welcome!"},
                                            {"start": 6.0, "text": "Lines"},
                                            {"start": 120.0, "text": "[music]"}],
                                   "file": srt.name, "date": TODAY.isoformat()}
    assert data(saved)[yt2html.UPDATES_KEY][-1]["added"] == ["own corrections of the transcript"]
    assert f"Updated metadata: {saved} (own corrections of the transcript, 3 lines)" in out
    assert (f'to show it: yt2html "{saved.name}" -o "{tmp_path}" --json load --transcript'
            in out) and not list(tmp_path.glob("*.html"))
    run(capsys, saved.name, "-o", str(tmp_path), "--json", "load", "--transcript",
        "plain,dual:pl,srt")
    html = page(tmp_path)
    # on the page: the corrected text everywhere the original is, with a note
    assert "Hello and welcome!" in html and "English, auto-generated, with own corrections" in html
    assert '<span class="text">Lines</span><span class="text">pl: lines</span>' in html
    assert row(html, "Transcript").endswith(
        f"2 versions: plain, dual with Polish (auto-translated); the original with own "
        f"corrections ({srt.name}, {TODAY.strftime(yt2html.DATE_FORMAT)}); SRT files next to "
        f"the page: en (auto-generated), pl (translated)")
    # the SRT file of the original: YouTube's text
    original = next(tmp_path.glob("*.en.auto-generated.srt")).read_text("utf-8")
    assert "Hello and welcome!" not in original
    # the word in either case
    upper = srt.rename(tmp_path / "Title [TESTVIDEO01].en.FIXED.srt")
    code, out = run(capsys, "--json", "merge", str(upper), "-o", str(tmp_path))
    assert code == 0 and data(saved)[yt2html.TRANSCRIPT_KEY]["fixed"]["file"] == upper.name
    assert yt2html.fixed_view({"cues": [{"start": 0, "text": "a"}],
                               "fixed": {"cues": [{"start": 0, "text": "b"}, {"start": 1}]}}
                              )["cues"][0]["text"] == "a"    # (does not fit: YouTube's)


def test_corrections_replaced_and_checked(capsys, tmp_path, saved):
    run(capsys, "--json", "merge", str(fixed_srt(tmp_path)), "-o", str(tmp_path))
    earlier = data(saved)
    earlier[yt2html.TRANSCRIPT_KEY]["fixed"]["date"] = "2026-11-01"
    saved.write_text(json.dumps(earlier), encoding="utf-8")
    code, out = run(capsys, "--json", "merge", str(fixed_srt(tmp_path, ("A", "B", "C"))),
                    "-o", str(tmp_path))
    assert code == 0 and "Replaces the corrections added on 01.11.2026." in out
    assert [c["text"] for c in data(saved)[yt2html.TRANSCRIPT_KEY]["fixed"]["cues"]] == ["A", "B", "C"]
    # another language, other lines: errors, nothing saved
    code, out = run(capsys, "--json", "merge", str(own_srt(tmp_path, "de", kind="fixed")),
                    "-o", str(tmp_path))
    assert code == 1 and ("'de' is not the language of the transcript (English, auto-generated, "
                          "with own corrections): corrections are in its language, '.en.fixed.srt'; "
                          "a translation of your own is '.de.own.srt'") in out
    code, out = run(capsys, "--json", "merge", str(fixed_srt(tmp_path, ("a", "b"), starts=STARTS[:2])),
                    "-o", str(tmp_path))
    assert code == 1 and "2 lines in the file, 3 in the transcript" in out
    assert [c["text"] for c in data(saved)[yt2html.TRANSCRIPT_KEY]["fixed"]["cues"]] == ["A", "B", "C"]


def test_corrections_with_transcript_ovr_and_page_ovr(capsys, tmp_path, saved, youtube):
    run(capsys, "--json", "merge", str(fixed_srt(tmp_path)), "-o", str(tmp_path))
    code, out = run(capsys, "TESTVIDEO01", "-o", str(tmp_path), "--json", "write",
                    "--transcript", "plain,ovr")
    assert code == 0 and data(saved)[yt2html.TRANSCRIPT_KEY]["fixed"]["file"].endswith(".en.fixed.srt")
    # no longer fitting: dropped, with a message
    old = data(saved)
    old[yt2html.TRANSCRIPT_KEY]["fixed"]["cues"][1]["start"] = 7.0
    saved.write_text(json.dumps(old), encoding="utf-8")
    code, out = run(capsys, "TESTVIDEO01", "-o", str(tmp_path), "--json", "write",
                    "--transcript", "plain,ovr")
    assert "Own corrections of the transcript dropped: they no longer fit it (line 2 starts" in out
    assert "fixed" not in data(saved)[yt2html.TRANSCRIPT_KEY]
    # --page ovr --json write names what is lost
    run(capsys, "--json", "merge", str(fixed_srt(tmp_path)), "-o", str(tmp_path))
    assert "(lost: own corrections of the transcript)" in yt2html.replaced_data_note(saved, data(saved))
