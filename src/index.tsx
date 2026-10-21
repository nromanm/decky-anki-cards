import {
  ButtonItem,
  DialogButton,
  Dropdown,
  DropdownOption,
  Field,
  Focusable,
  PanelSection,
  PanelSectionRow,
  SliderField,
  TextField,
  ToggleField,
  staticClasses
} from "@decky/ui";
import {
  callable,
  definePlugin,
  toaster,
} from "@decky/api"
import { useEffect, useState } from "react";
import { FaShip } from "react-icons/fa";

const checkAnkiConnection = callable<[], boolean>("check_anki_connection");
const getImagePreview = callable<[path: string], string>("get_image_preview");
const getAudioPreview = callable<[path: string], string>("get_audio_preview");
const takeLastRecordingAudio = callable<[], { path: string; duration: number | null }>("take_last_recording_audio");
const discardAudioFile = callable<[path: string], void>("discard_audio_file");
const ocrScreenshot = callable<[path: string, language: string, cropTop: number, cropLeft: number, cropRight: number, cropBottom: number], string>("ocr_screenshot");
const createDeck = callable<[], string>("create_deck");
const addNote = callable<[morph: string, definition: string, example: string, translation: string, image: string, audio: string], number>("add_note");
const translateText = callable<[text: string, sourceLanguage: string, targetLanguage: string], string>("translate_text");
const stopBackgroundAnki = callable<[], void>("stop_background_anki");
const startBackgroundAnki = callable<[], void>("start_background_anki");
const checkBackgroundAnkiStatus = callable<[], boolean>("check_background_anki_status");
const syncAnki = callable<[], void>("sync_anki");
const lookupDictionary = callable<[word: string, language: string], string>("lookup_dictionary");
const checkSetupStatus = callable<[], {
  anki_installed: boolean;
  anki_connect_reachable: boolean;
  deck_exists: boolean;
  sync_ok: boolean;
  sync_error: string;
}>("check_setup_status");

const DECK_NAME = "Decky Anki Plugin Deck";

// Single source of truth for the language preset list used by the origin/target selectors below
// (prep for a future feature: OCR the Morph/Text off a screenshot and auto-translate it). Add or
// remove a language here only — no backend change needed.
const LANGUAGE_OPTIONS = [
  "Japanese", "English", "Spanish", "French", "German", "Korean",
  "Mandarin Chinese", "Italian", "Portuguese", "Russian", "Arabic",
];

// Both a headless background launch and piggybacking on another game's Launch Options were
// confirmed broken: Anki starts but hangs before AnkiConnect loads (likely stuck on a dialog,
// since it's not a properly session-integrated app that way). The one thing confirmed to work
// reliably is launching Anki as its own real Steam app — same mechanism Quick-Access-Menu app
// launchers use (a non-Steam-game shortcut + RunGame). Tradeoff: this does switch focus away
// from the current game (gamescope can only focus one app at a time), but the game keeps running
// and switching back is quick.
//
// Steam's own flatpak-aware shortcut creator (InstallFlatpakAppAndCreateShortcut) was tried next,
// to get Anki's FlatpakAppID set correctly — but it runs an "ensure installed" flatpak command
// internally, which hits an interactive remote-choice prompt ("flathub found in multiple
// installations: system/user") on setups with both a system and a user flathub remote, and just
// aborts (appid 0) with no interactive terminal to answer it. Back to the plain version.
const ANKI_FLATPAK_EXE = "/usr/bin/flatpak";
const ANKI_FLATPAK_LAUNCH_OPTIONS = "run net.ankiweb.Anki";

// Non-Steam-game shortcut id for Anki, created lazily on first launch and reused after that.
// Module scope (not React state) so it survives the QAM remount quirk described below.
let ankiShortcutAppId: number | null = null;

async function ensureAnkiShortcut(): Promise<number> {
  if (ankiShortcutAppId !== null && window.appStore.GetAppOverviewByAppID(ankiShortcutAppId)) {
    return ankiShortcutAppId;
  }
  const id = await SteamClient.Apps.AddShortcut("Anki", ANKI_FLATPAK_EXE, "", ANKI_FLATPAK_LAUNCH_OPTIONS);
  SteamClient.Apps.SetShortcutName(id, "Anki");
  SteamClient.Apps.SetShortcutExe(id, ANKI_FLATPAK_EXE);
  SteamClient.Apps.SetShortcutLaunchOptions(id, ANKI_FLATPAK_LAUNCH_OPTIONS);
  SteamClient.Apps.SpecifyCompatTool(id, "");
  ankiShortcutAppId = id;
  return id;
}

