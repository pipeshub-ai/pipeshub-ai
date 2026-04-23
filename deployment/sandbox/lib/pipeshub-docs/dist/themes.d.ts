export interface DocTheme {
    readonly name: string;
    readonly palette: {
        readonly primary: string;
        readonly secondary: string;
        readonly accent: string;
        readonly textPrimary: string;
        readonly textMuted: string;
        readonly surface: string;
        readonly tableHeaderText: string;
        readonly tableZebra: string;
    };
    readonly type: {
        readonly headerFont: string;
        readonly bodyFont: string;
    };
}
export declare const THEMES: Readonly<Record<string, DocTheme>>;
export type DocThemeName = keyof typeof THEMES;
export declare function resolveTheme(theme: DocTheme | DocThemeName | string): DocTheme;
