"""--transcript versions in any language (plain:CODE, timed:CODE, dual:CODE),
the original without a code, --transcript ovr and --olang."""
import datetime as dt
import json
import re
import shutil
from pathlib import Path

import pytest

import yt2html
from conftest import caption_formats

DAY = dt.date(2026, 11, 24)
SHORT = Path(__file__).parent / "data" / "short.webm"


def render(info, spec):
    return yt2html.render_page(info, None, {}, DAY, transcript_spec=spec)


def sections(html):
    return re.findall(r'<section class="(transcript[^"]*)"([^>]*)>', html)


def row(html, name):
    return re.search(rf"<dt>{name}</dt><dd>(.*?)</dd>", html, re.S).group(1)


# Resolving the versions for one video ------------------------------------------------------

@pytest.mark.parametrize("spec, versions, notes", [
    ([("plain", None), ("timed", "pl")], [("plain", None), ("timed", "pl")], []),
    # a code in the original's language is the original, any region
    ([("plain", "en"), ("timed", "en-GB")], [("plain", None), ("timed", None)], []),
    # dual into the original has nothing beside it: timed
    ([("dual", "en")], [("timed", None)], ["dual:en: the original is English; shown as timed"]),
    # the same version twice, as it turns out now: once
    ([("plain", None), ("plain", "en")], [("plain", None)],
     ["plain:en: the same as plain; shown once"]),
    ([("timed", None), ("dual", "en-US"), ("dual", "pl")], [("timed", None), ("dual", "pl")],
     ["dual:en-US: the original is English; shown as timed",
      "dual:en-US: the same as timed; shown once"]),
])
def test_resolve_versions(info, spec, versions, notes):
    assert yt2html.resolve_versions(spec, info[yt2html.TRANSCRIPT_KEY]) == (versions, notes)


def test_the_languages_to_ask_for():
    spec = [("plain", None), ("timed", "pl"), ("dual", "pl"), ("plain", "en"), ("dual", "de")]
    assert yt2html.version_languages(spec) == ["pl", "en", "de"]
    assert yt2html.wanted_translations(spec, {"language": "en"}) == ["pl", "de"]
    assert yt2html.wanted_translations(spec, None) == ["pl", "en", "de"]


# The page -----------------------------------------------------------------------------------

def test_a_translation_on_its_own(info):
    html = render(info, [("plain", None), ("timed", "pl")])
    assert sections(html) == [("transcript original", ""),
                              ("transcript with-timestamps", ' data-lang="pl"')]
    assert "Polish, auto-translated, with timestamps" in html
    assert '<span class="text">pl: hello</span>' in html
    assert row(html, "Transcript").endswith(
        "2 versions: plain, timed in Polish (auto-translated)")


def test_only_translations_bring_the_original_hidden(info):
    # the lines under the video always come from the original
    html = render(info, [("plain", "pl")])
    found = sections(html)
    assert found == [("transcript", ' data-lang="pl"'),
                     ("transcript with-timestamps original", " hidden")]
    assert row(html, "Transcript").endswith("1 version: plain in Polish (auto-translated)")
    # with the original among them: not again
    assert len(sections(render(info, [("timed", "pl"), ("plain", None)]))) == 2


def test_the_change_of_speaker_goes_into_every_translation(info):
    transcript = info[yt2html.TRANSCRIPT_KEY]
    transcript["cues"][1]["text"] = ">> first line second line"
    for spec in ([("timed", "pl")], [("dual", "pl")], [("plain", "pl")]):
        html = render(info, spec)
        part = html[html.index('data-lang="pl"'):]
        assert "pl: lines" in part
        before = part[:part.index("pl: lines")]
        assert before.rstrip().endswith(
            yt2html.PLAIN_SPEAKER_MARK if spec[0][0] == "plain" else yt2html.SPEAKER_MARK), spec
    # a translation that kept the mark: one, not two
    translation = transcript["translations"]["pl"]["cues"]
    translation[1]["text"] = ">> pl: lines"
    html = render(info, [("timed", "pl")])
    assert html.count(yt2html.SPEAKER_MARK + " pl: lines") == 1


