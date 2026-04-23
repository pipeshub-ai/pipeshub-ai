// Document themes: font pairings + brand colors. Kept aligned with
// pipeshub-slides THEMES so a pptx and docx produced for the same topic
// share a visual identity. Colors are 6-char hex WITHOUT '#'.
export const THEMES = Object.freeze({
    corporate: {
        name: "Corporate",
        palette: {
            primary: "1E2761",
            secondary: "3C5CCB",
            accent: "F6AE2D",
            textPrimary: "1F2937",
            textMuted: "6B7280",
            surface: "F8FAFC",
            tableHeaderText: "FFFFFF",
            tableZebra: "F6F8FA",
        },
        type: { headerFont: "Cambria", bodyFont: "Calibri" },
    },
    editorial: {
        name: "Editorial",
        palette: {
            primary: "2C5F2D",
            secondary: "97BC62",
            accent: "B85042",
            textPrimary: "1F2937",
            textMuted: "6B7280",
            surface: "F5F5F5",
            tableHeaderText: "FFFFFF",
            tableZebra: "F7F8F3",
        },
        type: { headerFont: "Georgia", bodyFont: "Georgia" },
    },
    minimal: {
        name: "Minimal",
        palette: {
            primary: "36454F",
            secondary: "E07A5F",
            accent: "81B29A",
            textPrimary: "212121",
            textMuted: "6B7280",
            surface: "F7F7F7",
            tableHeaderText: "FFFFFF",
            tableZebra: "F2F2F2",
        },
        type: { headerFont: "Calibri", bodyFont: "Calibri" },
    },
    teal: {
        name: "Teal Trust",
        palette: {
            primary: "028090",
            secondary: "00A896",
            accent: "F4A261",
            textPrimary: "05313A",
            textMuted: "4F6D75",
            surface: "E8F6F5",
            tableHeaderText: "FFFFFF",
            tableZebra: "F1F7F6",
        },
        type: { headerFont: "Cambria", bodyFont: "Calibri" },
    },
});
export function resolveTheme(theme) {
    if (typeof theme !== "string")
        return theme;
    const found = THEMES[theme];
    if (!found) {
        const names = Object.keys(THEMES).join(", ");
        throw new Error(`Unknown docx theme "${theme}". Available: ${names}. ` +
            `Or pass a custom DocTheme object.`);
    }
    return found;
}
