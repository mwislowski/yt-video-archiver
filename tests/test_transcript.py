"""Transcript: choosing the track, captions, versions, bilingual and translations."""

import argparse
import datetime as dt
import json
import re
from pathlib import Path

import pytest

import yt2html

from conftest import (ORIGINAL_CAPTIONS, FakeHTTPError, FakeYDL, caption_formats,
                      translated_captions, watch_page)

# imRB4Y2ShoM (23.11.2026), the page saved in the user's browser: Polish
# original, an English auto-dub; only the caption list and the audio tracks
# (no addresses), and three of the translation languages
DUBBED_PLAYER = json.loads((Path(__file__).parent / "data" / "dubbed_player_response.json")
                           .read_text(encoding="utf-8"))


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
        # the player starting with the dub: the stream's mark still names the original
        player["captions"]["playerCaptionsTracklistRenderer"]["defaultAudioTrackIndex"] = 0
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

    def test_the_original_wins_over_a_dubs_default(self):
        # 8etkFuBLGZ0 (20.11.2026): Polish original, an English auto-dub; for
        # yt-dlp's English request the page named the dub's captions default
        auto = {f"{lang}-orig": caption_formats(f"{lang} (Original)") for lang in ("pl", "en-US")}
        auto.update({lang: caption_formats(lang) for lang in ("pl", "en", "de")})
        formats = [{"format_id": "140-0", "language": "pl", "language_preference": 10},
                   {"format_id": "140-1", "language": "en-US", "language_preference": -1}]
        info = {"automatic_captions": auto, "formats": formats,
                yt2html.CAPTION_KEY: {"language": "en-US", "kind": "asr"}}
        assert yt2html.transcript_language(info) == "pl"
        assert yt2html.choose_caption_track(info)[:2] == ("auto", "pl")
        # the author's Polish subtitles first: the dub's kind (asr) does not decide
        info["subtitles"] = {"pl": caption_formats("Polish")}
        assert yt2html.choose_caption_track(info)[:2] == ("manual", "pl")
        # marked only by YouTube's kind in the address (no language preference)
        del info["subtitles"]
        address = "https://x/videoplayback?xtags=acont%3D"
        info["formats"] = [{"language": "pl", "url": address + "original%3Alang%3Dpl&n=1"},
                           {"language": "en-US", "url": address + "dubbed-auto%3Alang%3Den-US"}]
        assert yt2html.choose_caption_track(info)[:2] == ("auto", "pl")

    def test_the_pages_default_stands_when_it_agrees_or_nothing_is_marked(self):
        auto = {f"{lang}-orig": caption_formats(lang) for lang in ("en", "pl")}
        default = {yt2html.CAPTION_KEY: {"language": "en", "kind": "asr"}}
        # the same language in another variant (en-US original, "en" captions)
        info = {"automatic_captions": auto, **default,
                "formats": [{"language": "en-US", "language_preference": 10}]}
        assert yt2html.transcript_language(info) == "en"
        # one audio language, nothing marked (the video's "language" is a guess)
        info = {"automatic_captions": auto, **default, "language": "pl",
                "formats": [{"language": "pl", "language_preference": -1}]}
        assert yt2html.transcript_language(info) == "en"
        # no default: a mark without a language is passed over; else the video's language
        info = {"automatic_captions": auto, "language": "pl",
                "formats": [{"language_preference": 10},
                            {"language": "en-US", "language_preference": 10}]}
        assert yt2html.transcript_language(info) == "en-US"
        info["formats"] = []
        assert yt2html.transcript_language(info) == "pl"


def test_json3_to_srt():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": " Hello "}, {"utf8": "and welcome"}]},
        {"tStartMs": 3000, "aAppend": 1, "segs": [{"utf8": "\n"}]},       # only a line break
        {"tStartMs": 4000},                                                # no text at all
        {"tStartMs": 3_723_456, "dDurationMs": 1544, "segs": [{"utf8": "first\nsecond"}]}]}
    assert yt2html.json3_to_srt(data) == (
        b"1\n00:00:00,000 --> 00:00:04,000\nHello and welcome\n\n"
        b"2\n01:02:03,456 --> 01:02:05,000\nfirst\nsecond\n")
    assert yt2html.json3_to_srt({}) == b""


