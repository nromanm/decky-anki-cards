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
import { useState } from "react";
import { FaShip } from "react-icons/fa";

const createDeckForLanguage = callable<[language: string], string>("create_deck_for_language");
const addNote = callable<[language: string, morph: string, definition: string, image: string, audio: string], number>("add_note");

// Single source of truth for the language preset list. Add/remove a language here only — no
// backend change needed, the backend just formats whatever string it's given.
const LANGUAGES = [
  "Japanese", "Spanish", "French", "German", "Korean",
  "Mandarin Chinese", "Italian", "Portuguese", "Russian", "Arabic",
];

const deckNameForLanguage = (language: string) => `Decky Anki Plugin Deck (${language})`;

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

  return (
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
