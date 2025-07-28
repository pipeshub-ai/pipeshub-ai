import type { Theme, CSSObject } from '@mui/material/styles';

import { dividerClasses } from '@mui/material/Divider';
import { checkboxClasses } from '@mui/material/Checkbox';
import { menuItemClasses } from '@mui/material/MenuItem';
import { autocompleteClasses } from '@mui/material/Autocomplete';

import { remToPx, varAlpha, mediaQueries } from './utils';

// ----------------------------------------------------------------------

/**
 * Usage:
 * ...hideScrollX,
 * ...hideScrollY,
 */
export const hideScrollX: CSSObject = {
  msOverflowStyle: 'none',
  scrollbarWidth: 'none',
  overflowX: 'auto',
  '&::-webkit-scrollbar': { display: 'none' },
};

export const hideScrollY: CSSObject = {
  msOverflowStyle: 'none',
  scrollbarWidth: 'none',
  overflowY: 'auto',
  '&::-webkit-scrollbar': { display: 'none' },
};

/**
 * Usage:
 * ...textGradient(`to right, ${theme.vars.palette.text.primary}, ${alpha(theme.vars.palette.text.primary, 0.2)}`
 */
export function textGradient(color: string): CSSObject {
  return {
    background: `linear-gradient(${color})`,
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    backgroundClip: 'text',
    textFillColor: 'transparent',
    color: 'transparent',
  };
}

/**
 * Usage:
 * ...borderGradient({ color: `to right, ${theme.vars.palette.text.primary}, ${alpha(theme.vars.palette.text.primary, 0.2)}`, padding: '4px' }),
 */
export type BorderGradientProps = {
  color?: string;
  padding?: string;
};

export function borderGradient(props?: BorderGradientProps): CSSObject {
  return {
    inset: 0,
    width: '100%',
    content: '""',
    height: '100%',
    margin: 'auto',
    position: 'absolute',
    borderRadius: 'inherit',
    padding: props?.padding ?? '2px',
    //
    mask: 'linear-gradient(#FFF 0 0) content-box, linear-gradient(#FFF 0 0)',
    WebkitMask: 'linear-gradient(#FFF 0 0) content-box, linear-gradient(#FFF 0 0)',
    maskComposite: 'exclude',
    WebkitMaskComposite: 'xor',
    ...(props?.color && {
      background: `linear-gradient(${props.color})`,
    }),
  };
}

/**
 * Usage:
 */
export type BgGradientProps = {
  color: string;
  imgUrl?: string;
};

export function bgGradient({ color, imgUrl }: BgGradientProps): CSSObject {
  if (imgUrl) {
    return {
      background: `linear-gradient(${color}), url(${imgUrl})`,
      backgroundSize: 'cover',
      backgroundRepeat: 'no-repeat',
      backgroundPosition: 'center center',
    };
  }
  return { background: `linear-gradient(${color})` };
}

/**
 * Usage:
 */
export type BgBlurProps = {
  color: string;
  blur?: number;
  imgUrl?: string;
};

export function bgBlur({ color, blur = 6, imgUrl }: BgBlurProps): CSSObject {
  if (imgUrl) {
    return {
      position: 'relative',
      backgroundImage: `url(${imgUrl})`,
      '&::before': {
        position: 'absolute',
        top: 0,
        left: 0,
        zIndex: 9,
        content: '""',
        width: '100%',
        height: '100%',
        backdropFilter: `blur(${blur}px)`,
        WebkitBackdropFilter: `blur(${blur}px)`,
        backgroundColor: color,
      },
    };
  }
  return {
    backdropFilter: `blur(${blur}px)`,
    WebkitBackdropFilter: `blur(${blur}px)`,
    backgroundColor: color,
  };
}

/**
 * Usage:
 * ...maxLine({ line: 2, persistent: theme.typography.caption }),
 */
export type MediaFontSize = {
  [key: string]: {
    fontSize: React.CSSProperties['fontSize'];
  };
};