def test_caption_key():
    tt = "https://www.youtube.com/api/timedtext?v=V&expire=1&sig=a&lang=en&kind=asr&fmt=srt"
    assert yt2html.caption_key(tt) == ("V", "en", "asr", "", "")
    assert yt2html.caption_key(tt + "&tlang=pl&name=x") == ("V", "en", "asr", "x", "pl")
    assert yt2html.caption_key("https://example.com/c?fmt=vtt") == "https://example.com/c?fmt=json3"


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

    def test_refused_translation_is_left_out_with_a_note(self, fake_ydl, capsys):
        # asked once, not retried: the page is made without it (it counts as finished)
        ydl = fake_ydl(fail_translations=1)
        transcript = yt2html.fetch_transcript(ydl, english_info(), ["pl", "de"])
        assert set(transcript["translations"]) == {"de"}
        assert transcript["missing"] == {
            "pl": {"reason": "YouTube refused to send it (HTTP 429 Too Many Requests)",
                   "refused": True, "date": dt.date.today().isoformat()}}
        assert sum("tlang=pl" in url for url in ydl.urls) == 1
        out = capsys.readouterr().out
        assert ("No translation into 'pl': YouTube refused to send it (HTTP 429 Too Many "
                "Requests).\n[yt2html]   The page is made without it and counts as finished; "
                "to add the translation later: --json write (keeps the comments) or "
                "--page ovr. YouTube refuses auto-translated captions to downloads at times (HTTP 429); try again "
                "later.") in out

    def test_other_failure_of_a_translation(self, fake_ydl):
        class Broken(fake_ydl):
            def urlopen(self, url):
                if "tlang" in url:
                    raise OSError("ERROR: [youtube] X: offline\nmore")
                return super().urlopen(url)
        transcript = yt2html.fetch_transcript(Broken(), english_info(), ["pl"])
        assert transcript["missing"]["pl"]["reason"] == "the download failed (offline)"
        assert transcript["missing"]["pl"]["refused"] is True

    def test_waits_for_the_first_translation_only(self, fake_ydl, monkeypatch, capsys):
        monkeypatch.setattr(yt2html.time, "monotonic", lambda: 100.0)
        yt2html.fetch_transcript(fake_ydl(), english_info(), ["pl", "de"], not_before=180.0)
        out = capsys.readouterr().out
        assert out.count("Waiting 80 s (YouTube refuses translations requested too soon)") == 2
        yt2html.fetch_transcript(fake_ydl(), english_info(), ["pl", "de"], not_before=180.0)
        assert "Waiting" not in capsys.readouterr().out      # downloaded already: no wait

    def test_subtitle_files_share_the_download(self, fake_ydl, monkeypatch):
        # the transcript's translation, then sub:pl from another fetch (other signature):
        # one request; a refusal is not asked again either
        ydl = fake_ydl(fail_translations=1)
        transcript = yt2html.fetch_transcript(ydl, english_info(), ["pl", "de"])
        asked = []

        def fetch_subtitles(url, args):
            asked.append(url)
            return json.dumps(translated_captions("pl")).encode()

        monkeypatch.setattr(yt2html, "fetch_subtitles", fetch_subtitles)
        other = "https://www.youtube.com/api/timedtext?v=X&expire=2&lang=en"   # any format
        srt = yt2html.download_srt(other + "&tlang=de", None)
        assert srt.decode().startswith("1\n00:00:00,000 --> 00:00:00,000\nde: hello\n\n2\n")
        with pytest.raises(FakeHTTPError, match="429"):
            yt2html.download_srt(other + "&tlang=pl", None)
        assert asked == [] and "pl" in transcript["missing"]
        yt2html.caption_cache.clear()        # the next run asks again
        assert b"pl: hello" in yt2html.download_srt(other + "&tlang=pl", None)
        assert len(asked) == 1 and "fmt=json3" in asked[0]

    def test_wait_is_random_within_the_range(self, monkeypatch):
        monkeypatch.setattr(yt2html.random, "uniform", lambda a, b: (a, b))
        assert yt2html.translation_wait() == (70, 100)

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
        assert message in transcript["missing"][target]["reason"]
        assert transcript["missing"][target]["refused"] is False
        out = capsys.readouterr().out
        assert f"No translation into '{target}': " in out and "--page ovr" not in out

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


# --------------------------------------------------------------------------
# The original language when yt-dlp has no audio tracks (imRB4Y2ShoM, 23.11.2026)
# --------------------------------------------------------------------------

def without_audio_tracks(**extra):
    """Data as from a run where YouTube asked to confirm one is not a bot:
    only the combined format 18 and storyboards, the watch page's default
    captions those of the English dub, speech recognised in both languages."""
    auto = {f"{lang}-orig": caption_formats(f"{lang} (Original)") for lang in ("en", "pl")}
    auto.update({lang: caption_formats(lang) for lang in ("en", "pl", "de")})
    formats = [{"format_id": f"sb{i}", "ext": "mhtml", "vcodec": "none", "acodec": "none"}
               for i in range(4)]
    formats.append({"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2", "height": 360})
    return {"automatic_captions": auto, "formats": formats, "language": "en",
            yt2html.CAPTION_KEY: {"language": "en", "kind": "asr"}, **extra}


