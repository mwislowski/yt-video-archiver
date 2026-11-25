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
    assert yt2html.source_keys(spec, {}, "en") == ["pl", "de"]
    assert yt2html.source_keys(spec, {}, None) == ["pl", "en", "de"]


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
    assert "if (!group.variants.some(function (v) { return v.kind === kind; })) {" in js
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


@pytest.mark.parametrize("spec, message", [
    ("plain,dual:pl.own", "dual:pl.own is a translation of your own"),
    ("plain:PL.own,dual:de.own", "plain:pl.own, dual:de.own are translations of your own")])
def test_own_versions_need_saved_data(capsys, spec, message):
    """One's own translations are only in the saved data (--json merge): such
    a version without --json is an error before anything is downloaded."""
    with pytest.raises(SystemExit):
        yt2html.parse_args(["URL", "--transcript", spec])
    assert (f"{message}, kept only in saved data: add --json write (or --json load)"
            in capsys.readouterr().err)
    for json_parameter in ("write", "refresh", "load"):
        yt2html.parse_args(["URL", "--transcript", spec, "--json", json_parameter])


def test_an_own_version_the_data_lack_is_not_available(info):
    """Not an error of the video (before: KeyError, the video FAILED): the
    page is made, its Transcript row says what is not there."""
    html = render(info, [("plain", None), ("dual", "pl.own")])
    assert "not available: dual:pl.own" in row(html, "Transcript")
    assert sections(html) == [("transcript original", "")]


def test_page_ovr_with_an_own_version_makes_the_page(capsys, saved, youtube, monkeypatch, info):
    """--page ovr --json write replaces the data with what YouTube has (the
    own translation is lost, as the console says): the page follows the new
    data instead of failing after they were saved."""
    data = json.loads(saved.read_text("utf-8"))
    transcript = data[yt2html.TRANSCRIPT_KEY]
    yt2html.set_own(transcript, "de", {"language": "de", "name": "German",
                                       "cues": transcript["cues"],
                                       "own": {"file": "x.de.own.srt", "date": "2026-11-01"}})
    saved.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(yt2html, "fetch_info", lambda source, args: json.loads(json.dumps(info)))
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--page", "ovr",
                         "--json", "write", "--transcript", "plain,dual:de.own"])
    out = capsys.readouterr().out
    assert code == 0 and "(lost: own translation de)" in out and "FAILED" not in out
    page = next(saved.parent.glob("*.html")).read_text("utf-8")
    assert "not available: dual:de.own" in row(page, "Transcript")
    assert "own" not in json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]


def test_json_load_with_an_own_version_where_youtube_refused(capsys, saved):
    """The data note YouTube's refusal of 'pl': --json load lets plain:pl.own
    through (that check is by language) and makes the page."""
    code = yt2html.main([saved.name, "-o", str(saved.parent), "--json", "load",
                         "--transcript", "plain,plain:pl.own"])
    assert code == 0, capsys.readouterr().out
    page = next(saved.parent.glob("*.html")).read_text("utf-8")
    assert "not available: plain:pl.own" in row(page, "Transcript")


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
    # (saved among the translations before 25.11.2026: kept in their own place now)
    assert after["own"]["de"]["own"] and "de" not in after.get("translations", {})
    assert "it" not in after["own"] and "it" not in after.get("translations", {})
    assert "Own translation into 'it' dropped: it no longer fits the transcript" in out
    assert "fix x.it.own.srt and merge it again." in out
    asked = [url for url in youtube["ydl"].urls if "tlang" in url]
    assert not any("tlang=de" in url or "tlang=pl" in url for url in asked)   # one's own
    assert after["own"]["pl"]["own"]


