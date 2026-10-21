import os
import re
import html
import json
import glob
import base64
import mimetypes
import tempfile
import subprocess
import ssl
import urllib.request
import urllib.error
import urllib.parse

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
import asyncio

ANKICONNECT_URL = "http://127.0.0.1:8765"

NOTE_TYPE_NAME = "Decky Anki Plugin Note Type"
NOTE_TYPE_FIELDS = ["Morph", "Definition/Translation", "Example", "Translation", "Image", "Audio"]
NOTE_TYPE_CSS = (
    ".card {\n"
    " font-family: arial;\n"
    " font-size: 20px;\n"
    " text-align: center;\n"
    " color: black;\n"
    " background-color: white;\n"
    "}"
)
NOTE_TYPE_FRONT_TEMPLATE = "{{Morph}}"
NOTE_TYPE_BACK_TEMPLATE = (
    "{{FrontSide}}\n\n<hr id=\"answer\">\n\n"
    "{{Definition/Translation}}\n\n{{Example}}\n\n{{Translation}}\n\n{{Image}}\n\n{{Audio}}"
)

# Bundled Tesseract OCR (bin/tesseract, static/musl build — see bin/NOTICE.md) and its
# tessdata_fast language files (bin/tessdata/), used to fill the "Example" field from whatever
# screenshot is currently set as the Image. Low-resource by design: a short-lived subprocess
# rather than a resident service, ~100-300MB peak RAM and well under a second per screenshot.
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
TESSERACT_BIN = os.path.join(PLUGIN_DIR, "bin", "tesseract")
TESSDATA_DIR = os.path.join(PLUGIN_DIR, "bin", "tessdata")

# Maps this plugin's language-selector display names (src/index.tsx LANGUAGE_OPTIONS) to
# Tesseract language codes / tessdata_fast filenames. Falls back to English for anything unmapped.
OCR_LANGUAGE_CODES = {
    "Japanese": "jpn",
    "English": "eng",
    "Spanish": "spa",
    "French": "fra",
    "German": "deu",
    "Korean": "kor",
    "Mandarin Chinese": "chi_sim",
    "Italian": "ita",
    "Portuguese": "por",
    "Russian": "rus",
    "Arabic": "ara",
}

# Same display names, mapped to Google Translate's language codes.
TRANSLATE_LANGUAGE_CODES = {
    "Japanese": "ja",
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Korean": "ko",
    "Mandarin Chinese": "zh-CN",
    "Italian": "it",
    "Portuguese": "pt",
    "Russian": "ru",
    "Arabic": "ar",
}

