import os
import re
import json
import glob
import base64
import mimetypes
import tempfile
import subprocess
import urllib.request
import urllib.error
import urllib.parse

# The decky plugin module is located at decky-loader/plugin
# For easy intellisense checkout the decky-loader code repo
# and add the `decky-loader/plugin/imports` path to `python.analysis.extraPaths` in `.vscode/settings.json`
import decky
import asyncio

ANKICONNECT_URL = "http://127.0.0.1:8765"

# Set up by scripts/setup-anki-service.sh, run once by the user directly on the Deck — this
# plugin never installs or enables it itself, only checks its status.
ANKI_SERVICE_UNIT = "anki-background.service"

NOTE_TYPE_NAME = "Decky Anki Plugin Note Type"
NOTE_TYPE_FIELDS = ["Morph", "Definition/Translation", "Image", "Audio"]
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
    "{{Definition/Translation}}\n\n{{Image}}\n\n{{Audio}}"
)

def _ankiconnect_request(action: str, params: dict = None, version: int = 6):
    payload = json.dumps({"action": action, "version": version, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(ANKICONNECT_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise Exception(body["error"])
    return body["result"]

DECK_NAME = "Decky Anki Plugin Deck"

# Builds an AnkiConnect picture/audio media entry from a user-typed path or URL, or None if
# empty. AnkiConnect distinguishes remote vs local media by which param is set: `url` for
# http(s), `path` for a local filesystem path (e.g. a file on the Deck itself).
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
        filename = os.path.basename(path) or f"decky_{field_name.lower()}"
        entry["path"] = path
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

    # Read-only check of the optional background systemd service (set up by
    # scripts/setup-anki-service.sh, never by this plugin). Returns systemctl's raw state string
    # ("active", "inactive", "failed", ...), or "unknown" if the check itself couldn't run (e.g.
    # the service was never set up, so systemd has no user session bus to query in some cases).
    async def check_anki_service_status(self) -> str:
        loop = asyncio.get_event_loop()
        try:
            env = os.environ.copy()
            env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["systemctl", "--user", "is-active", ANKI_SERVICE_UNIT],
                    capture_output=True, text=True, env=env, timeout=5,
                ),
            )
            return result.stdout.strip() or "unknown"
        except Exception as e:
            decky.logger.info(f"check_anki_service_status: could not check: {e}")
            return "unknown"

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

    # Creates (or reuses, if already present) the plugin's single deck and shared note type.
    # Requires Anki running with AnkiConnect installed.
    async def create_deck(self) -> str:
        loop = asyncio.get_event_loop()
        try:
            await self._ensure_deck_and_model(loop, DECK_NAME)
            decky.logger.info(f"create_deck: ensured deck {DECK_NAME!r} and note type {NOTE_TYPE_NAME!r}")
            return DECK_NAME
        except (urllib.error.URLError, ConnectionError) as e:
            decky.logger.error(f"Could not reach AnkiConnect at {ANKICONNECT_URL}: {e}")
            raise Exception(f"Could not reach AnkiConnect. Is Anki running with AnkiConnect installed? ({e})")

    # Adds a note to the plugin's deck, creating the deck/note type first if needed. Image and
    # audio are AnkiConnect media refs (path or URL), not raw field text.
    async def add_note(self, morph: str, definition: str, image: str, audio: str) -> int:
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

            # AnkiConnect has already copied the image/audio into Anki's own media collection at
            # this point, so the local source files (if local paths, not URLs) are redundant —
            # clean them up so they don't pile up on the Deck's storage. The audio path in
            # particular is always our own extracted temp file (never an arbitrary user path), so
            # it's always safe to delete.
            for label, entry in (("image", picture_entry), ("audio", audio_entry)):
                if entry and "path" in entry:
                    try:
                        os.remove(entry["path"])
                        decky.logger.info(f"add_note: deleted local {label} {entry['path']!r} after upload")
                    except OSError as e:
                        decky.logger.warning(f"add_note: could not delete local {label} {entry['path']!r}: {e}")

            return note_id
        except (urllib.error.URLError, ConnectionError) as e:
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
