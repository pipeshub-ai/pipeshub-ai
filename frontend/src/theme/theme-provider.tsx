import type {} from '@mui/lab/themeAugmentation';
import type {} from '@mui/x-tree-view/themeAugmentation';
import type {} from '@mui/x-data-grid/themeAugmentation';
import type {} from '@mui/x-date-pickers/themeAugmentation';
import type {} from '@mui/material/themeCssVarsAugmentation';

import { useState, useEffect } from 'react';

import CssBaseline from '@mui/material/CssBaseline';
import { Experimental_CssVarsProvider as CssVarsProvider } from '@mui/material/styles';

import axios from 'src/utils/axios';

import { useTranslate } from 'src/locales';

import { useSettingsContext } from 'src/components/settings';

import { schemeConfig } from './scheme-config';
import { RTL } from './with-settings/right-to-left';
import { createTheme, type BrandingConfig } from './create-theme';

type Props = {
  children: React.ReactNode;
};

export function ThemeProvider({ children }: Props) {
  const { currentLang } = useTranslate();

  const settings = useSettingsContext();

  const [branding, setBranding] = useState<BrandingConfig | undefined>(undefined);

  useEffect(() => {
    const fetchBranding = async () => {
      try {
        const response = await axios.get('/api/v1/configurationManager/platform/branding');
        if (response.data && Object.keys(response.data).length > 0) {
          setBranding(response.data);
        }
      } catch (error) {
        console.error('Failed to load branding', error);
      }
    };
    fetchBranding();
  }, []);

  const theme = createTheme(currentLang?.systemValue, settings, branding);

  return (
    <CssVarsProvider
      theme={theme}
      defaultMode={schemeConfig.defaultMode}
      modeStorageKey={schemeConfig.modeStorageKey}
    >
      <CssBaseline />
      <RTL direction={settings.direction}>{children}</RTL>
    </CssVarsProvider>
  );
}
