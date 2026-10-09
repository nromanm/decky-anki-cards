import {
  ButtonItem,
  DialogButton,
  Dropdown,
  DropdownOption,
  Field,
  Focusable,
  PanelSection,
  PanelSectionRow,
  TextField,
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
const ocrScreenshot = callable<[path: string, language: string], string>("ocr_screenshot");
const createDeck = callable<[], string>("create_deck");
const addNote = callable<[morph: string, definition: string, example: string, translation: string, image: string, audio: string], number>("add_note");

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
  const appId = await ensureAnkiShortcut();
  // Give Steam a moment to register the shortcut's exe/launch options before running it.
  await new Promise((resolve) => setTimeout(resolve, 500));
  const overview = window.appStore.GetAppOverviewByAppID(appId);
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
};

function Content() {
  const [morph, setMorphState] = useState(cache.morph);
  const [definition, setDefinitionState] = useState(cache.definition);
  const [example, setExampleState] = useState(cache.example);
  const [translation, setTranslationState] = useState(cache.translation);
  const [isRunningOcr, setIsRunningOcr] = useState(false);
  const [originLanguage, setOriginLanguageState] = useState(cache.originLanguage);
  const [targetLanguage, setTargetLanguageState] = useState(cache.targetLanguage);
  const [image, setImageState] = useState(cache.image);
  const [imagePreviewUrl, setImagePreviewUrlState] = useState(cache.imagePreviewUrl);
  const [audio, setAudioState] = useState(cache.audio);
  const [audioPreviewUrl, setAudioPreviewUrlState] = useState(cache.audioPreviewUrl);
  const [audioDuration, setAudioDurationState] = useState(cache.audioDuration);
  const [isCreatingDeck, setIsCreatingDeck] = useState(false);
  const [isAddingCard, setIsAddingCard] = useState(false);
  const [isLoadingAudio, setIsLoadingAudio] = useState(false);
  const [isLoadingScreenshot, setIsLoadingScreenshot] = useState(false);
  const [ankiStatus, setAnkiStatus] = useState<"unknown" | "connected" | "disconnected">("unknown");
  const [isCheckingStatus, setIsCheckingStatus] = useState(false);
  const [isLaunchingAnki, setIsLaunchingAnki] = useState(false);

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

  // Refresh status whenever this tab (re)mounts, including the QAM's remount-on-dropdown-close
  // quirk — harmless here since it's just a read-only refresh, not something that resets input.
  useEffect(() => {
    onCheckAnkiStatus();
  }, []);

  const onOpenAnki = () => {
    setIsLaunchingAnki(true);
    launchAnki()
      .catch((e) => {
        console.error("[AnkiCards] launchAnki failed:", e);
        toaster.toast({ title: "Could not open Anki", body: String(e) });
      })
      .finally(() => setIsLaunchingAnki(false));
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
        toaster.toast({ title: "Could not get last screenshot", body: String(e) });
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
    ocrScreenshot(image, originLanguage)
      .then((text) => {
        console.log("[AnkiCards] onRunOcr: result:", JSON.stringify(text));
        setExample(text);
        toaster.toast({
          title: text ? "OCR done" : "No text found",
          body: text ? text.slice(0, 80) : "Tesseract didn't recognize any text in that image.",
        });
      })
      .catch((e) => {
        console.error("[AnkiCards] ocrScreenshot failed:", e);
        toaster.toast({ title: "Could not read text from image", body: String(e) });
      })
      .finally(() => setIsRunningOcr(false));
  };

  // Placeholder — real translation (Example -> Translation, using From/To) is a future feature.
  const onTranslate = () => {
    toaster.toast({ title: "Not implemented yet", body: "Auto-translate is coming in a future update." });
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
        toaster.toast({ title: "Could not get recording audio", body: String(e) });
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
        toaster.toast({ title: "Could not create deck", body: String(e) });
      })
      .finally(() => setIsCreatingDeck(false));
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
      })
      .catch((e) => {
        console.error("[AnkiCards] add_note failed:", e);
        toaster.toast({ title: "Could not add card", body: String(e) });
      })
      .finally(() => setIsAddingCard(false));
  };

  const ankiStatusGlyph = ankiStatus === "connected" ? "✓" : ankiStatus === "disconnected" ? "✗" : "…";
  const ankiStatusColor = ankiStatus === "connected" ? "#2ecc71" : ankiStatus === "disconnected" ? "#e74c3c" : "#888";

  const compactButtonStyle = { flex: 1, minWidth: 0, fontSize: "12px", padding: "6px 4px" };

  return (
    <>
    <PanelSection title="Anki Status">
      <PanelSectionRow>
        <Field label="AnkiConnect">
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: "20px",
              height: "20px",
              borderRadius: "50%",
              backgroundColor: ankiStatusColor,
              color: "white",
              fontSize: "12px",
              lineHeight: 1,
            }}
          >
            {ankiStatusGlyph}
          </span>
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isCheckingStatus}
          onClick={onCheckAnkiStatus}
        >
          Check Status
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Anki App">
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isLaunchingAnki}
          onClick={onOpenAnki}
        >
          Open Anki
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          Opens Anki in a second window, switching away from your game.
        </div>
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="New Card">
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
      <PanelSectionRow>
        <TextField
          label="Front *"
          value={morph}
          onChange={(e) => setMorph(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Back"
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
        />
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Image & Example">
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
      {imagePreviewUrl && (
        <PanelSectionRow>
          <img
            src={imagePreviewUrl}
            style={{ width: "100%", borderRadius: "4px", display: "block" }}
          />
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
      <PanelSectionRow>
        <DialogButton
          disabled={!image || isRunningOcr}
          onClick={onRunOcr}
          style={compactButtonStyle}
        >
          OCR from Image
        </DialogButton>
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: "11px", opacity: 0.6, padding: "2px 0" }}>
          OCR text may not be fully accurate — check it before adding the card.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Example"
          value={example}
          onChange={(e) => setExample(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <DialogButton
          disabled
          onClick={onTranslate}
          style={compactButtonStyle}
        >
          Translate (coming soon)
        </DialogButton>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Translation"
          value={translation}
          onChange={(e) => setTranslation(e.target.value)}
        />
      </PanelSectionRow>
    </PanelSection>
    <PanelSection title="Audio">
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