def _ankiconnect_request(action: str, params: dict = None, version: int = 6):
    payload = json.dumps({"action": action, "version": version, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(ANKICONNECT_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise Exception(body["error"])
    return body["result"]

# Google Translate's unofficial clients5 endpoint (what the Google Translate Chrome extension
# itself calls) — free, no API key. The translate.googleapis.com?client=gtx variant (tried first)
# got IP-throttled (HTTP 429) almost immediately on a real Deck; this one wasn't, in testing.
# shortcut: still unofficial/no SLA, could start throttling or break the same way if Google
# changes it — the official (API-key-based) Cloud Translation API is the fallback if so.
# Decky Loader is a PyInstaller-frozen binary that spawns plugins under its own bundled Python +
# OpenSSL (LD_LIBRARY_PATH points at its extracted temp dir); that bundled OpenSSL doesn't resolve
# SteamOS's actual CA bundle on its own, so any HTTPS call here fails with CERTIFICATE_VERIFY_FAILED
# unless we point it at the system bundle explicitly.
_SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

def _https_context():
    return ssl.create_default_context(cafile=_SYSTEM_CA_BUNDLE) if os.path.exists(_SYSTEM_CA_BUNDLE) else None

def _google_translate(text: str, source_lang: str, target_lang: str) -> str:
    params = urllib.parse.urlencode({"client": "dict-chrome-ex", "sl": source_lang, "tl": target_lang, "q": text})
    req = urllib.request.Request(f"https://clients5.google.com/translate_a/t?{params}")
    with urllib.request.urlopen(req, timeout=10, context=_https_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data[0]

# Jisho.org's public JSON API (what jisho.org's own search box calls) — free, no API key.
# shortcut: only the first, most-relevant entry's senses are used, capped at 3, to keep the
# Back field readable rather than dumping every sense of a common word.
def _jisho_lookup(word: str) -> str:
    params = urllib.parse.urlencode({"keyword": word})
    req = urllib.request.Request(f"https://jisho.org/api/v1/search/words?{params}")
    with urllib.request.urlopen(req, timeout=10, context=_https_context()) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    entries = data.get("data") or []
    if not entries:
        return ""
    entry = entries[0]
    japanese = (entry.get("japanese") or [{}])[0]
    headword = japanese.get("word") or word
    reading = japanese.get("reading") or ""
    lines = [f"{headword} ({reading})" if reading else headword]
    for i, sense in enumerate(entry.get("senses") or [], start=1):
        if i > 3:
            break
        glosses = "; ".join(sense.get("english_definitions") or [])
        if not glosses:
            continue
        pos = ", ".join(sense.get("parts_of_speech") or [])
        lines.append(f"{i}. {glosses}" + (f" ({pos})" if pos else ""))
    return "\n".join(lines)

# Same display names as OCR_LANGUAGE_CODES/TRANSLATE_LANGUAGE_CODES, mapped to Wiktionary's
# language codes — used for every language except Japanese, which routes to Jisho instead (far
# better Japanese/English coverage). en.wiktionary.org's own definition API covers every language
# here directly (tried dictionaryapi.dev first: English-only in practice, every other language's
# dataset there currently 530s).
DICTIONARY_LANGUAGE_CODES = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Korean": "ko",
    "Mandarin Chinese": "zh",
    "Italian": "it",
    "Portuguese": "pt",
    "Russian": "ru",
    "Arabic": "ar",
}

def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()

# shortcut: only the first 3 definitions (across all parts of speech) are used, to keep the Back
# field readable rather than dumping every sense of a common word.
def _wiktionary_lookup(word: str, lang_code: str) -> str:
    req = urllib.request.Request(
        f"https://en.wiktionary.org/api/rest_v1/page/definition/{urllib.parse.quote(word, safe='')}"
    )
    req.add_header("User-Agent", "decky-anki-cards/0.0.1 (https://github.com/nromanm/decky-anki-cards)")
    try:
        with urllib.request.urlopen(req, timeout=10, context=_https_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""
        raise
    senses = data.get(lang_code) or []
    if not senses:
        return ""
    lines = [word]
    count = 0
    for sense_group in senses:
        pos = sense_group.get("partOfSpeech") or ""
        for definition in sense_group.get("definitions") or []:
            text = _strip_html(definition.get("definition") or "")
            if not text:
                continue
            count += 1
            if count > 3:
                return "\n".join(lines)
            lines.append(f"{count}. {text}" + (f" ({pos})" if pos else ""))
    return "\n".join(lines)

DECK_NAME = "Decky Anki Plugin Deck"

# Builds an AnkiConnect picture/audio media entry from a user-typed path or URL, or None if
# empty. AnkiConnect distinguishes remote vs local media by which param is set: `url` for
# http(s), `data` (base64) for a local filesystem path. Confirmed via `flatpak info
# --show-permissions net.ankiweb.Anki`: its sandbox has no filesystem access outside its own app
# data, so handing it a host path (e.g. Steam's screenshots dir, or our own /tmp audio extraction)
# always fails with ENOENT from Anki's side regardless of whether the file genuinely exists —
# AnkiConnect then writes that raw read error into the field's text instead of failing cleanly.
# Reading the bytes ourselves (this process isn't sandboxed) and sending them as data sidesteps
# that entirely; a path that's actually missing on our side is still skipped rather than guessed.
def _build_media_entry(value: str, field_name: str):
    value = (value or "").strip()
    if not value:
        return None
    entry = {"fields": [field_name]}
    if value.lower().startswith(("http://", "https://")):
        filename = os.path.basename(urllib.parse.urlsplit(value).path) or f"decky_{field_name.lower()}"
        entry["url"] = value
    else:
        path = os.path.expanduser(value)
        if not os.path.isfile(path):
            decky.logger.warning(f"_build_media_entry: {field_name} path {path!r} does not exist, skipping")
            return None
        filename = os.path.basename(path) or f"decky_{field_name.lower()}"
        with open(path, "rb") as f:
            entry["data"] = base64.b64encode(f.read()).decode("ascii")
    entry["filename"] = filename
    return entry

# Steam's "Record In Background" clips aren't exposed through a SteamClient API the way
# screenshots are (no GetLastRecordingTaken equivalent) — they're saved as segmented DASH media
# under ~/.local/share/Steam/userdata/<id>/gamerecordings/clips/clip_<appid>_<yyyymmdd>_<hhmmss>/
# video/, which holds one "bg_*" subdirectory (the pre-trigger background buffer) and/or one
# "fg_*" subdirectory (the post-trigger continuation) — a saved clip is their concatenation, not
# just one of them. Each subdirectory holds its own session.mpd plus init-stream<N>.m4s +
# chunk-stream<N>-*.m4s per track (0 = video, 1 = audio). Scanning every userdata dir (there can
# be several, one per account that ever signed in) handles the multi-account case without needing
# to know which account is "active".
#
# Picked by the timestamp embedded in the clip's own directory name, not by filesystem mtime —
# mtimes on the "video"/segment directories can be touched later (e.g. background thumbnail or
# metadata generation) without that touch meaning the clip is actually newer, which could
# silently select the wrong clip.
_CLIP_TIMESTAMP_RE = re.compile(r"^clip_\d+_(\d{8}_\d{6})$")

def _find_latest_recording_video_dir():
    pattern = os.path.join(
        decky.DECKY_USER_HOME, ".local", "share", "Steam", "userdata",
        "*", "gamerecordings", "clips", "clip_*",
    )
    dated = []
    for clip_dir in glob.glob(pattern):
        if not os.path.isdir(clip_dir):
            continue
        match = _CLIP_TIMESTAMP_RE.match(os.path.basename(clip_dir))
        if match:
            dated.append((match.group(1), clip_dir))
    if not dated:
        return None
    _, latest_clip_dir = max(dated, key=lambda item: item[0])
    video_dir = os.path.join(latest_clip_dir, "video")
    return video_dir if os.path.isdir(video_dir) else None

# "bg_" sorts before "fg_" alphabetically, which also happens to be their chronological order
# (background buffer precedes the foreground continuation), so a plain name sort is enough.
def _recording_segment_dirs(video_dir: str):
    return sorted(d for d in glob.glob(os.path.join(video_dir, "*")) if os.path.isdir(d))

_CHUNK_INDEX_RE = re.compile(r"chunk-stream1-(\d+)\.m4s$")

# Concatenating each segment's audio init segment followed by its numbered chunks, in order,
# then concatenating the segments themselves in bg-then-fg order, produces a single stream
# ffmpeg can read as the clip's full audio — standard DASH segment concatenation, no container
# rewriting needed before handing it to ffmpeg.
def _concat_audio_segments(segment_dirs, out_path: str) -> None:
    wrote_any = False
    with open(out_path, "wb") as out:
        for seg_dir in segment_dirs:
            init_path = os.path.join(seg_dir, "init-stream1.m4s")
            if not os.path.isfile(init_path):
                continue
            chunk_paths = [p for p in glob.glob(os.path.join(seg_dir, "chunk-stream1-*.m4s")) if _CHUNK_INDEX_RE.search(p)]
            chunk_paths.sort(key=lambda p: int(_CHUNK_INDEX_RE.search(p).group(1)))
            with open(init_path, "rb") as f:
                out.write(f.read())
            for chunk_path in chunk_paths:
                with open(chunk_path, "rb") as f:
                    out.write(f.read())
            wrote_any = True
    if not wrote_any:
        raise Exception("This clip has no separate audio track to extract.")

# None (rather than 0.0) when the probe itself failed, so a real zero-length clip isn't
# confused with ffprobe silently not working.
def _ffprobe_duration(path: str):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError):
        return None

# Colored/stylized game text often has poor contrast once Tesseract reduces it to grayscale
# (its internal luminance-weighted conversion can land a saturated color very close to the
# background's luminance even though the color is clearly distinct to a person). No single
# color channel is reliably best across cases — R, G and B each won on a different test image —
# so this runs OCR once per channel and keeps whichever result Tesseract itself is most
# confident in, via its own per-word confidence score (TSV output), rather than guessing.
_OCR_CHANNELS = ("r", "g", "b")

# Words below this are near-certainly garbage (stray symbols picked up from texture/noise) and
# are dropped entirely rather than left in the output — confirmed against a real screenshot
# where a channel's confident recognition of most of the screen still ended with a few such
# words trailing off into noise.
_OCR_MIN_WORD_CONFIDENCE = 50.0

def _parse_tsv_confidence_and_text(tsv_path: str):
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    words = []  # (line_key, confidence, text)
    for line in lines[1:]:
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # level 5 = word-level row
            continue
        text = cols[11]
        if not text.strip():
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            conf = -1.0
        if conf < _OCR_MIN_WORD_CONFIDENCE:
            continue
        line_key = (cols[2], cols[3], cols[4])  # block_num, par_num, line_num
        words.append((line_key, conf, text))

    if not words:
        return "", 0.0

    lines_out = []
    current_key, current_words = None, []
    for line_key, _, text in words:
        if line_key != current_key:
            if current_words:
                lines_out.append(" ".join(current_words))
                # Blank line between paragraphs (not just line breaks within one), so multi-block
                # screen text doesn't read as a single run-on paragraph.
                if current_key is not None and (line_key[0], line_key[1]) != (current_key[0], current_key[1]):
                    lines_out.append("")
            current_key, current_words = line_key, []
        current_words.append(text)
    if current_words:
        lines_out.append(" ".join(current_words))

    # Summed, not averaged: a channel that correctly reads most of the screen must outscore one
    # that reads only a confident fragment of it — a plain average rewards the smaller fragment
    # whenever its words happen to score a little higher on average.
    total_confidence = sum(conf for _, conf, _ in words)
    return "\n".join(lines_out), total_confidence

# Converts the 4 crop sliders (0-100, percent of the image to cut from that edge) into 0-1
# fractions, clamped so at least 10% of each dimension always remains, however the sliders are set.
def _clamp_crop_fractions(crop_top: float, crop_left: float, crop_right: float, crop_bottom: float):
    top = max(0.0, min(crop_top, 90.0)) / 100.0
    left = max(0.0, min(crop_left, 90.0)) / 100.0
    right = max(0.0, min(crop_right, 90.0 - left * 100.0)) / 100.0
    bottom = max(0.0, min(crop_bottom, 90.0 - top * 100.0)) / 100.0
    return top, left, right, bottom

class Plugin:
    # Reads a local image file and returns it as a data: URI, for the frontend to render as an
    # <img> preview. Steam's own Screenshot.strUrl isn't loadable from the plugin's content (wrong
    # origin/CSP for that internal URL) — a data: URI needs no network fetch, so it always renders
    # regardless of origin.
    async def get_image_preview(self, path: str) -> str:
        loop = asyncio.get_event_loop()
        try:
            def _read():
                mime_type, _ = mimetypes.guess_type(path)
                mime_type = mime_type or "image/png"
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                return f"data:{mime_type};base64,{data}"
            return await loop.run_in_executor(None, _read)
        except Exception as e:
            decky.logger.error(f"get_image_preview: could not read {path!r}: {e}")
            raise Exception(f"Could not load image preview: {e}")

    # Reads the extracted .opus audio file and returns it as a data: URI, for the frontend to
    # play with a plain <audio> element (same reasoning as get_image_preview: a data: URI needs
    # no network fetch, so it always plays regardless of origin/CSP). Always Ogg Opus, since this
    # only ever reads take_last_recording_audio's own output, never an arbitrary user file.
    async def get_audio_preview(self, path: str) -> str:
        loop = asyncio.get_event_loop()
        try:
            def _read():
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                return f"data:audio/ogg;base64,{data}"
            return await loop.run_in_executor(None, _read)
        except Exception as e:
            decky.logger.error(f"get_audio_preview: could not read {path!r}: {e}")
            raise Exception(f"Could not load audio preview: {e}")

    # Runs the bundled Tesseract OCR over the given screenshot (whatever path the Image section
    # currently has) for the "Example" field. `language` is the plugin's display name (e.g.
    # "Japanese"), mapped to a Tesseract language code; unmapped values fall back to English.
    # Per-channel rationale: see _OCR_CHANNELS above. crop_top/left/right/bottom (0-100, percent
    # of the image cropped away from that edge) narrow the OCR'd area to just the text the user
    # cares about — smaller + noisier backgrounds cropped out tends to read better, and the crop
    # also comes out of the same 2x upscale every channel already gets, so a small cropped region
    # ends up more magnified than the full image would.
    async def ocr_screenshot(
        self, path: str, language: str,
        crop_top: float = 0.0, crop_left: float = 0.0, crop_right: float = 0.0, crop_bottom: float = 0.0,
    ) -> str:
        loop = asyncio.get_event_loop()

        def _run():
            # Best-effort: the deployed binary is already executable (root-owned via
            # scripts/deploy.sh), and this backend runs as a non-root user, so it can't chmod a
            # file it doesn't own — EPERM here just means it was already fine.
            try:
                os.chmod(TESSERACT_BIN, 0o755)
            except OSError:
                pass
            lang_code = OCR_LANGUAGE_CODES.get(language, "eng")

            top, left, right, bottom = _clamp_crop_fractions(crop_top, crop_left, crop_right, crop_bottom)
            crop_filter = (
                f"crop=iw*{1 - left - right}:ih*{1 - top - bottom}:iw*{left}:ih*{top},"
                if (top or left or right or bottom) else ""
            )

            best_text, best_confidence = "", -1.0
            with tempfile.TemporaryDirectory() as workdir:
                for channel in _OCR_CHANNELS:
                    variant_path = os.path.join(workdir, f"variant_{channel}.png")
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", path,
                             "-vf", f"{crop_filter}format=rgb24,extractplanes={channel},scale=iw*2:ih*2:flags=lanczos",
                             variant_path],
                            capture_output=True, timeout=15, check=True,
                        )
                        out_base = os.path.join(workdir, f"out_{channel}")
                        subprocess.run(
                            [TESSERACT_BIN, variant_path, out_base, "-l", lang_code,
                             "--tessdata-dir", TESSDATA_DIR, "-c", "tessedit_create_tsv=1"],
                            capture_output=True, timeout=15, check=True,
                        )
                        text, confidence = _parse_tsv_confidence_and_text(out_base + ".tsv")
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                        decky.logger.warning(f"ocr_screenshot: channel {channel!r} failed: {e}")
                        continue
                    if text and confidence > best_confidence:
                        best_text, best_confidence = text, confidence

            return best_text

        try:
            return await loop.run_in_executor(None, _run)
        except Exception as e:
            decky.logger.error(f"ocr_screenshot: {e}")
            raise Exception(str(e))

    async def translate_text(self, text: str, source_language: str, target_language: str) -> str:
        loop = asyncio.get_event_loop()
        source = TRANSLATE_LANGUAGE_CODES.get(source_language, "en")
        target = TRANSLATE_LANGUAGE_CODES.get(target_language, "en")
        try:
            return await loop.run_in_executor(None, _google_translate, text, source, target)
        except Exception as e:
            decky.logger.error(f"translate_text: {e!r}")
            raise Exception(f"Could not reach Google Translate: {e}")

    async def lookup_dictionary(self, word: str, language: str) -> str:
        loop = asyncio.get_event_loop()
        if language != "Japanese" and language not in DICTIONARY_LANGUAGE_CODES:
            raise Exception(f"Dictionary lookup isn't available for {language}.")
        try:
            if language == "Japanese":
                return await loop.run_in_executor(None, _jisho_lookup, word)
            return await loop.run_in_executor(
                None, _wiktionary_lookup, word, DICTIONARY_LANGUAGE_CODES[language]
            )
        except Exception as e:
            decky.logger.error(f"lookup_dictionary: {e!r}")
            raise Exception(f"Could not look up that word: {e}")

    # Extracts just the audio track from the most recently saved Steam "Record In Background"
    # clip (Settings > System > Recording must be on, then save a clip with its hotkey) —
    # analogous to the Image field's screenshot capture, but clips aren't exposed through a
    # SteamClient API the way screenshots are, so this locates the clip's segmented DASH files on
    # disk directly and re-encodes only the audio track (compact Opus), never the video, to keep
    # this both fast and light on space. Only its own temp files get cleaned up here — the
    # original saved clip (video + audio) is left alone, since that's the user's own deliberately
    # saved recording, not a throwaway artifact like a screenshot.
    async def take_last_recording_audio(self) -> dict:
        loop = asyncio.get_event_loop()

        def _extract():
            video_dir = _find_latest_recording_video_dir()
            if video_dir is None:
                raise Exception(
                    "No saved game recording found. Enable \"Record In Background\" in "
                    "Settings > System > Recording, then save a clip before trying again."
                )
            segment_dirs = _recording_segment_dirs(video_dir)
            combined_fd, combined_path = tempfile.mkstemp(suffix=".m4s")
            os.close(combined_fd)
            output_fd, output_path = tempfile.mkstemp(suffix=".opus")
            os.close(output_fd)
            try:
                try:
                    _concat_audio_segments(segment_dirs, combined_path)
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", combined_path, "-vn", "-c:a", "libopus", "-b:a", "64k", output_path],
                        capture_output=True, timeout=30, check=True,
                    )
                except Exception:
                    os.remove(output_path)
                    raise
            finally:
                os.remove(combined_path)
            duration = _ffprobe_duration(output_path)
            return output_path, duration

        try:
            path, duration = await loop.run_in_executor(None, _extract)
            return {"path": path, "duration": duration}
        except subprocess.CalledProcessError as e:
            decky.logger.error(f"take_last_recording_audio: ffmpeg failed: {e.stderr}")
            raise Exception("Could not process the recording's audio.")
        except Exception as e:
            decky.logger.error(f"take_last_recording_audio: {e}")
            raise Exception(str(e))

    # Discards a temp audio file produced by take_last_recording_audio when the user retakes or
    # deletes it before ever adding a card — without this, every Retake/Delete that doesn't lead
    # to a successful Add Card would leak a small file under the system temp directory.
    async def discard_audio_file(self, path: str) -> None:
        try:
            os.remove(path)
        except OSError as e:
            decky.logger.info(f"discard_audio_file: could not delete {path!r}: {e}")

    # Lightweight ping to check whether Anki + AnkiConnect are reachable right now. Swallows
    # every error (connection refused, timeout, ...) and just reports false, since callers only
    # care about the yes/no status, not the specific failure.
    async def check_anki_connection(self) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _ankiconnect_request, "version")
            return True
        except Exception as e:
            decky.logger.info(f"check_anki_connection: not connected: {e}")
            return False

    # One combined readiness check instead of several separate round trips: is Anki even
    # installed, is AnkiConnect reachable, does the plugin's deck already exist, and does a sync
    # attempt succeed (folds in "logged into AnkiWeb" and "collection busy" — whatever Anki's own
    # sync error says, verbatim, covers both without us trying to probe its internals directly).
    async def check_setup_status(self) -> dict:
        loop = asyncio.get_event_loop()
        status = {
            "anki_installed": False,
            "anki_connect_reachable": False,
            "deck_exists": False,
            "sync_ok": False,
            "sync_error": "",
        }
        status["anki_installed"] = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["flatpak", "info", "net.ankiweb.Anki"], capture_output=True, timeout=5
            ).returncode == 0,
        )
        try:
            await loop.run_in_executor(None, _ankiconnect_request, "version")
            status["anki_connect_reachable"] = True
        except Exception as e:
            decky.logger.info(f"check_setup_status: AnkiConnect unreachable: {e}")
            return status

        try:
            deck_names = await loop.run_in_executor(None, _ankiconnect_request, "deckNames")
            status["deck_exists"] = DECK_NAME in deck_names
        except Exception as e:
            decky.logger.warning(f"check_setup_status: deckNames failed: {e}")

        try:
            await loop.run_in_executor(None, _ankiconnect_request, "sync")
            status["sync_ok"] = True
        except Exception as e:
            status["sync_error"] = str(e)

        return status

    # Manual trigger for the same best-effort sync add_note already fires after adding a card —
    # unlike that one, failures here are surfaced to the user instead of just logged, since this
    # is the user explicitly asking "did it sync?".
    async def sync_anki(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _ankiconnect_request, "sync")
        except Exception as e:
            decky.logger.error(f"sync_anki: {e}")
            raise Exception(f"Could not sync: {e}")

    # Best-effort stop of the optional always-on background service (scripts/setup-anki-service.sh)
    # some users set up for AnkiConnect. Anki enforces a single profile lock, so if that service's
    # instance is already running, launching Anki another way (the "Open Anki" shortcut) can't get
    # a usable window — Steam switches focus, finds nothing there, and bounces right back. A no-op
    # (harmless non-zero exit, swallowed) if the service was never set up or isn't running.
    async def stop_background_anki(self) -> None:
        loop = asyncio.get_event_loop()

        def _run():
            # This backend process's own environment has no XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS
            # (see CLAUDE.md: Decky runs plugins in a stripped-down environment), which `systemctl
            # --user` needs to reach the user's systemd instance — without it the command fails
            # silently (non-zero exit, no exception) and the service is never actually stopped.
            env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
            result = subprocess.run(
                ["systemctl", "--user", "stop", "anki-background.service"],
                capture_output=True, timeout=5, env=env,
            )
            if result.returncode != 0:
                decky.logger.info(
                    f"stop_background_anki: systemctl exited {result.returncode}: "
                    f"{result.stderr.decode(errors='replace').strip()}"
                )
            # `systemctl stop` only tears down the service's own `flatpak run` wrapper, not the
            # sandboxed Anki process it hands off to — that process is left running, and Anki's own
            # single-instance D-Bus activation then makes every fresh launch just silently hand off
            # to it instead of opening its own window ("Already running; reusing existing instance."
            # in the Steam log), which looks like an instant crash to Steam. Kill the sandbox itself.
            subprocess.run(["flatpak", "kill", "net.ankiweb.Anki"], capture_output=True, timeout=5, env=env)

        await loop.run_in_executor(None, _run)

    # Manual counterpart to stop_background_anki, for the user to turn the optional always-on
    # service (scripts/setup-anki-service.sh) back on after using "Open Anki" — no-op (harmless
    # non-zero exit, swallowed) if it was never set up.
    async def start_background_anki(self) -> None:
        loop = asyncio.get_event_loop()

        def _run():
            env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
            result = subprocess.run(
                ["systemctl", "--user", "start", "anki-background.service"],
                capture_output=True, timeout=5, env=env,
            )
            if result.returncode != 0:
                decky.logger.info(
                    f"start_background_anki: systemctl exited {result.returncode}: "
                    f"{result.stderr.decode(errors='replace').strip()}"
                )

        await loop.run_in_executor(None, _run)

    async def check_background_anki_status(self) -> bool:
        loop = asyncio.get_event_loop()

        def _run():
            env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "anki-background.service"],
                capture_output=True, timeout=5, env=env,
            )
            return result.returncode == 0

        return await loop.run_in_executor(None, _run)

    async def _ensure_deck_and_model(self, loop, deck_name: str) -> None:
        await loop.run_in_executor(None, _ankiconnect_request, "createDeck", {"deck": deck_name})
        existing_models = await loop.run_in_executor(None, _ankiconnect_request, "modelNames")
        if NOTE_TYPE_NAME not in existing_models:
            await loop.run_in_executor(None, _ankiconnect_request, "createModel", {
                "modelName": NOTE_TYPE_NAME,
                "inOrderFields": NOTE_TYPE_FIELDS,
                "css": NOTE_TYPE_CSS,
                "isCloze": False,
                "cardTemplates": [
                    {"Name": "Card 1", "Front": NOTE_TYPE_FRONT_TEMPLATE, "Back": NOTE_TYPE_BACK_TEMPLATE},
                ],
            })
        else:
            # Migration for note types created before later fields (Example, Translation, ...)
            # existed. Adds whatever's missing, in NOTE_TYPE_FIELDS order, at its intended index.
            existing_fields = await loop.run_in_executor(
                None, _ankiconnect_request, "modelFieldNames", {"modelName": NOTE_TYPE_NAME})
            for index, field_name in enumerate(NOTE_TYPE_FIELDS):
                if field_name not in existing_fields:
                    await loop.run_in_executor(None, _ankiconnect_request, "modelFieldAdd", {
                        "modelName": NOTE_TYPE_NAME,
                        "fieldName": field_name,
                        "index": index,
                    })
                    existing_fields.insert(index, field_name)

            # modelFieldAdd only adds the field slot — it never touches the card template, so a
            # note type from before Example/Translation existed would keep storing that data
            # (confirmed via notesInfo) without ever displaying it: the old "Card 1" template's
            # Back HTML simply has no {{Example}}/{{Translation}} placeholder to render. Push the
            # current template (and styling) every time; harmless no-op if already up to date.
            await loop.run_in_executor(None, _ankiconnect_request, "updateModelTemplates", {
                "model": {
                    "name": NOTE_TYPE_NAME,
                    "templates": {"Card 1": {"Front": NOTE_TYPE_FRONT_TEMPLATE, "Back": NOTE_TYPE_BACK_TEMPLATE}},
                },
            })
            await loop.run_in_executor(None, _ankiconnect_request, "updateModelStyling", {
                "model": {"name": NOTE_TYPE_NAME, "css": NOTE_TYPE_CSS},
            })

    # Creates (or reuses, if already present) the plugin's single deck and shared note type.
    # Requires Anki running with AnkiConnect installed.
    async def create_deck(self) -> str:
        loop = asyncio.get_event_loop()
        try:
            await self._ensure_deck_and_model(loop, DECK_NAME)
            decky.logger.info(f"create_deck: ensured deck {DECK_NAME!r} and note type {NOTE_TYPE_NAME!r}")
            return DECK_NAME
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Adds a note to the plugin's deck, creating the deck/note type first if needed. Image and
    # audio are AnkiConnect media refs (path or URL), not raw field text.
    async def add_note(self, morph: str, definition: str, example: str, translation: str, image: str, audio: str) -> int:
        loop = asyncio.get_event_loop()
        try:
            if not morph or not morph.strip():
                raise Exception("Morph is required.")
            await self._ensure_deck_and_model(loop, DECK_NAME)

            note = {
                "deckName": DECK_NAME,
                "modelName": NOTE_TYPE_NAME,
                "fields": {
                    "Morph": morph,
                    "Definition/Translation": definition,
                    "Example": example,
                    "Translation": translation,
                    "Image": "",
                    "Audio": "",
                },
                "options": {"allowDuplicate": False},
                "tags": [],
            }
            picture_entry = _build_media_entry(image, "Image")
            if picture_entry:
                note["picture"] = [picture_entry]
            audio_entry = _build_media_entry(audio, "Audio")
            if audio_entry:
                note["audio"] = [audio_entry]

            note_id = await loop.run_in_executor(None, _ankiconnect_request, "addNote", {"note": note})
            decky.logger.info(f"add_note: added note {note_id} to deck {DECK_NAME!r}")

            # AnkiConnect has already read and stored the audio data at this point, so the local
            # temp file is redundant — clean it up so it doesn't pile up on the Deck's storage.
            # Audio is always our own extracted temp file (never an arbitrary user path), so it's
            # always safe to delete. The image path, unlike audio, is the user's actual Steam
            # screenshot (never a copy we made) — never delete that.
            if audio_entry and os.path.isfile(audio):
                try:
                    os.remove(audio)
                    decky.logger.info(f"add_note: deleted local audio {audio!r} after upload")
                except OSError as e:
                    decky.logger.warning(f"add_note: could not delete local audio {audio!r}: {e}")

            # Best-effort: same as pressing Anki's own sync button. Never let a sync failure (not
            # logged into AnkiWeb, no network, conflict needing manual resolution, ...) mask that
            # the card itself was already added successfully.
            try:
                await loop.run_in_executor(None, _ankiconnect_request, "sync")
            except Exception as e:
                decky.logger.warning(f"add_note: sync failed: {e}")

            return note_id
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Asyncio-compatible long-running code, executed in a task when the plugin is loaded
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        decky.logger.info("Hello World!")

    # Function called first during the unload process, utilize this to handle your plugin being stopped, but not
    # completely removed
    async def _unload(self):
        decky.logger.info("Goodnight World!")
        pass

    # Function called after `_unload` during uninstall, utilize this to clean up processes and other remnants of your
    # plugin that may remain on the system
    async def _uninstall(self):
        decky.logger.info("Goodbye World!")
        pass

    # Migrations that should be performed before entering `_main()`.
    async def _migration(self):
        decky.logger.info("Migrating")
        # Here's a migration example for logs:
        # - `~/.config/decky-template/template.log` will be migrated to `decky.decky_LOG_DIR/template.log`
        decky.migrate_logs(os.path.join(decky.DECKY_USER_HOME,
                                               ".config", "decky-template", "template.log"))
        # Here's a migration example for settings:
        # - `~/homebrew/settings/template.json` is migrated to `decky.decky_SETTINGS_DIR/template.json`
        # - `~/.config/decky-template/` all files and directories under this root are migrated to `decky.decky_SETTINGS_DIR/`
        decky.migrate_settings(
            os.path.join(decky.DECKY_HOME, "settings", "template.json"),
            os.path.join(decky.DECKY_USER_HOME, ".config", "decky-template"))
        # Here's a migration example for runtime data:
        # - `~/homebrew/template/` all files and directories under this root are migrated to `decky.decky_RUNTIME_DIR/`
        # - `~/.local/share/decky-template/` all files and directories under this root are migrated to `decky.decky_RUNTIME_DIR/`
        decky.migrate_runtime(
            os.path.join(decky.DECKY_HOME, "template"),
            os.path.join(decky.DECKY_USER_HOME, ".local", "share", "decky-template"))
