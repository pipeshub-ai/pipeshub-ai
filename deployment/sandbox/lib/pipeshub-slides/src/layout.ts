// Layout constants in inches. Everything in pipeshub-slides lays out against
// these so slides don't drift out of alignment across primitives.

export type LayoutName = "16x9" | "16x10" | "4x3" | "wide";

export interface LayoutDimensions {
  width: number;
  height: number;
  pptxName: string;
}

export const LAYOUTS: Readonly<Record<LayoutName, LayoutDimensions>> =
  Object.freeze({
    "16x9": { width: 10, height: 5.625, pptxName: "LAYOUT_16x9" },
    "16x10": { width: 10, height: 6.25, pptxName: "LAYOUT_16x10" },
    "4x3": { width: 10, height: 7.5, pptxName: "LAYOUT_4x3" },
    wide: { width: 13.333, height: 7.5, pptxName: "LAYOUT_WIDE" },
  });

// Generous default margins so titles never sit right against the slide edge.
export const MARGINS = Object.freeze({
  left: 0.55,
  right: 0.55,
  top: 0.4,
  bottom: 0.4,
});

// Height of the top accent band rendered on every content slide. Kept thin
// so it reads as a subtle brand mark rather than a heavy bar.
export const ACCENT_BAND_HEIGHT = 0.18;

// Vertical offset of the title from the top of the slide (above the accent
// band it's 0.4 margin + 0.18 band + 0.15 gap ≈ 0.7).
export const TITLE_Y = 0.55;
export const TITLE_HEIGHT = 0.8;

// Body content sits below the title with a consistent gap so two slides in
// a row look aligned.
export const BODY_Y = TITLE_Y + TITLE_HEIGHT + 0.2;
