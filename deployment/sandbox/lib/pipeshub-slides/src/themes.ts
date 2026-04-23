// Curated palettes + type pairings the design skill references by name.
// Every color is a 6-character hex WITHOUT a leading '#' (pptxgenjs
// corrupts the file on '#' prefixes). Do not change that invariant.

export interface Theme {
  readonly name: string;
  readonly palette: {
    readonly primary: string;
    readonly secondary: string;
    readonly accent: string;
    readonly background: string;
    readonly surface: string;
    readonly textPrimary: string;
    readonly textMuted: string;
    readonly chart: readonly string[];
  };
  readonly type: {
    readonly headerFont: string;
    readonly bodyFont: string;
    readonly titleSize: number;
    readonly headerSize: number;
    readonly bodySize: number;
    readonly captionSize: number;
  };
}

const TYPE_CORPORATE = {
  headerFont: "Cambria",
  bodyFont: "Calibri",
  titleSize: 40,
  headerSize: 22,
  bodySize: 16,
  captionSize: 11,
} as const;

const TYPE_EDITORIAL = {
  headerFont: "Georgia",
  bodyFont: "Calibri",
  titleSize: 40,
  headerSize: 22,
  bodySize: 16,
  captionSize: 11,
} as const;

const TYPE_BOLD = {
  headerFont: "Arial Black",
  bodyFont: "Arial",
  titleSize: 44,
  headerSize: 22,
  bodySize: 16,
  captionSize: 11,
} as const;

export const THEMES: Readonly<Record<string, Theme>> = Object.freeze({
  midnightExecutive: {
    name: "Midnight Executive",
    palette: {
      primary: "1E2761",
      secondary: "CADCFC",
      accent: "F6AE2D",
      background: "FFFFFF",
      surface: "F8FAFC",
      textPrimary: "0F172A",
      textMuted: "64748B",
      chart: ["1E2761", "3C5CCB", "6B8AFF", "CADCFC", "F6AE2D"],
    },
    type: TYPE_CORPORATE,
  },
  forestMoss: {
    name: "Forest & Moss",
    palette: {
      primary: "2C5F2D",
      secondary: "97BC62",
      accent: "B85042",
      background: "F5F5F5",
      surface: "FFFFFF",
      textPrimary: "1F2937",
      textMuted: "6B7280",
      chart: ["2C5F2D", "4D8A3C", "97BC62", "DDE5B6", "B85042"],
    },
    type: TYPE_EDITORIAL,
  },
  coralEnergy: {
    name: "Coral Energy",
    palette: {
      primary: "F96167",
      secondary: "F9E795",
      accent: "2F3C7E",
      background: "FFFFFF",
      surface: "FFF4E6",
      textPrimary: "1A1A2E",
      textMuted: "4B5563",
      chart: ["F96167", "F9965B", "F9E795", "2F3C7E", "5E72EB"],
    },
    type: TYPE_BOLD,
  },
  warmTerracotta: {
    name: "Warm Terracotta",
    palette: {
      primary: "B85042",
      secondary: "E7E8D1",
      accent: "A7BEAE",
      background: "FFFBF5",
      surface: "F0EDE5",
      textPrimary: "2F2A26",
      textMuted: "6B6B6B",
      chart: ["B85042", "D08A71", "E7E8D1", "A7BEAE", "6C7A6A"],
    },
    type: TYPE_EDITORIAL,
  },
  oceanGradient: {
    name: "Ocean Gradient",
    palette: {
      primary: "065A82",
      secondary: "1C7293",
      accent: "9EB3C2",
      background: "FFFFFF",
      surface: "F1F7FB",
      textPrimary: "21295C",
      textMuted: "5A6A7E",
      chart: ["065A82", "1C7293", "3498DB", "9EB3C2", "21295C"],
    },
    type: TYPE_CORPORATE,
  },
  charcoalMinimal: {
    name: "Charcoal Minimal",
    palette: {
      primary: "36454F",
      secondary: "F2F2F2",
      accent: "E07A5F",
      background: "FFFFFF",
      surface: "F7F7F7",
      textPrimary: "212121",
      textMuted: "616161",
      chart: ["36454F", "606C76", "9EA7AD", "E07A5F", "81B29A"],
    },
    type: TYPE_CORPORATE,
  },
  tealTrust: {
    name: "Teal Trust",
    palette: {
      primary: "028090",
      secondary: "00A896",
      accent: "F4A261",
      background: "FFFFFF",
      surface: "E8F6F5",
      textPrimary: "05313A",
      textMuted: "4F6D75",
      chart: ["028090", "00A896", "02C39A", "F4A261", "E76F51"],
    },
    type: TYPE_CORPORATE,
  },
  berryCream: {
    name: "Berry & Cream",
    palette: {
      primary: "6D2E46",
      secondary: "A26769",
      accent: "ECE2D0",
      background: "FCF7F3",
      surface: "F5EFE7",
      textPrimary: "2A1418",
      textMuted: "6B5458",
      chart: ["6D2E46", "A26769", "D5B9B2", "ECE2D0", "4E342E"],
    },
    type: TYPE_EDITORIAL,
  },
  sageCalm: {
    name: "Sage Calm",
    palette: {
      primary: "50808E",
      secondary: "84B59F",
      accent: "D9B384",
      background: "FFFFFF",
      surface: "F0F5F2",
      textPrimary: "1E2A2E",
      textMuted: "566B70",
      chart: ["50808E", "84B59F", "69A297", "D9B384", "E08E45"],
    },
    type: TYPE_CORPORATE,
  },
  cherryBold: {
    name: "Cherry Bold",
    palette: {
      primary: "990011",
      secondary: "2F3C7E",
      accent: "FCF6F5",
      background: "FFFFFF",
      surface: "FBF1F1",
      textPrimary: "1A1A1A",
      textMuted: "4B5563",
      chart: ["990011", "C21E28", "E4572E", "2F3C7E", "4D5EAD"],
    },
    type: TYPE_BOLD,
  },
});

export type ThemeName = keyof typeof THEMES;

export function resolveTheme(theme: Theme | ThemeName | string): Theme {
  if (typeof theme !== "string") return theme;
  const found = (THEMES as Record<string, Theme>)[theme];
  if (!found) {
    const names = Object.keys(THEMES).join(", ");
    throw new Error(
      `Unknown theme "${theme}". Available: ${names}. ` +
        `Or pass a custom Theme object.`,
    );
  }
  return found;
}