def test_dual_into_the_original_and_the_row(info):
    html = render(info, [("dual", "en"), ("dual", "pl"), ("timed", "fi")])
    assert [c for c, _ in sections(html)] == ["transcript with-timestamps original",
                                              "transcript bilingual"]
    assert row(html, "Transcript") == (
        "English (original language, auto-generated); 2 versions: timed, dual with Polish "
        "(auto-translated); not available: timed:fi; dual:en: the original is English; "
        "shown as timed")


def test_the_console_names_what_changed(capsys, tmp_path, info):
    data = tmp_path / "Title [TESTVIDEO01].info.json"
    data.write_text(json.dumps(info), encoding="utf-8")
    assert yt2html.main([data.name, "-o", str(tmp_path), "--json", "load",
                         "--transcript", "timed,dual:en"]) == 0
    out = capsys.readouterr().out
    assert "  Transcript: dual:en: the original is English; shown as timed." in out
    assert "  Transcript: dual:en: the same as timed; shown once." in out


def test_the_player_takes_the_original_and_one_button_per_translation():
    js = yt2html.PLAYER_JS
    assert "var lists = everyList.filter(function (list) { return !list.section.hidden; });" in js
    assert "var source = originals.filter(timed)[0] || originals[0];" in js
    assert "if (!lang || seenLangs[lang]) return false;" in js
    assert "textOf(translation.els[j], column(translation))" in js


def test_in_the_browser(info, chromium, tmp_path):
    """Only a Polish translation on the page: the lines under the video are
    the original's, the pl button shows the translation from column 0."""
    page_path = tmp_path / "Title [TESTVIDEO01].html"
    page_path.write_text(render(info, [("timed", "pl"), ("dual", "pl")]), encoding="utf-8")
    shutil.copy(SHORT, tmp_path / "Title [TESTVIDEO01].webm")
    page = chromium.new_page()
    page.goto(page_path.as_uri())
    page.click(".fact.play")
    page.wait_for_function("document.querySelector('video').readyState >= 1")
    page.click("[data-do=subtitles]")
    assert page.inner_text(".now-playing .current") == "Hello and welcome"
    buttons = page.locator(".langs .lang")
    assert buttons.count() == 1 and buttons.inner_text() == "pl"   # pl once
    buttons.click()
    assert page.inner_text(".now-playing .translation") == "pl: hello"
    # Follow goes through the two sections on the page, not the hidden one
    page.click("[data-do=follow]")
    assert page.inner_text("[data-do=follow]") == "Follow 1/2"
    page.close()


# --transcript ovr ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [[], ["--json", "load"], ["--page", "ovr"]])
def test_ovr_needs_json_write_or_refresh(capsys, argv):
    with pytest.raises(SystemExit):
        yt2html.parse_args(["URL", "--transcript", "plain,ovr", *argv])
    assert ("--transcript ovr downloads the saved transcript again, so it needs --json write "
            "or --json refresh") in capsys.readouterr().err


def test_ovr_is_not_a_version():
    args = yt2html.parse_args(["URL", "--transcript", "timed,ovr", "--json", "write"])
    assert args.transcript == [("timed", None)] and args.transcript_ovr
    assert not yt2html.parse_args(["URL", "--transcript"]).transcript_ovr


