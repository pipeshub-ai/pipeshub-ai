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
export declare const THEMES: Readonly<Record<string, Theme>>;
export type ThemeName = keyof typeof THEMES;
export declare function resolveTheme(theme: Theme | ThemeName | string): Theme;