def with_own_work(saved):
    """Saved data with an own German translation and own corrections."""
    data = json.loads(saved.read_text("utf-8"))
    transcript = data[yt2html.TRANSCRIPT_KEY]
    cues = transcript["cues"]
    yt2html.set_own(transcript, "de", {
        "language": "de", "name": "German",
        "cues": [{"start": c["start"], "text": f"de {i}"} for i, c in enumerate(cues)],
        "own": {"file": "Title [TESTVIDEO01].de.own.srt", "date": "2026-11-01"}})
    transcript["fixed"] = {"cues": [{"start": c["start"], "text": f"fixed {i}"}
                                    for i, c in enumerate(cues)],
                           "file": "Title [TESTVIDEO01].en.fixed.srt", "date": "2026-11-01"}
    saved.write_text(json.dumps(data), encoding="utf-8")
    return data[yt2html.TRANSCRIPT_KEY]


@pytest.mark.parametrize("olang", [[], ["--olang", "en-GB"]])
def test_ovr_without_captions_now_keeps_the_saved_transcript(capsys, saved, youtube,
                                                             monkeypatch, olang):
    """--transcript ovr when YouTube's data have no captions now (the saved
    transcript came from them, the language is the same): nothing new came,
    so the saved one stays whole, with a warning (before: all of it, one's
    own translations and corrections included, was gone without a word)."""
    before = with_own_work(saved)
    monkeypatch.setattr(yt2html, "choose_caption_track", lambda info: None)
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,dual:de,ovr", *olang])
    out = capsys.readouterr().out
    assert code == 0
    assert ("The saved transcript (English) stays, with all it holds: YouTube's data have no "
            "captions for it now. Try --transcript ovr again later.") in out
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.TRANSCRIPT_KEY] == before
    assert set(data[yt2html.NOT_SAVED_KEY]) == {"transcript pl", "transcript de"}  # as saved
    assert "de 0" in next(saved.parent.glob("*.html")).read_text("utf-8")


def test_ovr_with_another_olang_without_captions_drops_it_and_names_all(capsys, saved, youtube,
                                                                       monkeypatch):
    """--olang de --transcript ovr on a video without German captions: done as
    asked (the transcript would be in the wrong language), but every part
    lost is named, with the files to merge again."""
    transcript = with_own_work(saved)
    transcript_tracks = {"pl.author": {"language": "pl", "name": "Polish", "kind": "author",
                                       "cues": transcript["cues"]}}
    data = json.loads(saved.read_text("utf-8"))
    data[yt2html.TRANSCRIPT_KEY]["tracks"] = transcript_tracks
    saved.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(yt2html, "choose_caption_track", lambda info: None)
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,ovr", "--olang", "de"])
    out = capsys.readouterr().out
    assert code == 0
    assert "No transcript: no captions in 'de', the original language set with --olang." in out
    assert ("The saved transcript is dropped (lost: the transcript in 'en'; YouTube's "
            "translation into fr; own translation de (Title [TESTVIDEO01].de.own.srt); own "
            "corrections (Title [TESTVIDEO01].en.fixed.srt); subtitles pl.author).") in out
    data = json.loads(saved.read_text("utf-8"))
    assert data[yt2html.TRANSCRIPT_KEY] is None
    assert data[yt2html.NOT_SAVED_KEY] == {"transcript": {   # the old transcript's notes went too
        "reason": "no captions in 'de', the original language set with --olang",
        "date": dt.date.today().isoformat(), "again": False}}


def test_ovr_keeps_an_asked_youtube_translation_that_did_not_come(capsys, saved, youtube):
    """dual:fr,ovr when YouTube refuses fr now: the saved fr still fits the
    transcript downloaded again, so it stays (before: replaced by a note that
    it was refused); not counted as added."""
    before = json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]["translations"]["fr"]
    youtube["fail_translations"] = 1
    code = yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,dual:fr,ovr"])
    out = capsys.readouterr().out
    assert code == 0
    assert ("YouTube's translation into 'fr' did not come now (YouTube refused to send it "
            "(HTTP 429 Too Many Requests)); the saved one stays.") in out
    data = json.loads(saved.read_text("utf-8"))
    transcript = data[yt2html.TRANSCRIPT_KEY]
    assert transcript["translations"]["fr"] == before
    assert "transcript fr" not in data.get(yt2html.NOT_SAVED_KEY, {})
    assert "translation fr" not in data[yt2html.UPDATES_KEY][-1]["added"]
    assert "not available" not in row(next(saved.parent.glob("*.html")).read_text("utf-8"),
                                      "Transcript")


