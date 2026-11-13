"""Transcript: choosing the track, captions, versions, bilingual and translations."""

import argparse
import re
import datetime as dt

import pytest

import yt2html
from conftest import ORIGINAL_CAPTIONS, caption_formats


def asr(*languages):
    return [{"languageCode": lang, "kind": "asr"} for lang in languages]


class TestDefaultCaptionTrack:
    def test_auto_dubbed_video_uses_the_original_audio(self):
        # Original English plus dubs; defaultAudioTrackIndex points to the original.
        audio = ["fr-FR.10", "ar.10", "en-US.4"]
        player = {"captions": {"playerCaptionsTracklistRenderer": {
            "captionTracks": asr("ar", "bn", "nl", "en"),
            "audioTracks": [{"audioTrackId": a, "defaultCaptionTrackIndex": 3} for a in audio],
            "defaultAudioTrackIndex": 2}}}
        assert yt2html.default_caption_track(player) == {"language": "en", "kind": "asr"}

    def test_original_audio_marked_in_streams(self):
        # Polish original, English dub played by default.
        player = {"captions": {"playerCaptionsTracklistRenderer": {
            "captionTracks": asr("pl"),
            "audioTracks": [{"audioTrackId": "en-US.10"}, {"audioTrackId": "pl.4"}],
            "defaultAudioTrackIndex": 1}},
            "streamingData": {"adaptiveFormats": [
                {"audioTrack": {"id": "en-US.10", "displayName": "English (US)", "audioIsDefault": True}},
                {"audioTrack": {"id": "pl.4", "displayName": "Polish original"}}]}}
        assert yt2html.default_caption_track(player) == {"language": "pl", "kind": None}

    def test_single_track(self):
        player = {"captions": {"playerCaptionsTracklistRenderer": {
            "captionTracks": asr("pl"), "audioTracks": [{"captionTrackIndices": [0]}],
            "defaultAudioTrackIndex": 0}}}
        assert yt2html.default_caption_track(player) == {"language": "pl", "kind": "asr"}

    def test_no_captions(self):
        assert yt2html.default_caption_track({}) is None


class TestChooseTrack:
    def test_dubbed_video_picks_the_original_not_the_first_orig_track(self):
        auto = {}
        for lang in ("ar", "en", "pl"):
            auto[f"{lang}-orig"] = caption_formats(f"{lang} (Original)")
            auto[lang] = caption_formats(lang)
        info = {"automatic_captions": auto, yt2html.CAPTION_KEY: {"language": "en", "kind": "asr"}}
        assert yt2html.choose_caption_track(info)[:2] == ("auto", "en")

    def test_author_subtitles_preferred(self):
        info = {"subtitles": {"en-US": caption_formats("English"), "live_chat": [{"ext": "json"}]},
                "automatic_captions": {"en-orig": caption_formats("English (Original)")}}
        assert yt2html.choose_caption_track(info)[:2] == ("manual", "en-US")

    def test_subtitles_in_another_language_are_not_the_transcript(self):
        info = {"subtitles": {"de": caption_formats("German")},
                "automatic_captions": {"en-orig": caption_formats("English (Original)")}}
        assert yt2html.choose_caption_track(info)[:2] == ("auto", "en")

    def test_original_language_from_yt_dlp_formats(self):
        auto = {f"{l}-orig": caption_formats(l) for l in ("ar", "en")}
        info = {"automatic_captions": auto,
                "formats": [{"language": "ar", "language_preference": -1},
                            {"language": "en-US", "language_preference": 10}]}
        assert yt2html.choose_caption_track(info)[:2] == ("auto", "en")

    def test_nothing_available(self):
        assert yt2html.choose_caption_track({}) is None


def test_json3_captions():
    cues = yt2html.parse_json3_captions(ORIGINAL_CAPTIONS)
    assert cues == [{"start": 0.0, "text": "Hello and welcome"},
                    {"start": 6.0, "text": "first line second line"},
                    {"start": 120.0, "text": "[Music]"}]


def english_info():
    return {"automatic_captions": {"en-orig": caption_formats("English (Original)")},
            yt2html.TRANSLATIONS_KEY: {"pl": "Polish", "de": "German"}}


