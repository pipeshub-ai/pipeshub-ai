// Public entry point for pipeshub-slides. Re-exports the high-level Deck
// primitives and the theme catalogue so user code can:
//
//     import { Deck, THEMES } from "pipeshub-slides";
//
// See `deck.ts` for the full API surface.
export { Deck } from "./deck.js";
export { THEMES, resolveTheme } from "./themes.js";
export { LAYOUTS, MARGINS } from "./layout.js";