def test_ovr_downloads_the_transcript_again_and_keeps_own_translations_that_fit(
        capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    transcript = data[yt2html.TRANSCRIPT_KEY]
    cues = [{"start": c["start"], "text": f"de {i}"} for i, c in enumerate(transcript["cues"])]
    transcript["translations"]["de"] = {"language": "de", "name": "German", "cues": cues,
                                        "own": {"file": "x.de.own.srt", "date": "2026-11-01"}}
    # Polish, which YouTube offers (refused before), of one's own now
    transcript["translations"]["pl"] = {"language": "pl", "name": "Polish", "cues": cues,
                                        "own": {"file": "x.pl.own.srt", "date": "2026-11-01"}}
    transcript["translations"]["it"] = {"language": "it", "name": "Italian", "cues": cues[:1],
                                        "own": {"file": "x.it.own.srt", "date": "2026-11-01"}}
    transcript["cues"][0]["text"] = "an old line"
    saved.write_text(json.dumps(data), encoding="utf-8")
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,dual:de,dual:pl,ovr"])
    out = capsys.readouterr().out
    assert code == 0
    after = json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]
    assert after["cues"][0]["text"] != "an old line"          # downloaded again
    assert "de" in after["translations"] and after["translations"]["de"]["own"]
    assert "it" not in after["translations"]
    assert "Own translation into 'it' dropped: it no longer fits the transcript" in out
    assert "fix x.it.own.srt and merge it again." in out
    asked = [url for url in youtube["ydl"].urls if "tlang" in url]
    assert not any("tlang=de" in url or "tlang=pl" in url for url in asked)   # one's own
    assert after["translations"]["pl"]["own"]


# --olang -------------------------------------------------------------------------------------

def test_olang_parsing(capsys):
    assert yt2html.parse_args(["URL", "--olang", "PT-br"]).olang == "pt-BR"
    for argv, message in (
            (["--olang", "jp"], "'jp' is not a language code"),
            (["--olang", "en", "--transcript", "dual:en-GB"],
             "dual:en-GB is the original itself (--olang en): nothing to put beside it; use timed"),
            (["--olang", "en", "--transcript", "plain,plain:en"],
             "plain and plain:en are the same transcript (--olang en); give one of them"),
            (["--olang", "en", "--json", "load"],
             "--olang chooses what is downloaded, so it cannot be used with --json load"),
            (["--olang", "en", "--json", "merge"], "cannot be used with --json merge")):
        with pytest.raises(SystemExit):
            yt2html.parse_args(["URL", *argv])
        assert message in capsys.readouterr().err, argv


def dubbed():
    """English original by the data, a Polish dub too; the user knows it is Polish."""
    auto = {f"{lang}-orig": caption_formats(f"{lang} (Original)") for lang in ("en", "pl")}
    return {"automatic_captions": auto, yt2html.CAPTION_KEY: {"language": "en", "kind": "asr"},
            "formats": [{"format_id": "251-0", "language": "en", "acodec": "opus", "vcodec": "none",
                         "language_preference": 10, "format_note": "original"},
                        {"format_id": "251-1", "language": "pl", "acodec": "opus", "vcodec": "none"}]}


def test_olang_wins_over_what_the_program_finds():
    info = dubbed()
    assert yt2html.transcript_language(info) == "en"
    info[yt2html.OLANG_KEY] = "pl"
    assert yt2html.transcript_language(info) == "pl"
    assert yt2html.choose_caption_track(info)[:2] == ("auto", "pl")
    # no captions in that language: none, rather than another language's
    info[yt2html.OLANG_KEY] = "de"
    info["subtitles"] = {"fr": caption_formats("French")}
    assert yt2html.choose_caption_track(info) is None
    del info[yt2html.OLANG_KEY]
    assert yt2html.choose_caption_track(info)[:2] == ("auto", "en")


def test_olang_ends_the_uncertainty():
    from test_transcript import without_audio_tracks
    info = without_audio_tracks()
    assert yt2html.original_uncertain(info)
    info[yt2html.OLANG_KEY] = "pl"
    assert not yt2html.original_uncertain(info)


def test_olang_without_captions_in_it(fake_ydl):
    info = {**dubbed(), yt2html.OLANG_KEY: "de"}
    assert yt2html.fetch_transcript(fake_ydl(), info) is None
    assert info[yt2html.TRANSCRIPT_MISSING_KEY] == ("no captions in 'de', the original language "
                                                     "set with --olang")


def test_olang_picks_the_main_audio():
    info = dubbed()
    assert yt2html.video_choice(info).original[0] == "en"
    info[yt2html.OLANG_KEY] = "pl"
    assert yt2html.video_choice(info).original[0] == "pl"
    info[yt2html.OLANG_KEY] = "fr"   # no such track: what YouTube marks
    assert yt2html.video_choice(info).original[0] == "en"