class TestFetching:
    def test_transcript(self, fake_ydl):
        transcript = yt2html.fetch_transcript(fake_ydl(), english_info())
        assert transcript["name"] == "English" and transcript["kind"] == "auto"
        assert len(transcript["cues"]) == 3

    def test_two_translations_keep_timing(self, fake_ydl):
        ydl = fake_ydl()
        transcript = yt2html.fetch_transcript(ydl, english_info(), ["pl", "de"])
        assert set(transcript["translations"]) == {"pl", "de"}
        assert any("tlang=pl" in url for url in ydl.urls)
        texts = yt2html.align_translation(transcript["cues"], transcript["translations"]["pl"]["cues"])
        assert texts == ["pl: hello", "pl: lines", "pl: music"]

    def test_retry_after_429(self, fake_ydl):
        transcript = yt2html.fetch_transcript(fake_ydl(fail_translations=1), english_info(), ["pl"])
        assert transcript["translations"]["pl"]["cues"]

    def test_gives_up_after_repeated_429(self, fake_ydl):
        with pytest.raises(yt2html.TranscriptError, match="cookies-from-browser"):
            yt2html.fetch_transcript(fake_ydl(fail_translations=9), english_info(), ["pl"])

    @pytest.mark.parametrize("target, kwargs, message", [
        ("fr", {}, "(available: de, pl)"),
        ("en", {}, "language of the transcript itself"),
        ("pl", {"empty_translation": True}, "no translation into 'pl'"),
    ])
    def test_translation_not_there(self, fake_ydl, target, kwargs, message, capsys):
        # what YouTube does not have: the transcript without it, with the reason
        transcript = yt2html.fetch_transcript(fake_ydl(**kwargs), english_info(), [target, "de"])
        assert target not in transcript["translations"]
        assert ("de" in transcript["translations"]) is not bool(kwargs)   # the others still come
        assert message in transcript["missing"][target]
        assert f"No translation into '{target}': " in capsys.readouterr().out

    def test_no_transcript(self, fake_ydl):
        info = {}
        assert yt2html.fetch_transcript(fake_ydl(), info, ["pl"]) is None   # a note, no error
        assert info[yt2html.TRANSCRIPT_MISSING_KEY] == "the video has no captions"

    def test_failed_download_stops(self, fake_ydl):
        class Broken(fake_ydl):
            def urlopen(self, url):
                raise OSError("offline")
        # may pass: nothing saved, so the next run tries again
        with pytest.raises(yt2html.TranscriptError, match=r"could not download the transcript \(offline\); nothing was saved"):
            yt2html.fetch_transcript(Broken(), english_info())


class TestSpec:
    @pytest.mark.parametrize("value, expected", [
        ("plain", [("plain", None)]),
        ("", [("plain", None)]),            # the option alone
        ("timestamps,plain", [("timestamps", None), ("plain", None)]),
        (" plain , bilingual:pl,bilingual:pt-BR ",
         [("plain", None), ("bilingual", "pl"), ("bilingual", "pt-BR")]),
        # any case of letters; codes in the usual notation, as YouTube writes them
        ("Plain,TIMESTAMPS,Bilingual:PL,bilingual:pt-br,bilingual:ZH-HANS,bilingual:es-419",
         [("plain", None), ("timestamps", None), ("bilingual", "pl"), ("bilingual", "pt-BR"),
          ("bilingual", "zh-Hans"), ("bilingual", "es-419")]),
        (" ", [("plain", None)]),
    ])
    def test_valid(self, value, expected):
        assert yt2html.transcript_spec(value) == expected

    def test_standard_code(self):
        # for --transcript now, for the codes of --video later
        assert [yt2html.standard_code(c) for c in ("PL", "PT-br", "ZH-HANS", "es-419", "fil")] == [
            "pl", "pt-BR", "zh-Hans", "es-419", "fil"]

    @pytest.mark.parametrize("value", [
        "plain,", "plain,plain", "none,plain", "bilingual", "bilingual:",
        "bilingual:polish!", "plain:pl", "block", "bilingual:pl,bilingual:pl", "bilingual:pl,bilingual:PL",
        "bilingual:jp",
    ])
    def test_invalid(self, value):
        with pytest.raises(argparse.ArgumentTypeError):
            yt2html.transcript_spec(value)

    @pytest.mark.parametrize("value, message", [
        # anything unknown, "none" too (without the option there is no transcript)
        ("none", "'none' is not a parameter (use plain, timestamps or bilingual:CODE)"),
        ("https://www.youtube.com/watch?v=x", "is not a parameter (use plain, timestamps or "
                                              "bilingual:CODE); the video's address goes before --transcript"),
        ("bilingual:jp", "'bilingual:jp': 'jp' is not a language code (e.g. pl, en, pt-BR)"),
    ])
    def test_messages(self, value, message):
        with pytest.raises(argparse.ArgumentTypeError, match=re.escape(message)):
            yt2html.transcript_spec(value)