async function launchAnki(): Promise<void> {
  // Confirmed (don't remove this): skipping this kill leaves the background service's existing
  // instance alive, and Anki's own single-instance D-Bus activation just silently hands off to it
  // instead of opening a new window — Steam's shortcut-launched process exits almost immediately
  // either way, so there's no way to "raise" the existing headless instance's window this way.
  await stopBackgroundAnki().catch((e) => console.error("[AnkiCards] stopBackgroundAnki failed:", e));
  await new Promise((resolve) => setTimeout(resolve, 1000));

  const appId = await ensureAnkiShortcut();
  // Give Steam a moment to register the shortcut's exe/launch options before running it.
  let overview = null;
  for (let attempt = 0; attempt < 10 && !overview; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    overview = window.appStore.GetAppOverviewByAppID(appId);
  }
  if (!overview) {
    throw new Error("Could not resolve the Anki shortcut after creating it.");
  }
  SteamClient.Apps.RunGame(overview.gameid, "", -1, 0);
}

// Decky plugins can't trigger a screenshot capture themselves — only read whatever Steam's own
// screenshot hotkey (Steam + R1) most recently took. "Take"/"Retake" means: the user presses that
// hotkey, then this pulls the result in. `GetLocalScreenshotPath` resolves the actual on-disk
// file, which is what AnkiConnect needs and what the backend reads to build a preview.
//
// GetLocalScreenshotPath's first argument must be the string "gameid" (`strGameID`), not the
// plain numeric Steam App ID (`nAppID`) — despite the ambient type declaration saying `number`.
// Every other Screenshots method (DeleteLocalScreenshot, ShowScreenshotInSystemViewer, ...) takes
// a string appId; passing the raw numeric App ID here gets rejected by Steam's native binding
// with "invalid arguments (arg 0)".
//
// Screenshot.strUrl (Steam's own internal URL for its screenshot manager UI) isn't usable as an
// <img> src here — the plugin's content isn't a trusted origin for it and it renders as a broken
// image. get_image_preview reads the file server-side and returns a data: URI instead, which
// needs no network fetch and so always renders regardless of origin/CSP.
async function useLastScreenshotPath(): Promise<string> {
  const screenshot = await SteamClient.Screenshots.GetLastScreenshotTaken();
  return SteamClient.Screenshots.GetLocalScreenshotPath(screenshot.strGameID as unknown as number, screenshot.hHandle);
}

// The Quick Access Menu remounts this component's tab content right after a Dropdown closes
// (Steam re-focuses the already-active tab, logging "Trying to change focus to already selected
// tab"). Module-scope cache + write-through survives that remount so selections don't reset.
const cache = {
  morph: "",
  definition: "",
  example: "",
  translation: "",
  originLanguage: "Japanese",
  targetLanguage: "English",
  image: "",
  imagePreviewUrl: "",
  audio: "",
  audioPreviewUrl: "",
  audioDuration: null as number | null,
  cropTop: 0,
  cropLeft: 0,
  cropRight: 0,
  cropBottom: 0,
  cropEnabled: false,
};

// Decky wraps every backend (Python) exception as a JS Error whose .message is just the generic
// "Python Exception" — the actual message (e.g. "Could not reach Google Translate: ...") only
// shows up as the last line of .stack, after the embedded Python traceback. Toasts built from
// String(e) alone were showing that useless generic text; this pulls out the real one.
function describeError(e: unknown): string {
  const stack = e && typeof e === "object" && "stack" in e ? String((e as { stack: unknown }).stack) : "";
  const lines = stack.trim().split("\n");
  const lastLine = lines[lines.length - 1] ?? "";
  const match = lastLine.match(/^\w*(?:Error|Exception):\s*(.+)$/);
  return match ? match[1] : String(e);
}

