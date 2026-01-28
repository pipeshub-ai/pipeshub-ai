import type { SettingsState } from 'src/components/settings';
import type { Theme, Components } from '@mui/material/styles';
import { darken, getContrastRatio, lighten } from '@mui/material/styles';

import COLORS from '../core/colors.json';
import PRIMARY_COLOR from './primary-color.json';
import { components as coreComponents } from '../core/components';
import { hexToRgbChannel, createPaletteChannel } from '../styles';
import { primary as corePrimary, grey as coreGreyPalette } from '../core/palette';
import { createShadowColor, customShadows as coreCustomShadows } from '../core/custom-shadows';

import type { ThemeComponents, ThemeUpdateOptions } from '../types';

// ----------------------------------------------------------------------

const PRIMARY_COLORS = {
  default: COLORS.primary,
  cyan: PRIMARY_COLOR.cyan,
  purple: PRIMARY_COLOR.purple,
  blue: PRIMARY_COLOR.blue,
  orange: PRIMARY_COLOR.orange,
  red: PRIMARY_COLOR.red,
  green: PRIMARY_COLOR.green,
};

// ----------------------------------------------------------------------

/**
 * [1] settings @primaryColor
 * [2] settings @contrast
 */

export function updateCoreWithSettings(
  theme: ThemeUpdateOptions,
  settings: SettingsState
): ThemeUpdateOptions {
  const { colorSchemes, customShadows } = theme;

  const updatedPrimary = getPalette(
    settings,
    corePrimary
  );

  return {
    ...theme,
    colorSchemes: {
      ...colorSchemes,
      light: {
        palette: {
          ...colorSchemes?.light?.palette,
          /** [1] */
          primary: updatedPrimary,
          /** [2] */
          background: {
            ...colorSchemes?.light?.palette?.background,
            default: getBackgroundDefault(settings.contrast),
            defaultChannel: hexToRgbChannel(getBackgroundDefault(settings.contrast)),
          },
        },
      },
      dark: {
        palette: {
          ...colorSchemes?.dark?.palette,
          /** [1] */
          primary: updatedPrimary,
        },
      },
    },
    customShadows: {
      ...customShadows,
      /** [1] */
      primary:
        settings.primaryColor === 'default'
          ? coreCustomShadows('light').primary
          : createShadowColor(updatedPrimary.mainChannel),
    },
  };
}

// ----------------------------------------------------------------------

export function updateComponentsWithSettings(settings: SettingsState) {
  const components: ThemeComponents = {};

  /** [2] */
  if (settings.contrast === 'hight') {
    const MuiCard: Components<Theme>['MuiCard'] = {
      styleOverrides: {
        root: ({ theme, ownerState }) => {
          let rootStyles = {};
          if (typeof coreComponents?.MuiCard?.styleOverrides?.root === 'function') {
            rootStyles = coreComponents.MuiCard.styleOverrides.root({ ownerState, theme }) ?? {};
          }

          return {
            ...rootStyles,
            boxShadow: theme.customShadows.z1,
          };
        },
      },
    };

    components.MuiCard = MuiCard;
  }

  return { components };
}

// ----------------------------------------------------------------------

function getPalette(settings: SettingsState, initialPalette: typeof corePrimary) {
  /** [1] */
  if (settings.primaryColor === 'default') {
    return initialPalette;
  }

  if (settings.primaryColor === 'custom') {
    return getCustomPrimaryPalette(settings.customPrimaryColor);
  }

  const preset = PRIMARY_COLORS[settings.primaryColor];
  return createPaletteChannel(preset ?? PRIMARY_COLORS.blue);
}

function getBackgroundDefault(contrast: SettingsState['contrast']) {
  /** [2] */
  return contrast === 'default' ? '#FFFFFF' : coreGreyPalette[200];
}

function getCustomPrimaryPalette(color: string) {
  const fallback = PRIMARY_COLOR.blue;

  if (!/^#([0-9a-fA-F]{6})$/.test(color ?? '')) {
    return createPaletteChannel(fallback);
  }

  const main = color;
  const light = lighten(main, 0.2);
  const lighter = lighten(main, 0.4);
  const dark = darken(main, 0.2);
  const darker = darken(main, 0.4);
  const contrastText = getContrastRatio(main, '#ffffff') >= 3 ? '#ffffff' : '#1C252E';

  return createPaletteChannel({
    lighter,
    light,
    main,
    dark,
    darker,
    contrastText,
  });
}
