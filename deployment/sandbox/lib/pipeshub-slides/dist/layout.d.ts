export type LayoutName = "16x9" | "16x10" | "4x3" | "wide";
export interface LayoutDimensions {
    width: number;
    height: number;
    pptxName: string;
}
export declare const LAYOUTS: Readonly<Record<LayoutName, LayoutDimensions>>;
export declare const MARGINS: Readonly<{
    left: 0.55;
    right: 0.55;
    top: 0.4;
    bottom: 0.4;
}>;
export declare const ACCENT_BAND_HEIGHT = 0.18;
export declare const TITLE_Y = 0.55;
export declare const TITLE_HEIGHT = 0.8;
export declare const BODY_Y: number;