def test_olang_in_fresh_data_and_the_page_choices():
    fresh = {"id": "x", yt2html.OLANG_KEY: "pl"}
    assert yt2html.with_page_choices(fresh, {yt2html.OLANG_KEY: "de"})[yt2html.OLANG_KEY] == "pl"
    assert yt2html.with_page_choices({"id": "x"}, {yt2html.OLANG_KEY: "de"})[yt2html.OLANG_KEY] == "de"
    assert yt2html.OLANG_KEY not in yt2html.with_page_choices({"id": "x"}, None)


def test_olang_on_the_page(info):
    info[yt2html.CARDS_KEY] = []
    info[yt2html.OLANG_KEY] = "en"
    html = render(info, [("plain", None)])
    assert "the original set in the command" in row(html, "Transcript")
    assert "--olang en" in re.search(r'<code class="command">(.*?)</code>', html).group(1)


def test_olang_with_saved_data(capsys, saved, youtube):
    # recorded, also when the page is there and nothing else is missing; a
    # transcript in another language stays, with a warning
    assert yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript"]) == 0
    capsys.readouterr()
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--olang", "pl", "--transcript"])
    out = capsys.readouterr().out
    data = json.loads(saved.read_text("utf-8"))
    assert code == 0 and data[yt2html.OLANG_KEY] == "pl"
    assert data[yt2html.UPDATES_KEY][-1]["added"] == ["original language pl"]
    assert ("  Warning: the saved transcript is in 'en', not in 'pl' (--olang); --transcript ovr "
            "downloads it again in 'pl'.") in out
    # the same again: nothing new, the page stays
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--olang", "pl", "--transcript"])
    assert code == 0 and "the page stays as it is" in capsys.readouterr().out


# --transcript srt ------------------------------------------------------------------------------

def test_srt_is_a_parameter_not_a_version():
    assert yt2html.transcript_spec("srt") == [("plain", None), ("srt", None)]
    assert yt2html.transcript_spec("timed:pl,SRT") == [("timed", "pl"), ("srt", None)]
    args = yt2html.parse_args(["URL", "--transcript", "dual:pl,srt"])
    assert args.transcript_srt and ("srt", None) in args.transcript
    assert yt2html.resolve_versions(args.transcript, {"language": "en"})[0] == [("dual", "pl")]
    assert yt2html.version_languages(args.transcript) == ["pl"]


def test_cues_srt():
    cues = [{"start": 1.0, "end": 2.5, "text": ">> Hello"}, {"start": 3.0, "text": "next"},
            {"start": 4.25, "text": "last"}]
    assert yt2html.cues_srt(cues).decode() == (
        "1\n00:00:01,000 --> 00:00:02,500\n>> Hello\n\n"
        "2\n00:00:03,000 --> 00:00:04,250\nnext\n\n"      # no end saved: to the next line
        "3\n00:00:04,250 --> 00:00:09,250\nlast\n")       # the last: 5 s


def test_json3_cues_keep_their_end():
    data = {"events": [{"tStartMs": 1000, "dDurationMs": 1500, "segs": [{"utf8": "a"}]},
                       {"tStartMs": 3000, "segs": [{"utf8": "b"}]}]}
    assert yt2html.parse_json3_captions(data) == [{"start": 1.0, "end": 2.5, "text": "a"},
                                                  {"start": 3.0, "text": "b"}]


