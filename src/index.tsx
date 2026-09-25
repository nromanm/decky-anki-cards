import {
  ButtonItem,
  DropdownItem,
  DropdownOption,
  Field,
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
const checkAnkiServiceStatus = callable<[], string>("check_anki_service_status");
const getImagePreview = callable<[path: string], string>("get_image_preview");
const createDeckForLanguage = callable<[language: string], string>("create_deck_for_language");
const addNote = callable<[language: string, morph: string, definition: string, image: string, audio: string], number>("add_note");

// Single source of truth for the language preset list. Add/remove a language here only — no
// backend change needed, the backend just formats whatever string it's given.
const LANGUAGES = [
  "Japanese", "Spanish", "French", "German", "Korean",
  "Mandarin Chinese", "Italian", "Portuguese", "Russian", "Arabic",
];

const deckNameForLanguage = (language: string) => `Decky Anki Plugin Deck (${language})`;

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
  language: "",
  morph: "",
  definition: "",
  image: "",
  imagePreviewUrl: "",
  audio: "",
};

function Content() {
  const [language, setLanguageState] = useState(cache.language);
  const [morph, setMorphState] = useState(cache.morph);
  const [definition, setDefinitionState] = useState(cache.definition);
  const [image, setImageState] = useState(cache.image);
  const [imagePreviewUrl, setImagePreviewUrlState] = useState(cache.imagePreviewUrl);
  const [audio, setAudioState] = useState(cache.audio);
  const [isCreatingDeck, setIsCreatingDeck] = useState(false);
  const [isAddingCard, setIsAddingCard] = useState(false);
  const [isLoadingScreenshot, setIsLoadingScreenshot] = useState(false);
  const [ankiStatus, setAnkiStatus] = useState<"unknown" | "connected" | "disconnected">("unknown");
  const [serviceStatus, setServiceStatus] = useState<string>("unknown");
  const [isCheckingStatus, setIsCheckingStatus] = useState(false);
  const [isLaunchingAnki, setIsLaunchingAnki] = useState(false);

  const onCheckAnkiStatus = () => {
    setIsCheckingStatus(true);
    Promise.all([
      checkAnkiConnection()
        .then((connected) => setAnkiStatus(connected ? "connected" : "disconnected"))
        .catch((e) => {
          console.error("[AnkiCards] check_anki_connection failed:", e);
          setAnkiStatus("disconnected");
        }),
      checkAnkiServiceStatus()
        .then((status) => setServiceStatus(status))
        .catch((e) => {
          console.error("[AnkiCards] check_anki_service_status failed:", e);
          setServiceStatus("unknown");
        }),
    ]).finally(() => setIsCheckingStatus(false));
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

  const setLanguage = (value: string) => {
    cache.language = value;
    setLanguageState(value);
  };
  const setMorph = (value: string) => {
    cache.morph = value;
    setMorphState(value);
  };
  const setDefinition = (value: string) => {
    cache.definition = value;
    setDefinitionState(value);
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

  const languageOptions: DropdownOption[] = LANGUAGES.map((lang) => ({ data: lang, label: lang }));

  const extractOptionValue = (option: DropdownOption): string => {
    return option && typeof option === "object" && "data" in option ? option.data : (option as unknown as string);
  };

  const onLanguageChange = (option: DropdownOption) => {
    setLanguage(extractOptionValue(option));
  };

  const onCreateDeck = () => {
    setIsCreatingDeck(true);
    createDeckForLanguage(language)
      .then((deckName) => {
        toaster.toast({ title: "Deck ready", body: deckName });
      })
      .catch((e) => {
        console.error("[AnkiCards] create_deck_for_language failed:", e);
        toaster.toast({ title: "Could not create deck", body: String(e) });
      })
      .finally(() => setIsCreatingDeck(false));
  };

  const onAddCard = () => {
    setIsAddingCard(true);
    addNote(language, morph, definition, image, audio)
      .then((noteId) => {
        toaster.toast({ title: "Card added", body: `Note ${noteId} added to ${deckNameForLanguage(language)}` });
        setMorph("");
        setDefinition("");
        setImage("");
        setImagePreviewUrl("");
        setAudio("");
      })
      .catch((e) => {
        console.error("[AnkiCards] add_note failed:", e);
        toaster.toast({ title: "Could not add card", body: String(e) });
      })
      .finally(() => setIsAddingCard(false));
  };

  const ankiStatusGlyph = ankiStatus === "connected" ? "✓" : ankiStatus === "disconnected" ? "✗" : "…";
  const ankiStatusColor = ankiStatus === "connected" ? "#2ecc71" : ankiStatus === "disconnected" ? "#e74c3c" : "#888";

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
        <Field label="Background service">{serviceStatus}</Field>
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
    </PanelSection>
    <PanelSection title="New Card">
      <PanelSectionRow>
        <DropdownItem
          label="Language"
          rgOptions={languageOptions}
          selectedOption={language}
          onChange={onLanguageChange}
          strDefaultLabel="Select a language"
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Deck">{language ? deckNameForLanguage(language) : "(select a language)"}</Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!language || isCreatingDeck}
          onClick={onCreateDeck}
        >
          Create Deck
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Morph *"
          value={morph}
          onChange={(e) => setMorph(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Definition / Translation"
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        {imagePreviewUrl ? (
          <img
            src={imagePreviewUrl}
            style={{ width: "100%", borderRadius: "4px", display: "block" }}
          />
        ) : (
          <Field label="Image">No image selected</Field>
        )}
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={isLoadingScreenshot}
          onClick={onTakeScreenshot}
        >
          {imagePreviewUrl ? "Retake (use last screenshot)" : "Take (use last screenshot)"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!imagePreviewUrl}
          onClick={onDeleteImage}
        >
          Delete Image
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Audio (file path or URL)"
          value={audio}
          onChange={(e) => setAudio(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!language || !morph || isAddingCard}
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