// One-level undo for auto-filled fields (Look Up -> Back, OCR -> Example, Translate ->
// Translation) — lets the user get back whatever was there before an auto-fill if they don't like
// the result, without a full history stack.
function useFieldBackup(value: string, setValue: (v: string) => void) {
  const [backup, setBackup] = useState<string | null>(null);
  return {
    hasBackup: backup !== null,
    stash: () => setBackup(value),
    undo: () => {
      if (backup !== null) {
        setValue(backup);
        setBackup(null);
      }
    },
    clear: () => setBackup(null),
  };
}

function Content() {
  const [morph, setMorphState] = useState(cache.morph);
  const [definition, setDefinitionState] = useState(cache.definition);
  const [example, setExampleState] = useState(cache.example);
  const [translation, setTranslationState] = useState(cache.translation);
  const [isRunningOcr, setIsRunningOcr] = useState(false);
  const [isTranslating, setIsTranslating] = useState(false);
  const [originLanguage, setOriginLanguageState] = useState(cache.originLanguage);
  const [targetLanguage, setTargetLanguageState] = useState(cache.targetLanguage);
  const [image, setImageState] = useState(cache.image);
  const [imagePreviewUrl, setImagePreviewUrlState] = useState(cache.imagePreviewUrl);
  const [audio, setAudioState] = useState(cache.audio);
  const [audioPreviewUrl, setAudioPreviewUrlState] = useState(cache.audioPreviewUrl);
  const [audioDuration, setAudioDurationState] = useState(cache.audioDuration);
  const [cropTop, setCropTopState] = useState(cache.cropTop);
  const [cropLeft, setCropLeftState] = useState(cache.cropLeft);
  const [cropRight, setCropRightState] = useState(cache.cropRight);
  const [cropBottom, setCropBottomState] = useState(cache.cropBottom);
  const [cropEnabled, setCropEnabledState] = useState(cache.cropEnabled);
  const [isCreatingDeck, setIsCreatingDeck] = useState(false);
  const [isAddingCard, setIsAddingCard] = useState(false);
  const [isLoadingAudio, setIsLoadingAudio] = useState(false);
  const [isLoadingScreenshot, setIsLoadingScreenshot] = useState(false);
  const [ankiStatus, setAnkiStatus] = useState<"unknown" | "connected" | "disconnected">("unknown");
  const [isCheckingStatus, setIsCheckingStatus] = useState(false);
  const [isLaunchingAnki, setIsLaunchingAnki] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isTogglingBackgroundAnki, setIsTogglingBackgroundAnki] = useState(false);
  const [isBackgroundAnkiActive, setIsBackgroundAnkiActive] = useState<boolean | null>(null);
  const [isLookingUpDictionary, setIsLookingUpDictionary] = useState(false);
  const [setupStatus, setSetupStatus] = useState<{
    anki_installed: boolean;
    anki_connect_reachable: boolean;
    deck_exists: boolean;
    sync_ok: boolean;
    sync_error: string;
  } | null>(null);
  const [isCheckingSetup, setIsCheckingSetup] = useState(false);

  const onCheckAnkiStatus = () => {
    setIsCheckingStatus(true);
    checkAnkiConnection()
      .then((connected) => setAnkiStatus(connected ? "connected" : "disconnected"))
      .catch((e) => {
        console.error("[AnkiCards] check_anki_connection failed:", e);
        setAnkiStatus("disconnected");
      })
      .finally(() => setIsCheckingStatus(false));
  };

  const onCheckBackgroundAnkiStatus = () => {
    checkBackgroundAnkiStatus()
      .then((active) => setIsBackgroundAnkiActive(active))
      .catch((e) => console.error("[AnkiCards] checkBackgroundAnkiStatus failed:", e));
  };

  const onCheckSetupStatus = () => {
    setIsCheckingSetup(true);
    checkSetupStatus()
      .then((status) => setSetupStatus(status))
      .catch((e) => console.error("[AnkiCards] checkSetupStatus failed:", e))
      .finally(() => setIsCheckingSetup(false));
  };

  // Refresh status whenever this tab (re)mounts, including the QAM's remount-on-dropdown-close
  // quirk — harmless here since it's just a read-only refresh, not something that resets input.
  useEffect(() => {
    onCheckAnkiStatus();
    onCheckBackgroundAnkiStatus();
    onCheckSetupStatus();
  }, []);

  const onOpenAnki = () => {
    setIsLaunchingAnki(true);
    launchAnki()
      .catch((e) => {
        console.error("[AnkiCards] launchAnki failed:", e);
        toaster.toast({ title: "Could not open Anki", body: describeError(e) });
      })
      .finally(() => {
        setIsLaunchingAnki(false);
        onCheckBackgroundAnkiStatus(); // launchAnki() stops the background service as a side effect
      });
  };

  // Manual controls for the optional always-on background service (scripts/setup-anki-service.sh)
  // — "Open Anki" already stops it automatically before launching (Anki only allows one running
  // instance at a time), so this lets the user turn it back on afterward instead of waiting for
  // the next Gaming Mode boot, or turn it off without also opening a window.
  const onStartBackgroundAnki = () => {
    setIsTogglingBackgroundAnki(true);
    startBackgroundAnki()
      .then(() => toaster.toast({ title: "Background Anki started", body: "AnkiConnect should be reachable shortly." }))
      .catch((e) => {
        console.error("[AnkiCards] startBackgroundAnki failed:", e);
        toaster.toast({ title: "Could not start background Anki", body: describeError(e) });
      })
      .finally(() => {
        setIsTogglingBackgroundAnki(false);
        onCheckBackgroundAnkiStatus();
      });
  };

  const onStopBackgroundAnki = () => {
    setIsTogglingBackgroundAnki(true);
    stopBackgroundAnki()
      .then(() => toaster.toast({ title: "Background Anki stopped", body: "" }))
      .catch((e) => {
        console.error("[AnkiCards] stopBackgroundAnki failed:", e);
        toaster.toast({ title: "Could not stop background Anki", body: describeError(e) });
      })
      .finally(() => {
        setIsTogglingBackgroundAnki(false);
        onCheckBackgroundAnkiStatus();
      });
  };

  const onSyncAnki = () => {
    setIsSyncing(true);
    syncAnki()
      .then(() => toaster.toast({ title: "Synced", body: "" }))
      .catch((e) => {
        console.error("[AnkiCards] syncAnki failed:", e);
        toaster.toast({ title: "Could not sync", body: describeError(e) });
      })
      .finally(() => setIsSyncing(false));
  };

  const onLookupDictionary = () => {
    setIsLookingUpDictionary(true);
    lookupDictionary(morph, originLanguage)
      .then((text) => {
        definitionUndo.stash();
        setDefinition(text);
        toaster.toast({
          title: text ? "Dictionary entry found" : "No entry found",
          body: text ? text.slice(0, 80) : `Jisho has no entry for "${morph}".`,
        });
      })
      .catch((e) => {
        console.error("[AnkiCards] lookupDictionary failed:", e);
        toaster.toast({ title: "Could not look up that word", body: describeError(e) });
      })
      .finally(() => setIsLookingUpDictionary(false));
  };

  const onTakeScreenshot = () => {
    setIsLoadingScreenshot(true);
    useLastScreenshotPath()
      .then((path) => {
        setImage(path);
        return getImagePreview(path);
      })
      .then((previewUrl) => setImagePreviewUrl(previewUrl))
      .catch((e) => {
        console.error("[AnkiCards] onTakeScreenshot failed:", e);
        toaster.toast({ title: "Could not get last screenshot", body: describeError(e) });
      })
      .finally(() => setIsLoadingScreenshot(false));
  };

  const onDeleteImage = () => {
    setImage("");
    setImagePreviewUrl("");
  };

  const onRunOcr = () => {
    console.log("[AnkiCards] onRunOcr: running OCR on", image, "lang", originLanguage);
    setIsRunningOcr(true);
    ocrScreenshot(
      image,
      originLanguage,
      cropEnabled ? cropTop : 0,
      cropEnabled ? cropLeft : 0,
      cropEnabled ? cropRight : 0,
      cropEnabled ? cropBottom : 0,
    )
      .then((text) => {
        console.log("[AnkiCards] onRunOcr: result:", JSON.stringify(text));
        exampleUndo.stash();
        setExample(text);
        toaster.toast({
          title: text ? "OCR done" : "No text found",
          body: text ? text.slice(0, 80) : "Tesseract didn't recognize any text in that image.",
        });
      })
      .catch((e) => {
        console.error("[AnkiCards] ocrScreenshot failed:", e);
        toaster.toast({ title: "Could not read text from image", body: describeError(e) });
      })
      .finally(() => setIsRunningOcr(false));
  };

  const onTranslate = () => {
    setIsTranslating(true);
    translateText(example, originLanguage, targetLanguage)
      .then((text) => {
        translationUndo.stash();
        setTranslation(text);
      })
      .catch((e) => {
        console.error("[AnkiCards] translateText failed:", e);
        toaster.toast({ title: "Could not translate", body: describeError(e) });
      })
      .finally(() => setIsTranslating(false));
  };

  // Each take produces its own temp file on the backend — discard whatever was taken before
  // (if anything) so Retake/Delete don't leak it under the system temp directory.
  const discardPreviousAudio = () => {
    if (!audio) {
      return Promise.resolve();
    }
    const previous = audio;
    return discardAudioFile(previous).catch((e) => {
      console.error("[AnkiCards] discardAudioFile failed:", e);
    });
  };

  const onTakeAudio = () => {
    setIsLoadingAudio(true);
    discardPreviousAudio()
      .then(() => takeLastRecordingAudio())
      .then(({ path, duration }) => {
        setAudio(path);
        setAudioDuration(duration);
        return getAudioPreview(path);
      })
      .then((previewUrl) => setAudioPreviewUrl(previewUrl))
      .catch((e) => {
        console.error("[AnkiCards] onTakeAudio failed:", e);
        toaster.toast({ title: "Could not get recording audio", body: describeError(e) });
      })
      .finally(() => setIsLoadingAudio(false));
  };

  const onDeleteAudio = () => {
    discardPreviousAudio();
    setAudio("");
    setAudioPreviewUrl("");
    setAudioDuration(null);
  };

  const setMorph = (value: string) => {
    cache.morph = value;
    setMorphState(value);
  };
  const setDefinition = (value: string) => {
    cache.definition = value;
    setDefinitionState(value);
  };
  const setExample = (value: string) => {
    cache.example = value;
    setExampleState(value);
  };
  const setTranslation = (value: string) => {
    cache.translation = value;
    setTranslationState(value);
  };
  const definitionUndo = useFieldBackup(definition, setDefinition);
  const exampleUndo = useFieldBackup(example, setExample);
  const translationUndo = useFieldBackup(translation, setTranslation);
  const setOriginLanguage = (value: string) => {
    cache.originLanguage = value;
    setOriginLanguageState(value);
  };
  const setTargetLanguage = (value: string) => {
    cache.targetLanguage = value;
    setTargetLanguageState(value);
  };
  const setImage = (value: string) => {
    cache.image = value;
    setImageState(value);
  };
  const setImagePreviewUrl = (value: string) => {
    cache.imagePreviewUrl = value;
    setImagePreviewUrlState(value);
  };
  const setAudio = (value: string) => {
    cache.audio = value;
    setAudioState(value);
  };
  const setAudioPreviewUrl = (value: string) => {
    cache.audioPreviewUrl = value;
    setAudioPreviewUrlState(value);
  };
  const setAudioDuration = (value: number | null) => {
    cache.audioDuration = value;
    setAudioDurationState(value);
  };
  const setCropTop = (value: number) => {
    cache.cropTop = value;
    setCropTopState(value);
  };
  const setCropLeft = (value: number) => {
    cache.cropLeft = value;
    setCropLeftState(value);
  };
  const setCropRight = (value: number) => {
    cache.cropRight = value;
    setCropRightState(value);
  };
  const setCropBottom = (value: number) => {
    cache.cropBottom = value;
    setCropBottomState(value);
  };
  const setCropEnabled = (value: boolean) => {
    cache.cropEnabled = value;
    setCropEnabledState(value);
  };

  const languageOptions: DropdownOption[] = LANGUAGE_OPTIONS.map((lang) => ({ data: lang, label: lang }));

  const extractOptionValue = (option: DropdownOption): string => {
    return option && typeof option === "object" && "data" in option ? option.data : (option as unknown as string);
  };

  const onOriginLanguageChange = (option: DropdownOption) => {
    setOriginLanguage(extractOptionValue(option));
  };

  const onTargetLanguageChange = (option: DropdownOption) => {
    setTargetLanguage(extractOptionValue(option));
  };

  const onCreateDeck = () => {
    setIsCreatingDeck(true);
    createDeck()
      .then((deckName) => {
        toaster.toast({ title: "Deck ready", body: deckName });
      })
      .catch((e) => {
        console.error("[AnkiCards] create_deck failed:", e);
        toaster.toast({ title: "Could not create deck", body: describeError(e) });
      })
      .finally(() => {
        setIsCreatingDeck(false);
        onCheckSetupStatus();
      });
  };

  const onAddCard = () => {
    setIsAddingCard(true);
    addNote(morph, definition, example, translation, image, audio)
      .then((noteId) => {
        toaster.toast({ title: "Card added", body: `Note ${noteId} added to ${DECK_NAME}` });
        setMorph("");
        setDefinition("");
        setExample("");
        setTranslation("");
        setImage("");
        setImagePreviewUrl("");
        setAudio("");
        setAudioPreviewUrl("");
        setAudioDuration(null);
        definitionUndo.clear();
        exampleUndo.clear();
        translationUndo.clear();
      })
      .catch((e) => {
        console.error("[AnkiCards] add_note failed:", e);
        toaster.toast({ title: "Could not add card", body: describeError(e) });
      })
      .finally(() => {
        setIsAddingCard(false);
        onCheckSetupStatus();
      });
  };

  const ankiStatusGlyph = ankiStatus === "connected" ? "✓" : ankiStatus === "disconnected" ? "✗" : "…";
  const ankiStatusColor = ankiStatus === "connected" ? "#2ecc71" : ankiStatus === "disconnected" ? "#e74c3c" : "#888";

  // Same circular colored-glyph badge as the AnkiConnect status row, reused for every other
  // readiness check so they all read the same way at a glance.
  const statusBadge = (glyph: string, color: string) => (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: "20px",
        height: "20px",
        borderRadius: "50%",
        backgroundColor: color,
        color: "white",
        fontSize: "12px",
        lineHeight: 1,
      }}
    >
      {glyph}
    </span>
  );

  // true/false/null (not yet known, or not checkable without AnkiConnect) -> glyph + color.
  const tristateBadge = (value: boolean | null) =>
    value === null ? statusBadge("…", "#888") : value ? statusBadge("✓", "#2ecc71") : statusBadge("✗", "#e74c3c");

  const compactButtonStyle = { flex: 1, minWidth: 0, fontSize: "12px", padding: "6px 4px" };

  const divider = (
    <PanelSectionRow>
      <div style={{ borderTop: "1px solid rgba(255, 255, 255, 0.15)", margin: "6px 0" }} />
    </PanelSectionRow>
  );

  return (
    <>
    <PanelSection title="Anki Status">
      <PanelSectionRow>
        <Field label="AnkiConnect">{statusBadge(ankiStatusGlyph, ankiStatusColor)}</Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Anki installed">
          {tristateBadge(setupStatus ? setupStatus.anki_installed : null)}
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Deck created">
          {tristateBadge(!setupStatus || !setupStatus.anki_connect_reachable ? null : setupStatus.deck_exists)}
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Sync">
          {tristateBadge(!setupStatus || !setupStatus.anki_connect_reachable ? null : setupStatus.sync_ok)}
        </Field>
      </PanelSectionRow>
      {setupStatus && setupStatus.anki_connect_reachable && !setupStatus.sync_ok && setupStatus.sync_error && (
        <PanelSectionRow>
          <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
            {setupStatus.sync_error}
          </div>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isCheckingStatus || isCheckingSetup}
          onClick={() => {
            onCheckAnkiStatus();
            onCheckSetupStatus();
          }}
        >
          Check Status
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Anki App">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isLaunchingAnki || isBackgroundAnkiActive === true}
          onClick={onOpenAnki}
        >
          Open Anki
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          Opens Anki in a second window, switching away from your game. Disabled while the
          background service below is running — stop it first (Anki only allows one instance).
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isSyncing}
          onClick={onSyncAnki}
        >
          Sync Now
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          Forces an AnkiWeb sync (same as Anki's own sync button) — requires AnkiConnect
          reachable and the profile already logged into AnkiWeb.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            disabled={isTogglingBackgroundAnki || isBackgroundAnkiActive === true}
            onClick={onStartBackgroundAnki}
            style={compactButtonStyle}
          >
            Start Background
          </DialogButton>
          <DialogButton
            disabled={isTogglingBackgroundAnki || isBackgroundAnkiActive === false}
            onClick={onStopBackgroundAnki}
            style={compactButtonStyle}
          >
            Stop Background
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          Controls the optional always-on background service (set up once via
          scripts/setup-anki-service.sh) — keeps AnkiConnect reachable without opening a window.
        </div>
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="New Card">
      {!setupStatus?.deck_exists && (
        <>
          <PanelSectionRow>
            <Field label="Deck">{DECK_NAME}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={isCreatingDeck}
              onClick={onCreateDeck}
            >
              Create Deck
            </ButtonItem>
          </PanelSectionRow>
          {divider}
        </>
      )}
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "11px", opacity: 0.6, marginBottom: "2px" }}>From</div>
            <Dropdown
              rgOptions={languageOptions}
              selectedOption={originLanguage}
              onChange={onOriginLanguageChange}
            />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "11px", opacity: 0.6, marginBottom: "2px" }}>To</div>
            <Dropdown
              rgOptions={languageOptions}
              selectedOption={targetLanguage}
              onChange={onTargetLanguageChange}
            />
          </div>
        </Focusable>
      </PanelSectionRow>
      {divider}
      <PanelSectionRow>
        <TextField
          label="Front *"
          value={morph}
          onChange={(e) => setMorph(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          To copy text between fields: long-press the touchpad to select words, then press Copy.
          To paste, long-press again and press Paste.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            disabled={!morph || isLookingUpDictionary}
            onClick={onLookupDictionary}
            style={compactButtonStyle}
          >
            Look Up ({originLanguage === "Japanese" ? "Jisho" : "Wiktionary"})
          </DialogButton>
          <DialogButton
            disabled={!definitionUndo.hasBackup}
            onClick={definitionUndo.undo}
            style={compactButtonStyle}
          >
            Undo
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
      {divider}
      <PanelSectionRow>
        <div style={{ fontSize: "14px", marginBottom: "4px" }}>Back</div>
        <textarea
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
          rows={5}
          style={{
            width: "100%",
            minHeight: "110px",
            resize: "vertical",
            fontFamily: "inherit",
            fontSize: "13px",
            lineHeight: 1.4,
            padding: "8px",
            boxSizing: "border-box",
            background: "rgba(255,255,255,0.05)",
            color: "inherit",
            border: "1px solid rgba(255,255,255,0.2)",
            borderRadius: "4px",
          }}
        />
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Image & Example">
      {divider}
      {imagePreviewUrl && (
        <PanelSectionRow>
          <div style={{ position: "relative" }}>
            <img
              src={imagePreviewUrl}
              style={{ width: "100%", borderRadius: "4px", display: "block" }}
            />
            {cropEnabled && (
              <>
                <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: `${cropTop}%`, background: "rgba(0, 0, 0, 0.65)" }} />
                <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: `${cropBottom}%`, background: "rgba(0, 0, 0, 0.65)" }} />
                <div style={{ position: "absolute", top: `${cropTop}%`, bottom: `${cropBottom}%`, left: 0, width: `${cropLeft}%`, background: "rgba(0, 0, 0, 0.65)" }} />
                <div style={{ position: "absolute", top: `${cropTop}%`, bottom: `${cropBottom}%`, right: 0, width: `${cropRight}%`, background: "rgba(0, 0, 0, 0.65)" }} />
              </>
            )}
          </div>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            disabled={isLoadingScreenshot}
            onClick={onTakeScreenshot}
            style={compactButtonStyle}
          >
            {imagePreviewUrl ? "Retake" : "Take"}
          </DialogButton>
          <DialogButton
            disabled={!imagePreviewUrl}
            onClick={onDeleteImage}
            style={compactButtonStyle}
          >
            Delete
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
      {!imagePreviewUrl && (
        <PanelSectionRow>
          <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
            Uses your last Steam screenshot (Steam + R1) — take one, then tap Take.
          </div>
        </PanelSectionRow>
      )}
      {imagePreviewUrl && (
        <PanelSectionRow>
          <ToggleField
            label="Crop before OCR"
            checked={cropEnabled}
            onChange={setCropEnabled}
          />
        </PanelSectionRow>
      )}
      {imagePreviewUrl && cropEnabled && (
        <>
          <PanelSectionRow>
            <SliderField
              label="Crop top"
              value={cropTop}
              min={0}
              max={90}
              step={5}
              minimumDpadGranularity={5}
              valueSuffix="%"
              onChange={setCropTop}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Crop left"
              value={cropLeft}
              min={0}
              max={90}
              step={5}
              minimumDpadGranularity={5}
              valueSuffix="%"
              onChange={setCropLeft}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Crop right"
              value={cropRight}
              min={0}
              max={90}
              step={5}
              minimumDpadGranularity={5}
              valueSuffix="%"
              onChange={setCropRight}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField
              label="Crop bottom"
              value={cropBottom}
              min={0}
              max={90}
              step={5}
              minimumDpadGranularity={5}
              valueSuffix="%"
              onChange={setCropBottom}
            />
          </PanelSectionRow>
        </>
      )}
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            disabled={!image || isRunningOcr}
            onClick={onRunOcr}
            style={compactButtonStyle}
          >
            OCR from Image
          </DialogButton>
          <DialogButton
            disabled={!exampleUndo.hasBackup}
            onClick={exampleUndo.undo}
            style={compactButtonStyle}
          >
            Undo
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          OCR text may not be fully accurate — check it before adding the card.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "14px", marginBottom: "4px" }}>Example</div>
        <textarea
          value={example}
          onChange={(e) => setExample(e.target.value)}
          rows={5}
          style={{
            width: "100%",
            minHeight: "110px",
            resize: "vertical",
            fontFamily: "inherit",
            fontSize: "13px",
            lineHeight: 1.4,
            padding: "8px",
            boxSizing: "border-box",
            background: "rgba(255,255,255,0.05)",
            color: "inherit",
            border: "1px solid rgba(255,255,255,0.2)",
            borderRadius: "4px",
          }}
        />
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Translation">
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            disabled={!example || isTranslating}
            onClick={onTranslate}
            style={compactButtonStyle}
          >
            Translate
          </DialogButton>
          <DialogButton
            disabled={!translationUndo.hasBackup}
            onClick={translationUndo.undo}
            style={compactButtonStyle}
          >
            Undo
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "14px", marginBottom: "4px" }}>Translation</div>
        <textarea
          value={translation}
          onChange={(e) => setTranslation(e.target.value)}
          rows={5}
          style={{
            width: "100%",
            minHeight: "110px",
            resize: "vertical",
            fontFamily: "inherit",
            fontSize: "13px",
            lineHeight: 1.4,
            padding: "8px",
            boxSizing: "border-box",
            background: "rgba(255,255,255,0.05)",
            color: "inherit",
            border: "1px solid rgba(255,255,255,0.2)",
            borderRadius: "4px",
          }}
        />
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Audio">
      {divider}
      {audioPreviewUrl && (
        <PanelSectionRow>
          <audio
            controls
            src={audioPreviewUrl}
            style={{ width: "100%", display: "block" }}
          />
        </PanelSectionRow>
      )}
      {audio && (
        <PanelSectionRow>
          <Field label="Length">{audioDuration !== null ? `${audioDuration.toFixed(1)}s` : "unknown"}</Field>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px" }}>
          <DialogButton
            disabled={isLoadingAudio}
            onClick={onTakeAudio}
            style={compactButtonStyle}
          >
            {audio ? "Retake" : "Take"}
          </DialogButton>
          <DialogButton
            disabled={!audio}
            onClick={onDeleteAudio}
            style={compactButtonStyle}
          >
            Delete
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
      {!audio && (
        <PanelSectionRow>
          <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
            Needs "Record In Background" on (Settings &gt; System &gt; Recording) and a saved
            clip — save one, then tap Take.
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
    <PanelSection title="Add Card">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!morph || isAddingCard}
          onClick={onAddCard}
        >
          Add Card
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
    </>
  );
};

export default definePlugin(() => {
  console.log("Anki Cards plugin initializing, this is called once on frontend startup")

  return {
    // The name shown in various decky menus
    name: "Anki Cards",
    // The element displayed at the top of your plugin's menu
    titleView: <div className={staticClasses.Title}>Anki Cards</div>,
    // The content of your plugin's menu
    content: <Content />,
    // The icon displayed in the plugin list
    icon: <FaShip />,
    // The function triggered when your plugin unloads
    onDismount() {
      console.log("Unloading")
    },
  };
});