def sections(info, spec):
    html = yt2html.render_page(info, None, {}, yt2html.dt.date(2026, 11, 5), transcript_spec=spec)
    import re
    return re.findall(r'<section class="(transcript[^"]*)"[ >]', html), html


class TestRendering:
    def test_default_plain_split_by_chapters(self, info):
        found, html = sections(info, None)
        assert found == ["transcript"]
        import re
        visible = re.sub(r"<span data-c=\"[0-9.]+\">|</span>", "", html)  # the text as shown
        assert "<h3>Intro</h3><p>Hello and welcome first line second line</p>" in visible
        assert "<h3>Second part</h3><p>[Music]</p>" in visible
        # one invisible span per cue with its start, for the player
        assert '<p><span data-c="0">Hello and welcome</span> <span data-c=' in html

    def test_versions_in_given_order(self, info):
        found, html = sections(info, [("timestamps", None), ("plain", None), ("bilingual", "pl")])
        assert found == ["transcript with-timestamps", "transcript", "transcript bilingual"]
        assert "English, auto-generated, with timestamps" in html
        assert "Polish, auto-translated" in html

    def test_none_hides_a_saved_transcript(self, info):
        assert sections(info, [])[0] == []


class TestSpeakerChanges:
    """YouTube's ">>" (new speaker) becomes our grey mark."""

    MARK = yt2html.SPEAKER_MARK

    def test_marks_are_replaced(self):
        assert yt2html.speaker_html(">> Hello & bye") == (f"{self.MARK} Hello &amp; bye", True)
        assert yt2html.speaker_html("Right. >> Yes.") == (f"Right. {self.MARK} Yes.", True)
        assert yt2html.speaker_html(">>> New topic")[0] == f"{self.MARK} New topic"
        assert yt2html.speaker_html("No change") == ("No change", False)
        assert yt2html.speaker_html(">> Start", at_start=False) == ("Start", True)

    def render(self, info, spec):
        info[yt2html.TRANSCRIPT_KEY]["cues"][0]["text"] = ">> Hello and welcome"
        info[yt2html.TRANSCRIPT_KEY]["cues"][1]["text"] = "first line >> second voice"
        return yt2html.render_page(info, None, {}, dt.date(2026, 11, 7), transcript_spec=spec)

    def test_timestamps_and_bilingual(self, info):
        html = self.render(info, [("timestamps", None), ("bilingual", "pl")])
        assert "&gt;&gt;" not in html
        assert f'<span class="text">{self.MARK} Hello and welcome</span>' in html
        assert f'<span class="text">first line {self.MARK} second voice</span>' in html
        # the translation of a cue that starts a new speaker gets the mark too
        assert html.count(f'<span class="text">{self.MARK} ') >= 3

    def test_plain_uses_a_grey_bar_but_not_at_the_start(self, info):
        html = self.render(info, [("plain", None)])
        assert "&gt;&gt;" not in html
        assert '<p><span data-c="0">Hello and welcome</span>' in html      # start of a part
        assert f"first line {yt2html.PLAIN_SPEAKER_MARK} second voice" in html

    def test_lines_under_the_video_leave_the_marks_out(self):
        assert "all('.speaker', copy).forEach(function (mark) { mark.remove(); });" in yt2html.PLAYER_JS


def test_bilingual_section_names_its_language(info):
    _, html = sections(info, [("bilingual", "pl")])
    assert '<section class="transcript bilingual" data-lang="pl"><h2>Transcript</h2>' in html
