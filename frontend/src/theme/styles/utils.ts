// ----------------------------------------------------------------------

export const stylesMode = {
  light: '[data-mui-color-scheme="light"] &',
  dark: '[data-mui-color-scheme="dark"] &',
};

export const mediaQueries = {
  upXs: '@media (min-width:0px)',
  upSm: '@media (min-width:600px)',
  upMd: '@media (min-width:900px)',
  upLg: '@media (min-width:1200px)',
  upXl: '@media (min-width:1536px)',
};

/**
 * Set font family
 */
export function setFont(fontName: string) {
  return `"${fontName}",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif,"Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol"`;
}

/**
 * Converts rem to px
 */
export function remToPx(value: string): number {
  return Math.round(parseFloat(value) * 16);
}

/**
 * Converts px to rem
 */
export function pxToRem(value: number): string {
  return `${value / 16}rem`;
}

/**
 * Responsive font sizes
 */
export function responsiveFontSizes({ sm, md, lg }: { sm: number; md: number; lg: number }) {
  return {
    [mediaQueries.upSm]: { fontSize: pxToRem(sm) },
    [mediaQueries.upMd]: { fontSize: pxToRem(md) },
    [mediaQueries.upLg]: { fontSize: pxToRem(lg) },
  };
}

/**
 * Converts a hex color to RGB channels
 */
export function hexToRgbChannel(hex: string) {
  // Handle RGB format: rgb(r, g, b)
  if (hex.startsWith('rgb')) {
    const rgbValues = hex.match(/\d+/g);
    if (rgbValues && rgbValues.length >= 3) {
      return `${rgbValues[0]} ${rgbValues[1]} ${rgbValues[2]}`;
    }
  }

  if (!/^#[0-9A-F]{6}$/i.test(hex)) {
    // If it's a 3-digit hex, expand it
    if (/^#[0-9A-F]{3}$/i.test(hex)) {
      const r = parseInt(hex.charAt(1) + hex.charAt(1), 16);
      const g = parseInt(hex.charAt(2) + hex.charAt(2), 16);
      const b = parseInt(hex.charAt(3) + hex.charAt(3), 16);
      return `${r} ${g} ${b}`;
    }
    throw new Error(`Invalid hex color: ${hex}`);
  }

  const r = parseInt(hex.substring(1, 3), 16);
  const g = parseInt(hex.substring(3, 5), 16);
  const b = parseInt(hex.substring(5, 7), 16);

  return `${r} ${g} ${b}`;
}

/**
 * Converts a hex color to RGB channels
 */
export function createPaletteChannel(hexPalette: Record<string, string>) {
  const channelPalette: Record<string, string> = {};

  Object.entries(hexPalette).forEach(([key, value]) => {
    channelPalette[`${key}Channel`] = hexToRgbChannel(value);
  });

  return { ...hexPalette, ...channelPalette };
}

/**
 * Color with alpha channel
 */
export function varAlpha(color: string, opacity = 1) {
  const unsupported =
    color.startsWith('#') ||
    color.startsWith('rgb') ||
    color.startsWith('rgba') ||
    (!color.includes('var') && color.includes('Channel'));

  if (unsupported) {
    throw new Error(
      `[Alpha]: Unsupported color format "${color}".
       Supported formats are:
       - RGB channels: "0 184 217".
       - CSS variables with "Channel" prefix: "var(--palette-common-blackChannel, #000000)".
       Unsupported formats are:
       - Hex: "#00B8D9".
       - RGB: "rgb(0, 184, 217)".
       - RGBA: "rgba(0, 184, 217, 1)".
       `
    );
  }

  return `rgba(${color} / ${opacity})`;
}