class TestOriginalFromTheWatchPage:
    def test_read_from_the_audio_tracks(self):
        assert yt2html.page_original_language(DUBBED_PLAYER) == "pl"
        # the default captions there follow the original (the user's own browser)
        assert yt2html.default_caption_track(DUBBED_PLAYER) == {"language": "pl", "kind": "asr"}

    def test_by_yt_dlps_rule(self):
        def player(*tracks):
            return {"streamingData": {"adaptiveFormats": [{"itag": 1}] + [
                {"itag": 140, "audioTrack": t} for t in tracks]}}
        assert yt2html.page_original_language({}) is None
        assert yt2html.page_original_language(player()) is None
        # the dub YouTube plays by default is not the original
        assert yt2html.page_original_language(player(
            {"displayName": "English (US)", "id": "en-US.10", "audioIsDefault": True})) is None
        # an audio description is not the original, even when its name says so
        assert yt2html.page_original_language(player(
            {"displayName": "English original descriptive", "id": "en.3"},
            {"displayName": "English ORIGINAL", "id": "en-GB.4"})) == "en-GB"
        assert yt2html.page_original_language(player({"displayName": "Polish original"})) is None

    def test_saved_with_the_page_data(self):
        info = {"id": "imRB4Y2ShoM"}
        yt2html.add_watch_page_data(FakeYDL(webpage=watch_page({}, DUBBED_PLAYER)), info)
        assert info[yt2html.ORIGINAL_AUDIO_KEY] == "pl"
        # the page without the player data, or not read at all: unknown
        yt2html.add_watch_page_data(FakeYDL(webpage=watch_page({})), info)
        assert info[yt2html.ORIGINAL_AUDIO_KEY] is None
        info[yt2html.ORIGINAL_AUDIO_KEY] = "pl"
        yt2html.add_watch_page_data(FakeYDL(webpage="<html></html>"), info)
        assert info[yt2html.ORIGINAL_AUDIO_KEY] is None

    def test_the_page_tells_the_original_when_the_formats_do_not(self):
        info = without_audio_tracks()
        assert yt2html.transcript_language(info) == "en"      # the dub's captions, as before
        info[yt2html.ORIGINAL_AUDIO_KEY] = "pl"
        assert yt2html.marked_original_language(info) == "pl"
        assert yt2html.transcript_language(info) == "pl"
        assert yt2html.choose_caption_track(info)[:2] == ("auto", "pl")
        assert not yt2html.original_uncertain(info)
        # the formats' mark comes first
        info["formats"] = [{"language": "de", "language_preference": 10}]
        assert yt2html.transcript_language(info) == "de"

    def test_the_formats_table_takes_the_same_language(self):
        # an audio format without a language takes the original's (the guess of
        # the Transcript row); format 18 combines image and sound, no audio row
        info = without_audio_tracks(**{yt2html.ORIGINAL_AUDIO_KEY: "pl"})
        info["formats"].append({"format_id": "140", "ext": "m4a", "vcodec": "none",
                                "acodec": "mp4a.40.2", "filesize": 1000})
        choice = yt2html.video_choice(info)
        assert [choice.track(f)[0] for f in choice.audio] == ["pl"]

    def test_the_page_metadata_takes_the_same_language(self, info):
        info.update(without_audio_tracks(**{yt2html.ORIGINAL_AUDIO_KEY: "pl"}))
        html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5))
        assert '<meta name="yt2html:language" content="pl">' in html

    def test_fresh_data_get_the_pages_choices(self):
        page = {yt2html.CAPTION_KEY: {"language": "en"}, yt2html.ORIGINAL_AUDIO_KEY: "pl",
                yt2html.TRANSLATIONS_KEY: {"de": "German"}, "title": "not taken"}
        fresh = {"id": "x", "title": "fresh", yt2html.ORIGINAL_AUDIO_KEY: "stale"}
        assert yt2html.with_page_choices(fresh, page) == {
            "id": "x", "title": "fresh", yt2html.CAPTION_KEY: {"language": "en"},
            yt2html.ORIGINAL_AUDIO_KEY: "pl", yt2html.TRANSLATIONS_KEY: {"de": "German"}}
        assert yt2html.with_page_choices(fresh, None)[yt2html.ORIGINAL_AUDIO_KEY] is None