def test_ovr_takes_a_youtube_translation_that_came_now(capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    for cue in data[yt2html.TRANSCRIPT_KEY]["translations"]["fr"]["cues"]:
        cue["text"] = "an old line"
    saved.write_text(json.dumps(data), encoding="utf-8")
    assert yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,dual:fr,ovr"]) == 0
    assert "did not come now" not in capsys.readouterr().out
    french = json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]["translations"]["fr"]
    assert all(cue["text"] != "an old line" for cue in french["cues"])     # the new one


def test_ovr_drops_a_saved_youtube_translation_that_no_longer_fits(capsys, saved, youtube):
    data = json.loads(saved.read_text("utf-8"))
    french = data[yt2html.TRANSCRIPT_KEY]["translations"]["fr"]
    french["cues"] = french["cues"][:1]                      # made from other captions
    saved.write_text(json.dumps(data), encoding="utf-8")
    youtube["fail_translations"] = 1
    assert yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,dual:fr,ovr"]) == 0
    out = capsys.readouterr().out
    assert ("YouTube's translation into 'fr' did not come now, and the saved one is dropped: "
            "it no longer fits the transcript (1 line in the file,") in out
    data = json.loads(saved.read_text("utf-8"))
    assert "fr" not in (data[yt2html.TRANSCRIPT_KEY].get("translations") or {})
    assert data[yt2html.NOT_SAVED_KEY]["transcript fr"]["again"]


def test_ovr_drops_youtube_translations_not_asked_for(capsys, saved, youtube):
    """plain,ovr: the saved fr is not asked for, so it goes (the user's
    decision 25.11.2026: ovr overwrites; one's own work is what stays)."""
    assert yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "plain,ovr"]) == 0
    transcript = json.loads(saved.read_text("utf-8"))[yt2html.TRANSCRIPT_KEY]
    assert "fr" not in (transcript.get("translations") or {})
    assert "did not come now" not in capsys.readouterr().out


def test_transcript_parts():
    transcript = {"language": "en", "cues": [], "translations": {"pl": {}, "fr": {}},
                  "own": {"de": {"own": {"date": "2026-11-01"}}}, "fixed": {"cues": []}}
    assert yt2html.transcript_parts(transcript) == [
        "the transcript in 'en'", "YouTube's translations into fr, pl", "own translation de"]


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
    gaps = {}
    assert yt2html.fetch_transcript(fake_ydl(), info, gaps) is None
    assert gaps["transcript"]["reason"] == "no captions in 'de', the original language set with --olang"


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


# Subtitles of their own in another language (the author's, automatic ones of a dub) ------------

PL_TT = "https://www.youtube.com/api/timedtext?v=X&lang=pl"


def with_polish(author=True, auto=False):
    """English data with the author's Polish subtitles and/or a Polish dub's automatic ones."""
    from test_transcript import english_info
    info = english_info()
    if author:
        info["subtitles"] = {"pl": caption_formats("Polish", PL_TT),
                             "live_chat": [{"ext": "json", "url": "chat"}]}
    if auto:
        info["automatic_captions"]["pl-orig"] = caption_formats("Polish (Original)",
                                                                PL_TT + "&kind=asr")
    return info


@pytest.mark.parametrize("value, expected", [
    ("plain:pl.author", [("plain", "pl.author")]),
    ("timed:PT-br.Auto-Generated", [("timed", "pt-BR.auto-generated")]),
    ("plain:pl.translated,plain:pl.own", [("plain", "pl.translated"), ("plain", "pl.own")]),
    ("dual:pl.own", [("dual", "pl.own")]),
])
def test_kinds_in_the_spec(value, expected):
    assert yt2html.transcript_spec(value) == expected


