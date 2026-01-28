import type { Theme } from '@mui/material/styles';
import type { SettingsState } from 'src/components/settings';

import { darken, lighten, experimental_extendTheme as extendTheme } from '@mui/material/styles';

import { overridesTheme } from './overrides-theme';
import { setFont, createPaletteChannel } from './styles/utils';
import { shadows, typography, components, colorSchemes, customShadows } from './core';
import { updateCoreWithSettings, updateComponentsWithSettings } from './with-settings/update-theme';

import type { ThemeLocaleComponents } from './types';

// ----------------------------------------------------------------------

export type BrandingConfig = {
  primaryColor?: string;
  secondaryColor?: string;
  fontFamily?: string;
  // logoUrl and companyName are handled in components
};

export function createTheme(
  localeComponents: ThemeLocaleComponents,
  settings: SettingsState,
  branding?: BrandingConfig
): Theme {
  // Generate palette overrides from branding
  const brandingPalette: any = {};

  if (branding?.primaryColor) {
    const main = branding.primaryColor;
    brandingPalette.primary = createPaletteChannel({
      lighter: lighten(main, 0.8),
      light: lighten(main, 0.4),
      main,
      dark: darken(main, 0.2),
      darker: darken(main, 0.5),
      contrastText: '#ffffff', // Simplified
    });
  }

  if (branding?.secondaryColor) {
    const main = branding.secondaryColor;
    brandingPalette.secondary = createPaletteChannel({
      lighter: lighten(main, 0.8),
      light: lighten(main, 0.4),
      main,
      dark: darken(main, 0.2),
      darker: darken(main, 0.5),
      contrastText: '#ffffff',
    });
  }

  const initialTheme = {
    colorSchemes: {
      ...colorSchemes,
      light: {
        palette: {
          ...colorSchemes.light?.palette,
          ...brandingPalette,
        },
      },
      dark: {
        palette: {
          ...colorSchemes.dark?.palette,
          ...brandingPalette,
        },
      },
    },
    shadows: shadows(settings.colorScheme),
    customShadows: customShadows(settings.colorScheme),
    direction: settings.direction,
    shape: { borderRadius: 8 },
    components,
    typography: {
      ...typography,
      fontFamily: setFont(branding?.fontFamily || settings.fontFamily),
    },
    cssVarPrefix: '',
    shouldSkipGeneratingVar,
  };

  /**
   * 1.Update values from settings before creating theme.
   */
  const updateTheme = updateCoreWithSettings(initialTheme, settings);

  /**
   * 2.Create theme + add locale + update component with settings.
   */
  const theme = extendTheme(
    updateTheme,
    localeComponents,
    updateComponentsWithSettings(settings),
    overridesTheme
  );

  return theme;
}

// ----------------------------------------------------------------------

function shouldSkipGeneratingVar(keys: string[], value: string | number): boolean {
  const skipGlobalKeys = [
    'mixins',
    'overlays',
    'direction',
    'breakpoints',
    'cssVarPrefix',
    'unstable_sxConfig',
    'typography',
    // 'transitions',
  ];

  const skipPaletteKeys: {
    [key: string]: string[];
  } = {
    global: ['tonalOffset', 'dividerChannel', 'contrastThreshold'],
    grey: ['A100', 'A200', 'A400', 'A700'],
    text: ['icon'],
  };

  const isPaletteKey = keys[0] === 'palette';

  if (isPaletteKey) {
    const paletteType = keys[1];
    const skipKeys = skipPaletteKeys[paletteType] || skipPaletteKeys.global;

    return keys.some((key) => skipKeys?.includes(key));
  }

  return keys.some((key) => skipGlobalKeys?.includes(key));
}

/**
* createTheme without @settings and @locale components.
*
 ```jsx
export function createTheme(): Theme {
  const initialTheme = {
    colorSchemes,
    shadows: shadows('light'),
    customShadows: customShadows('light'),
    shape: { borderRadius: 8 },
    components,
    typography,
    cssVarPrefix: '',
    shouldSkipGeneratingVar,
  };

  const theme = extendTheme(initialTheme, overridesTheme);

  return theme;
}
 ```
*/