export type MaxLineProps = {
  line: number;
  persistent?: Partial<React.CSSProperties>;
};

function getFontSize(fontSize: React.CSSProperties['fontSize']) {
  return typeof fontSize === 'string' ? remToPx(fontSize) : fontSize;
}

function getLineHeight(lineHeight: React.CSSProperties['lineHeight'], fontSize?: number) {
  if (typeof lineHeight === 'string') {
    return fontSize ? remToPx(lineHeight) / fontSize : 1;
  }
  return lineHeight;
}

export function maxLine({ line, persistent }: MaxLineProps): CSSObject {
  const baseStyles: CSSObject = {
    overflow: 'hidden',
    display: '-webkit-box',
    textOverflow: 'ellipsis',
    WebkitLineClamp: line,
    WebkitBoxOrient: 'vertical',
  };

  if (persistent) {
    const fontSizeBase = getFontSize(persistent.fontSize);
    const fontSizeSm = getFontSize((persistent as MediaFontSize)[mediaQueries.upSm]?.fontSize);
    const fontSizeMd = getFontSize((persistent as MediaFontSize)[mediaQueries.upMd]?.fontSize);
    const fontSizeLg = getFontSize((persistent as MediaFontSize)[mediaQueries.upLg]?.fontSize);

    const lineHeight = getLineHeight(persistent.lineHeight, fontSizeBase);

    return {
      ...baseStyles,
      ...(lineHeight && {
        ...(fontSizeBase && { height: fontSizeBase * lineHeight * line }),
        ...(fontSizeSm && { [mediaQueries.upSm]: { height: fontSizeSm * lineHeight * line } }),
        ...(fontSizeMd && { [mediaQueries.upMd]: { height: fontSizeMd * lineHeight * line } }),
        ...(fontSizeLg && { [mediaQueries.upLg]: { height: fontSizeLg * lineHeight * line } }),
      }),
    };
  }

  return baseStyles;
}

/**
 * Usage:
 * ...paper({ theme, color: varAlpha(theme.vars.palette.background.paperChannel, 0.9), dropdown: true }),
 */
type PaperProps = {
  theme: Theme;
  color?: string;
  dropdown?: boolean;
};

export function paper({ theme, color, dropdown }: PaperProps) {
  return {
    ...bgBlur({
      color: color ?? varAlpha(theme.vars.palette.background.paperChannel, 0.9),
      blur: 20,
    }),
    backgroundRepeat: 'no-repeat, no-repeat',
    backgroundPosition: 'top right, left bottom',
    backgroundSize: '50%, 50%',
    ...(theme.direction === 'rtl' && { backgroundPosition: 'top left, right bottom' }),
    ...(dropdown && {
      padding: theme.spacing(0.5),
      boxShadow: theme.customShadows.dropdown,
      borderRadius: `${theme.shape.borderRadius * 1.25}px`,
    }),
  };
}

/**
 * Usage:
 * ...menuItem(theme)
 */
export function menuItem(theme: Theme) {
  return {
    ...theme.typography.body2,
    padding: theme.spacing(0.75, 1),
    borderRadius: theme.shape.borderRadius * 0.75,
    '&:not(:last-of-type)': { marginBottom: 4 },
    [`&.${menuItemClasses.selected}`]: {
      fontWeight: theme.typography.fontWeightSemiBold,
      backgroundColor: theme.vars.palette.action.selected,
      '&:hover': { backgroundColor: theme.vars.palette.action.hover },
    },
    [`& .${checkboxClasses.root}`]: {
      padding: theme.spacing(0.5),
      marginLeft: theme.spacing(-0.5),
      marginRight: theme.spacing(0.5),
    },
    [`&.${autocompleteClasses.option}[aria-selected="true"]`]: {
      backgroundColor: theme.vars.palette.action.selected,
      '&:hover': { backgroundColor: theme.vars.palette.action.hover },
    },
    [`&+.${dividerClasses.root}`]: { margin: theme.spacing(0.5, 0) },
  };
}