@pytest.mark.parametrize("value, message", [
    ("plain:pl.dubbed", "'plain:pl.dubbed': the kind after the code is one of author, "
                        "auto-generated, translated, own, e.g. plain:pl.author"),
    ("dual:pl.author", "'dual:pl.author': dual puts the translation of the same lines beside the "
                       "original, so only dual:pl, dual:pl.translated or dual:pl.own"),
    ("timed:jp.author", "'timed:jp.author': 'jp' is not a language code"),
    ("plain:pl.author,plain:PL.author", "'plain:pl.author' is given more than once"),
])
def test_kind_errors(value, message):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match=re.escape(message)):
        yt2html.transcript_spec(value)


def test_subtitles_in():
    info = with_polish(auto=True)
    assert yt2html.subtitles_in(info, "pl", "author")[0] == "pl"
    assert yt2html.subtitles_in(info, "pl-PL", "author")[0] == "pl"      # the same language
    assert yt2html.subtitles_in(info, "pl", "auto-generated")[0] == "pl"
    assert yt2html.subtitles_in(info, "live_chat", "author") is None
    assert yt2html.subtitles_in(info, "de", "author") is None


@pytest.mark.parametrize("author, auto, spec, keys", [
    # plain and timed (25.11.2026): the translation, and when it does not come the
    # author's subtitles, else the automatic ones of a dub
    (True, True, [("plain", "pl")], ["pl|pl.author"]),
    (False, True, [("timed", "pl")], ["pl|pl.auto-generated"]),
    (False, False, [("plain", "pl")], ["pl"]),
    # a kind: that one; dual: the translation
    (True, False, [("plain", "pl.translated"), ("dual", "pl")], ["pl"]),
    (False, False, [("plain", "pl.author"), ("timed", "pl.auto-generated")],
     ["pl.author", "pl.auto-generated"]),
    (True, False, [("plain", "pl.own"), ("dual", "pl.own")], []),     # one's own: not asked
])
def test_source_keys(author, auto, spec, keys):
    assert yt2html.source_keys(spec, with_polish(author, auto), "en") == keys


def test_one_s_own_translation_is_not_asked_of_youtube():
    spec = [("plain", "pl"), ("dual", "pl")]   # without a kind one's own comes first
    assert yt2html.source_keys(spec, with_polish(author=False), "en", {"pl"}) == []
    assert yt2html.source_keys(spec, with_polish(), "en", {"pl"}) == []
    # by name: YouTube's translation and the author's subtitles are still asked for
    spec = [("plain", "pl.translated"), ("timed", "pl.author")]
    assert yt2html.source_keys(spec, with_polish(), "en", {"pl"}) == ["pl", "pl.author"]
    assert yt2html.split_keys(["pl|pl.author", "de", "pl", "fr.author"]) == (
        ["pl", "de"], ["fr.author"], {"pl": "pl.author"})
    assert yt2html.key_code("pt-BR|pt-BR.author") == "pt-BR"


def test_the_author_s_subtitles_when_youtube_refuses_its_translation(fake_ydl, capsys):
    ydl = fake_ydl(fail_translations=99)
    gaps = {}
    transcript = yt2html.fetch_transcript(ydl, with_polish(), gaps, ["pl|pl.author"])
    assert "pl" not in transcript.get("translations", {}) and gaps["transcript pl"]["again"]
    assert list(transcript["tracks"]) == ["pl.author"]
    out = capsys.readouterr().out
    assert "  Instead: subtitles by the author in 'pl'." in out
    assert yt2html.version_source(transcript, "plain", "pl") == "pl.author"
    # the translation comes: the subtitles are not fetched (no request)
    yt2html.caption_cache.clear()   # (each track once per run)
    ydl = fake_ydl()
    transcript = yt2html.fetch_transcript(ydl, with_polish(), {}, ["pl|pl.author"])
    assert "pl" in transcript["translations"] and "tracks" not in transcript
    assert not any(url.startswith(PL_TT) for url in ydl.urls)
    # automatic subtitles of a dub when there are no author's
    yt2html.caption_cache.clear()
    ydl = fake_ydl(fail_translations=99)
    dubbed = {**with_polish(author=False, auto=True), "language": "en"}   # (two -orig tracks)
    transcript = yt2html.fetch_transcript(ydl, dubbed, {}, ["pl|pl.auto-generated"])
    assert list(transcript["tracks"]) == ["pl.auto-generated"]
    assert "  Instead: automatic subtitles in 'pl'." in capsys.readouterr().out


