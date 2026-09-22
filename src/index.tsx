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
import { useEffect, useRef, useState } from "react";
import { FaShip } from "react-icons/fa";

interface ModelField {
  name: string;
  description: string;
  required: boolean;
}

const getDecks = callable<[], string[]>("get_decks");
const getDeckModels = callable<[deck_name: string], string[]>("get_deck_models");
const getModelFields = callable<[model_name: string], ModelField[]>("get_model_fields");

// The Quick Access Menu remounts this component's tab content right after a Dropdown closes
// (Steam re-focuses the already-active tab, logging "Trying to change focus to already selected
// tab"). Module-scope cache + write-through survives that remount so selections don't reset.
const cache = {
  deckName: "",
  modelName: "",
  fieldValues: {} as Record<string, string>,
};

function Content() {
  const [decks, setDecks] = useState<string[]>([]);
  const [deckName, setDeckNameState] = useState(cache.deckName);
  const [models, setModels] = useState<string[]>([]);
  const [modelName, setModelNameState] = useState(cache.modelName);
  const [modelFields, setModelFields] = useState<ModelField[]>([]);
  const [fieldValues, setFieldValuesState] = useState<Record<string, string>>(cache.fieldValues);

  const setDeckName = (value: string) => {
    cache.deckName = value;
    setDeckNameState(value);
  };
  const setModelName = (value: string) => {
    cache.modelName = value;
    setModelNameState(value);
  };
  const setFieldValues = (value: Record<string, string> | ((prev: Record<string, string>) => Record<string, string>)) => {
    setFieldValuesState((prev) => {
      const next = typeof value === "function" ? value(prev) : value;
      cache.fieldValues = next;
      return next;
    });
  };

  useEffect(() => {
    getDecks()
      .then((result) => {
        console.log("[AnkiCards] get_decks resolved:", result);
        setDecks(result);
      })
      .catch((e) => {
        console.error("[AnkiCards] get_decks failed:", e);
        toaster.toast({ title: "Could not load decks", body: String(e) });
      });
  }, []);

  // First run of this effect can be a genuine mount (deckName === "") or a QAM-triggered remount
  // rehydrating a previously picked deck from `cache` — only reset downstream state on a real
  // user-driven change (every run after the first).
  const isDeckEffectFirstRun = useRef(true);
  useEffect(() => {
    console.log("[AnkiCards] deckName state is now:", deckName);
    const isFirstRun = isDeckEffectFirstRun.current;
    isDeckEffectFirstRun.current = false;
    if (!isFirstRun) {
      setModelName("");
      setModelFields([]);
      setFieldValues({});
    }
    if (!deckName) {
      setModels([]);
      return;
    }
    getDeckModels(deckName)
      .then((result) => {
        console.log(`[AnkiCards] get_deck_models(${deckName}) resolved:`, result);
        setModels(result);
      })
      .catch((e) => {
        console.error(`[AnkiCards] get_deck_models(${deckName}) failed:`, e);
        toaster.toast({ title: "Could not load note types for deck", body: String(e) });
      });
  }, [deckName]);

  const isModelEffectFirstRun = useRef(true);
  useEffect(() => {
    console.log("[AnkiCards] modelName state is now:", modelName);
    const isFirstRun = isModelEffectFirstRun.current;
    isModelEffectFirstRun.current = false;
    if (!modelName) {
      setModelFields([]);
      if (!isFirstRun) {
        setFieldValues({});
      }
      return;
    }
    getModelFields(modelName)
      .then((result) => {
        console.log(`[AnkiCards] get_model_fields(${modelName}) resolved:`, result);
        setModelFields(result);
        setFieldValues((prev) => {
          if (isFirstRun) {
            // Rehydrating after a remount: keep whatever was already typed, just backfill
            // any field this model has that the cache doesn't know about yet.
            const merged = { ...prev };
            for (const field of result) {
              if (!(field.name in merged)) {
                merged[field.name] = "";
              }
            }
            return merged;
          }
          const initialValues: Record<string, string> = {};
          for (const field of result) {
            initialValues[field.name] = "";
          }
          return initialValues;
        });
      })
      .catch((e) => {
        console.error(`[AnkiCards] get_model_fields(${modelName}) failed:`, e);
        toaster.toast({ title: "Could not load fields", body: String(e) });
      });
  }, [modelName]);

  const deckOptions: DropdownOption[] = decks.map((deck) => ({ data: deck, label: deck }));
  const modelOptions: DropdownOption[] = models.map((model) => ({ data: model, label: model }));

  const extractOptionValue = (option: DropdownOption): string => {
    return option && typeof option === "object" && "data" in option ? option.data : (option as unknown as string);
  };

  const onDeckChange = (option: DropdownOption) => {
    console.log("[AnkiCards] Deck onChange option:", option);
    setDeckName(extractOptionValue(option));
  };

  const onModelChange = (option: DropdownOption) => {
    console.log("[AnkiCards] Note Type onChange option:", option);
    setModelName(extractOptionValue(option));
  };

  const onFieldChange = (fieldName: string, value: string) => {
    setFieldValues((prev) => ({ ...prev, [fieldName]: value }));
  };

  const onAddCard = () => {
    // TODO: call AnkiConnect "addNote" with { deckName, modelName, fields: fieldValues } once verified
    toaster.toast({
      title: "Card ready",
      body: `${deckName || "(no deck)"} / ${modelName || "(no note type)"}: ${JSON.stringify(fieldValues)}`
    });
  };

  return (
    <PanelSection title="New Card">
      <PanelSectionRow>
        <DropdownItem
          label="Deck"
          rgOptions={deckOptions}
          selectedOption={deckName}
          onChange={onDeckChange}
          strDefaultLabel="Select a deck"
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Note Type"
          rgOptions={modelOptions}
          selectedOption={modelName}
          onChange={onModelChange}
          strDefaultLabel={deckName ? "Select a note type" : "Select a deck first"}
          disabled={!deckName}
        />
      </PanelSectionRow>
      {modelFields.map((field) => (
        <PanelSectionRow key={field.name}>
          <TextField
            label={`${field.name}${field.required ? " *" : ""} (${modelName})`}
            description={field.description || undefined}
            value={fieldValues[field.name]}
            onChange={(e) => onFieldChange(field.name, e.target.value)}
          />
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={onAddCard}
        >
          Add Card
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Debug: deckName / modelName">{`${deckName || "(none)"} / ${modelName || "(none)"}`}</Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Debug: fieldValues">{JSON.stringify(fieldValues)}</Field>
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
