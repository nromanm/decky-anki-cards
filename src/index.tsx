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

// The Quick Access Menu remounts this component's tab content right after a Dropdown closes
// (Steam re-focuses the already-active tab, logging "Trying to change focus to already selected
// tab"). Module-scope cache + write-through survives that remount so selections don't reset.
const cache = {
  language: "",
  morph: "",
  definition: "",
  image: "",
  audio: "",
};

function Content() {
  const [language, setLanguageState] = useState(cache.language);
  const [morph, setMorphState] = useState(cache.morph);
  const [definition, setDefinitionState] = useState(cache.definition);
  const [image, setImageState] = useState(cache.image);
  const [audio, setAudioState] = useState(cache.audio);
  const [isCreatingDeck, setIsCreatingDeck] = useState(false);
  const [isAddingCard, setIsAddingCard] = useState(false);
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
        <TextField
          label="Image (file path or URL)"
          value={image}
          onChange={(e) => setImage(e.target.value)}
        />
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