def test_json_write_asks_for_the_translation_again_and_keeps_the_fallback(saved):
    data = json.loads(saved.read_text("utf-8"))
    transcript = data[yt2html.TRANSCRIPT_KEY]
    transcript.get("translations", {}).pop("pl", None)
    data[yt2html.NOT_SAVED_KEY] = {"transcript pl": {"reason": "refused", "again": True}}
    data["subtitles"] = {"pl": caption_formats("Polish", PL_TT)}
    args = yt2html.parse_args(["TESTVIDEO01", "--json", "write", "--transcript", "timed:pl"])
    # both missing: the translation, and the subtitles if it does not come
    assert yt2html.missing_for_update(data, args) == (False, ["pl|pl.author"])
    # the subtitles there: only the translation again (the page then takes it)
    transcript["tracks"] = {"pl.author": {"language": "pl", "name": "Polish", "kind": "author",
                                          "cues": []}}
    assert yt2html.missing_for_update(data, args) == (False, ["pl"])
    # YouTube has no translation into pl at all: the subtitles only
    data[yt2html.NOT_SAVED_KEY] = {"transcript pl": {"reason": "not offered", "again": False}}
    del transcript["tracks"]
    assert yt2html.missing_for_update(data, args) == (False, ["pl.author"])


def test_the_author_s_subtitles_are_downloaded(fake_ydl, capsys):
    ydl = fake_ydl()
    gaps = {}
    transcript = yt2html.fetch_transcript(ydl, with_polish(), gaps, ["pl.author", "de.author", "pl"])
    assert list(transcript["tracks"]) == ["pl.author"]
    track = transcript["tracks"]["pl.author"]
    assert (track["language"], track["name"], track["kind"]) == ("pl", "Polish", "author")
    assert any(url.startswith(PL_TT) for url in ydl.urls)
    assert gaps == {"transcript de.author": {
        "reason": "YouTube has no subtitles by the author in 'de'",
        "date": dt.date.today().isoformat(), "again": False}}
    assert "pl" in transcript["translations"]                  # and the translation as well
    out = capsys.readouterr().out
    assert "Downloading subtitles (Polish, by the author)..." in out


def test_subtitles_of_their_own_on_the_page(info):
    transcript = info[yt2html.TRANSCRIPT_KEY]
    transcript["tracks"] = {"pl.author": {"language": "pl", "name": "Polish", "kind": "author",
                                          "cues": [{"start": 0.5, "text": "Cześć"}]}}
    html = render(info, [("plain", None), ("timed", "pl.author"), ("timed", "pl")])
    found = sections(html)
    assert found == [("transcript original", ""), ("transcript with-timestamps", ' data-track="pl"'),
                     ("transcript with-timestamps", ' data-lang="pl"')]
    assert "Polish, by the author" in html and "Cześć" in html
    assert row(html, "Transcript").endswith(
        "3 versions: plain, timed in Polish (by the author), timed in Polish (auto-translated)")
    # the same source twice: once (without a kind YouTube's translation comes
    # before the author's subtitles)
    versions, notes = yt2html.resolve_versions([("timed", "pl"), ("timed", "pl.translated")],
                                               transcript)
    assert versions == [("timed", "pl")]
    assert notes == ["timed:pl.translated: the same as timed:pl; shown once"]
    translation = transcript["translations"].pop("pl")
    assert yt2html.version_source(transcript, "timed", "pl") == "pl.author"
    transcript["translations"]["pl"] = translation
    # what the data have not: named with its reason
    info[yt2html.NOT_SAVED_KEY] = {"transcript pl.auto-generated": {"reason": "YouTube has none",
                                                                    "again": False}}
    assert row(render(info, [("plain", "pl.auto-generated")]), "Transcript").endswith(
        "not available: plain:pl.auto-generated (YouTube has none)")
    # their SRT file under their kind
    assert yt2html.srt_tracks(info, [("timed", "pl.author")])[0][:2] == ("pl", "author")