def test_srt_tracks(info):
    transcript = info[yt2html.TRANSCRIPT_KEY]
    transcript["translations"]["de"] = {"language": "de", "cues": transcript["cues"],
                                        "own": {"file": "x.de.own.srt", "date": "2026-11-01"}}
    tracks = yt2html.srt_tracks(info, [("plain", None), ("timed", None), ("dual", "pl"),
                                       ("timed", "pl"), ("plain", "de"), ("timed", "fi")])
    # the original once, YouTube's Polish once; one's own and what is not there: none
    assert [(code, kind) for code, kind, _ in tracks] == [("en", "auto-generated"),
                                                          ("pl", "translated")]
    transcript["kind"] = "manual"
    assert yt2html.srt_tracks(info, [("plain", "pl")])[0][:2] == ("pl", "translated")
    assert yt2html.srt_tracks(info, [("plain", None)])[0][:2] == ("en", "author")
    info[yt2html.TRANSCRIPT_KEY] = None
    assert yt2html.srt_tracks(info, [("plain", None)]) == []


def test_srt_files_written_only_when_they_change(tmp_path, info, capsys):
    page_path = tmp_path / "Title [TESTVIDEO01].html"
    spec = [("plain", None), ("dual", "pl")]
    assert yt2html.write_srt_files(info, spec, page_path) == [".en.auto-generated.srt",
                                                              ".pl.translated.srt"]
    en = tmp_path / "Title [TESTVIDEO01].en.auto-generated.srt"
    assert en.read_text("utf-8").startswith("1\n00:00:00,000 --> 00:00:06,000\nHello and welcome\n")
    assert f"Saved subtitles: {en}" in capsys.readouterr().out
    # the same again: left as it is, its date too
    renamed = tmp_path / "01 My name [TESTVIDEO01].en.auto-generated.srt"
    en.rename(renamed)
    before = renamed.stat().st_mtime_ns
    assert yt2html.write_srt_files(info, spec, page_path) == []
    assert renamed.stat().st_mtime_ns == before and not en.exists()
    # the transcript changed: written over, under the name it has
    info[yt2html.TRANSCRIPT_KEY]["cues"][0]["text"] = "Changed"
    assert yt2html.write_srt_files(info, spec, page_path) == [".en.auto-generated.srt"]
    assert "Changed" in renamed.read_text("utf-8")


def test_srt_with_the_page_from_saved_data(capsys, tmp_path, info):
    # without YouTube: the SRT files come from the saved data
    data = tmp_path / "Title [TESTVIDEO01].info.json"
    data.write_text(json.dumps(info), encoding="utf-8")
    assert yt2html.main([data.name, "-o", str(tmp_path), "--json", "load",
                         "--transcript", "timed:pl,srt"]) == 0
    names = sorted(p.name.split("]")[-1] for p in tmp_path.glob("*.srt"))
    assert names == [".pl.translated.srt"]   # the versions asked for (not the hidden original)
    html = next(tmp_path.glob("*.html")).read_text("utf-8")
    assert row(html, "Transcript").endswith("; SRT files next to the page: pl (translated)")
    assert yt2html.main([data.name, "-o", str(tmp_path), "--json", "load",
                         "--transcript", "dual:pl,srt"]) == 0     # dual: the original too
    assert len(list(tmp_path.glob("*.srt"))) == 2
    assert re.search(r'<code class="command">.*--transcript timed:pl,srt</code>', html)


def test_srt_and_a_page_that_is_there(capsys, saved, youtube):
    # without saved data the page is skipped, and its SRT files with it (said so)
    folder = saved.parent
    assert yt2html.main(["TESTVIDEO01", "-o", str(folder), "--json", "write", "--transcript"]) == 0
    capsys.readouterr()
    assert yt2html.main(["TESTVIDEO01", "-o", str(folder), "--transcript", "srt"]) == 0
    assert ("; SRT files are made with the page (--page ovr, or --json write with saved data)"
            in capsys.readouterr().out)
    assert not list(folder.glob("*.srt"))
    # with saved data and nothing else missing: a missing SRT file is something missing
    assert yt2html.main(["TESTVIDEO01", "-o", str(folder), "--json", "write",
                         "--transcript", "srt"]) == 0
    assert "Saved subtitles:" in capsys.readouterr().out and list(folder.glob("*.srt"))
    assert yt2html.main(["TESTVIDEO01", "-o", str(folder), "--json", "write",
                         "--transcript", "srt"]) == 0
    assert "the page stays as it is" in capsys.readouterr().out