class TestOriginalUncertain:
    def test_when(self):
        info = without_audio_tracks()
        assert yt2html.original_uncertain(info)
        # one language recognised: no dubs to mistake it for
        del info["automatic_captions"]["pl-orig"]
        assert not yt2html.original_uncertain(info)
        info["automatic_captions"]["pl-orig"] = []          # a track without formats
        assert not yt2html.original_uncertain(info)
        # marked: in the formats, or on the page
        assert not yt2html.original_uncertain(without_audio_tracks(
            formats=[{"language": "pl", "language_preference": 10}]))
        assert not yt2html.original_uncertain(without_audio_tracks(
            **{yt2html.ORIGINAL_AUDIO_KEY: "pl"}))

    def test_the_transcript_is_not_called_original(self):
        info = without_audio_tracks()
        transcript = yt2html.fetch_transcript(FakeYDL(), info)
        assert transcript["language"] == "en" and transcript["original"] is False
        info[yt2html.ORIGINAL_AUDIO_KEY] = "pl"
        transcript = yt2html.fetch_transcript(FakeYDL(), info)
        assert transcript["language"] == "pl" and transcript["original"] is True

    def test_on_the_page(self, info):
        info.update(without_audio_tracks())
        info[yt2html.TRANSCRIPT_KEY]["original"] = True      # saved before the check
        spec = [("plain", None), ("timestamps", None), ("bilingual", "fr")]
        info[yt2html.TRANSCRIPT_KEY]["translations"] = {
            "fr": {"language": "fr", "name": "French", "cues": info[yt2html.TRANSCRIPT_KEY]["cues"]}}
        html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5), transcript_spec=spec)
        note = ('<div class="note uncertain">Original language uncertain: YouTube did not send '
                'the audio tracks that tell it from the dubs, so this transcript is in English, '
                'the captions YouTube opens with. Make the page again later; if YouTube asked to '
                'confirm you are not a bot, with your browser&#x27;s cookies.</div>')
        assert html.count(note) == 1
        assert re.search(r'<section class="transcript"><h2>Transcript[^\n]*?</h2>'
                         + re.escape(note), html)
        assert "--cookies" not in html.split("<code")[0]          # no option names on the page
        assert re.search(r"<dt>Transcript</dt><dd>English \(auto-generated; original language "
                         r"unknown\); 3 versions", html)
        # known: no note, the row as before
        info[yt2html.ORIGINAL_AUDIO_KEY] = "en"
        html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5), transcript_spec=spec)
        assert "uncertain" not in html and "English (original language, auto-generated)" in html

    def test_no_transcript_section_no_note(self, info):
        # a bilingual version YouTube could not give makes no section
        info.update(without_audio_tracks())
        html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5),
                                   transcript_spec=[("bilingual", "it")])
        assert 'class="note uncertain"' not in html
        assert ".transcript .note{color:var(--muted);font-size:12px;" in html   # grey

    def test_a_transcript_of_an_unknown_language(self, info):
        info.update(without_audio_tracks())
        del info[yt2html.TRANSCRIPT_KEY]["name"]
        info[yt2html.TRANSCRIPT_KEY]["language"] = ""
        html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5))
        assert "so this transcript is in an unknown language, the captions" in html
        info[yt2html.TRANSCRIPT_KEY]["language"] = "en"           # no name: the code
        html = yt2html.render_page(info, None, {}, dt.date(2026, 11, 5))
        assert "so this transcript is in en, the captions" in html

    def test_the_console_warns(self, capsys, tmp_path, info):
        info.update(without_audio_tracks())
        data = tmp_path / "Title [TESTVIDEO01].info.json"
        data.write_text(json.dumps(info), encoding="utf-8")
        assert yt2html.main([str(data), "--json", "load", "-o", str(tmp_path), "--transcript"]) == 0
        warning = ("  Warning: the original language is uncertain: YouTube sent no audio tracks "
                   "to tell it from the dubs, so the transcript and the page's language are "
                   "taken as 'en' (the captions YouTube opens with). Make the page again later; "
                   "if YouTube asked you to confirm you're not a bot, try with --cookies.")
        out = capsys.readouterr().out
        assert warning in out and out.index("Saved page:") < out.index(warning)
        # without a transcript: only the page's language
        del info[yt2html.TRANSCRIPT_KEY]
        info.pop(yt2html.CAPTION_KEY)
        data.write_text(json.dumps(info), encoding="utf-8")
        assert yt2html.main([str(data), "--json", "load", "-o", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "so the page's language is taken as 'en' (the captions" in out
        # nothing to know it by at all
        info["language"] = None
        data.write_text(json.dumps(info), encoding="utf-8")
        yt2html.main([str(data), "--json", "load", "-o", str(tmp_path)])
        assert "taken as 'unknown'" in capsys.readouterr().out
        # known: no warning
        info[yt2html.ORIGINAL_AUDIO_KEY] = "pl"
        data.write_text(json.dumps(info), encoding="utf-8")
        yt2html.main([str(data), "--json", "load", "-o", str(tmp_path)])
        assert "Warning" not in capsys.readouterr().out