def test_json_write_adds_subtitles_of_their_own(capsys, saved, youtube):
    # the saved data list the author's Polish subtitles: timed:pl.author asks for them
    data = json.loads(saved.read_text("utf-8"))
    data["subtitles"] = {"pl": caption_formats("Polish", PL_TT)}
    saved.write_text(json.dumps(data), encoding="utf-8")
    args = yt2html.parse_args(["TESTVIDEO01", "--json", "write", "--transcript",
                               "timed:pl.author"])
    assert yt2html.missing_for_update(data, args) == (False, ["pl.author"])
    # YouTube's data now lack them: noted, not asked for again
    assert yt2html.main(["TESTVIDEO01", "-o", str(saved.parent), "--json", "write",
                         "--transcript", "timed:pl.author"]) == 0
    after = json.loads(saved.read_text("utf-8"))[yt2html.NOT_SAVED_KEY]
    assert after["transcript pl.author"]["reason"] == "YouTube has no subtitles by the author in 'pl'"
    capsys.readouterr()
    assert yt2html.missing_for_update(json.loads(saved.read_text("utf-8")), args) == (False, [])
    assert ("No subtitles pl.author in the saved data (YouTube has no subtitles by the author "
            "in 'pl'); not asked for again.") in capsys.readouterr().out


def test_a_kind_names_whose_translation_it_is():
    transcript = {"language": "en", "cues": [], "translations": {
        "pl": {"language": "pl", "name": "Polish", "cues": []},
        "de": {"language": "de", "name": "German", "cues": [], "own": True}},   # saved before
        "own": {"fr": {"language": "fr", "name": "French", "cues": [], "own": {}}}}
    source = yt2html.version_source
    assert source(transcript, "plain", "pl.translated") == "pl"
    assert source(transcript, "plain", "pl.own") == "pl.own"            # YouTube's, not one's own
    assert not yt2html.available(transcript, "pl.own")
    assert source(transcript, "dual", "de.own") == "de.own"
    assert yt2html.available(transcript, "de.own") and yt2html.available(transcript, "fr.own")
    assert source(transcript, "timed", "de.translated") == "de.translated"
    assert not yt2html.available(transcript, "de.translated")
    assert source(transcript, "dual", "de") == "de.own"                 # no kind: one's own
    assert source(transcript, "dual", "pl") == "pl"                     # … else YouTube's
    assert source(transcript, "dual", "fr") == "fr.own"
    assert source(transcript, "dual", "it") == "it"                     # none: as written
    transcript["tracks"] = {"it.author": {"cues": []}}
    assert source(transcript, "dual", "it") == "it"                     # dual: no subtitles
    assert source(transcript, "plain", "it") == "it.author"


# One's own translation beside YouTube's (25.11.2026) ----------------------------------------

def with_own(info, language="pl", prefix="own"):
    transcript = info[yt2html.TRANSCRIPT_KEY]
    yt2html.set_own(transcript, language, {
        "language": language, "name": {"pl": "Polish", "de": "German"}[language],
        "cues": [{"start": c["start"], "text": f"{prefix}: {c['text']}"} for c in transcript["cues"]],
        "own": {"file": f"x.{language}.own.srt", "date": "2026-11-25"}})
    return transcript


def test_own_and_youtube_s_sections(info):
    with_own(info)
    html = render(info, [("dual", "pl"), ("timed", "pl.translated"), ("plain", "pl.own")])
    assert sections(html) == [("transcript bilingual", ' data-lang="pl" data-own'),
                              ("transcript with-timestamps", ' data-lang="pl"'),
                              ("transcript", ' data-lang="pl" data-own'),
                              ("transcript with-timestamps original", " hidden")]
    assert "Polish, own translation" in html and "Polish, auto-translated" in html
    # the SRT files: YouTube's only; the Command row: without one's own
    assert [t[:2] for t in yt2html.srt_tracks(info, [("dual", "pl"), ("timed", "pl.translated")])] \
        == [("en", "auto-generated"), ("pl", "translated")]
    assert re.search(r'<code class="command">(.*?)</code>', html).group(1).endswith(
        "--transcript timed:pl.translated")


def test_two_translations_of_one_language_in_the_browser(info, chromium, tmp_path):
    """pl₁ (one's own, first on the page), pl₂ (YouTube's), off; another
    language keeps the variant shown last."""
    with_own(info)
    with_own(info, "de", "de")
    page_path = tmp_path / "Title [TESTVIDEO01].html"
    page_path.write_text(render(info, [("dual", "pl"), ("dual", "pl.translated"),
                                       ("timed", "pl"), ("dual", "de")]), encoding="utf-8")
    shutil.copy(SHORT, tmp_path / "Title [TESTVIDEO01].webm")
    page = chromium.new_page()
    page.goto(page_path.as_uri())
    page.click(".fact.play")
    page.wait_for_function("document.querySelector('video').readyState >= 1")
    page.click("[data-do=subtitles]")
    buttons = page.locator(".langs .lang")
    pl, de = buttons.nth(0), buttons.nth(1)
    assert buttons.count() == 2 and pl.inner_text() == "pl1" and de.inner_text() == "de"
    assert pl.locator("sub").count() == 1 and de.locator("sub").count() == 0

    def shown():
        return page.inner_text(".now-playing .translation")
    pl.click()
    assert shown() == "own: Hello and welcome" and pl.get_attribute("aria-pressed") == "true"
    pl.click()
    assert shown() == "pl: hello" and pl.inner_text() == "pl2"
    pl.click()   # after the last: off, the first again next time
    assert shown() == "" and pl.get_attribute("aria-pressed") == "false" and pl.inner_text() == "pl1"
    pl.click()
    pl.click()
    de.click()   # another language: pl keeps pl₂
    assert shown() == "de: Hello and welcome" and pl.inner_text() == "pl2"
    pl.click()
    assert shown() == "pl: hello"
    pl.click()
    assert shown() == ""
    page.close()


def test_follow_goes_through_subtitles_of_their_own_in_the_browser(info, chromium, tmp_path):
    transcript = info[yt2html.TRANSCRIPT_KEY]
    transcript["tracks"] = {"pl.author": {"language": "pl", "name": "Polish", "kind": "author",
                                          "cues": [{"start": 0.0, "text": "Witam"},
                                                   {"start": 1.5, "text": "Dalej"}]}}
    page_path = tmp_path / "Title [TESTVIDEO01].html"
    page_path.write_text(render(info, [("timed", None), ("timed", "pl.author")]), encoding="utf-8")
    shutil.copy(SHORT, tmp_path / "Title [TESTVIDEO01].webm")
    page = chromium.new_page()
    page.goto(page_path.as_uri())
    page.click(".fact.play")
    page.wait_for_function("document.querySelector('video').readyState >= 1")
    page.click("[data-do=subtitles]")
    assert page.locator(".langs .lang").count() == 0              # other lines: no button
    page.click("[data-do=follow]")
    page.click("[data-do=follow]")
    assert page.inner_text("[data-do=follow]") == "Follow 2/2"
    # the subtitles' own times: at 1.6 s their second line
    page.evaluate("document.querySelector('video').currentTime = 1.6")
    page.wait_for_function("(document.querySelector('section[data-track] .now') || {})"
                           ".textContent?.includes('Dalej')", timeout=3000)
    page.close()
