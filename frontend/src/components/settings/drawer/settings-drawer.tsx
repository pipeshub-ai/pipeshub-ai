import closeIcon from '@iconify-icons/mingcute/close-line';
import restartIcon from '@iconify-icons/solar/restart-bold';

import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Badge from '@mui/material/Badge';
import Tooltip from '@mui/material/Tooltip';
import Button from '@mui/material/Button';
import IconButton from '@mui/material/IconButton';
import Typography from '@mui/material/Typography';
import Drawer, { drawerClasses } from '@mui/material/Drawer';
import TextField from '@mui/material/TextField';
import TabContext from '@mui/lab/TabContext';
import TabList from '@mui/lab/TabList';
import TabPanel from '@mui/lab/TabPanel';
import Tab from '@mui/material/Tab';
import { useTheme, useColorScheme } from '@mui/material/styles';
import { useState, SyntheticEvent, ChangeEvent } from 'react';

import COLORS from 'src/theme/core/colors.json';
import { paper, varAlpha } from 'src/theme/styles';
import { defaultFont } from 'src/theme/core/typography';
import PRIMARY_COLOR from 'src/theme/with-settings/primary-color.json';

import { Iconify } from '../../iconify';
import { BaseOption } from './base-option';
import { Scrollbar } from '../../scrollbar';
import { FontOptions } from './font-options';
import { NavOptions } from './nav-options';
import { useSettingsContext } from '../context';
import { PresetsOptions } from './presets-options';
import { defaultSettings } from '../config-settings';
import { FullScreenButton } from './fullscreen-button';
import { Block } from './styles';

import type { SettingsDrawerProps, SettingsState } from '../types';

// ----------------------------------------------------------------------

export function SettingsDrawer({
  sx,
  hideFont,
  hideCompact,
  hidePresets,
  hideNavColor,
  hideContrast,
  hideNavLayout,
  hideDirection,
  hideColorScheme,
}: SettingsDrawerProps) {
  const theme = useTheme();

  const settings = useSettingsContext();

  const { mode, setMode } = useColorScheme();
  const [tabValue, setTabValue] = useState<'layout' | 'branding'>('layout');

  const handleTabChange = (_event: SyntheticEvent, newValue: string) => {
    setTabValue(newValue as 'layout' | 'branding');
  };

  const customColorValue = settings.customPrimaryColor || PRIMARY_COLOR.blue.main;
  const isCustomColorValid = /^#([0-9a-fA-F]{6})$/.test(customColorValue);

  const handleCustomColorChange = (event: ChangeEvent<HTMLInputElement>) => {
    settings.onUpdateField('customPrimaryColor', event.target.value);
  };

  const handleApplyCustomColor = () => {
    if (!isCustomColorValid) {
      return;
    }
    settings.onUpdateField('primaryColor', 'custom');
  };

  const presetOptions: { name: SettingsState['primaryColor']; value: string }[] = [
    { name: 'default', value: COLORS.primary.main },
    { name: 'cyan', value: PRIMARY_COLOR.cyan.main },
    { name: 'purple', value: PRIMARY_COLOR.purple.main },
    { name: 'blue', value: PRIMARY_COLOR.blue.main },
    { name: 'orange', value: PRIMARY_COLOR.orange.main },
    { name: 'red', value: PRIMARY_COLOR.red.main },
    { name: 'green', value: PRIMARY_COLOR.green.main },
  ];

  if (isCustomColorValid) {
    presetOptions.push({ name: 'custom', value: customColorValue });
  }

  const showNavOptions = !hideNavColor || !hideNavLayout;

  const navOptions = showNavOptions ? (
    <NavOptions
      value={{ color: settings.navColor, layout: settings.navLayout }}
      options={{
        colors: ['integrate', 'apparent'],
        layouts: ['horizontal', 'vertical', 'mini'],
      }}
      onClickOption={{
        color: (newValue) => settings.onUpdateField('navColor', newValue),
        layout: (newValue) => settings.onUpdateField('navLayout', newValue),
      }}
      hideNavColor={hideNavColor}
      hideNavLayout={hideNavLayout}
    />
  ) : null;

  const renderHead = (
    <Box display="flex" alignItems="center" sx={{ py: 2, pr: 1, pl: 2.5 }}>
      <Typography variant="h6" sx={{ flexGrow: 1 }}>
        UI Settings
      </Typography>

      <FullScreenButton />

      <Tooltip title="Reset">
        <IconButton
          onClick={() => {
            settings.onReset();
            setMode(defaultSettings.colorScheme);
          }}
        >
          <Badge color="error" variant="dot" invisible={!settings.canReset}>
            <Iconify icon={restartIcon} />
          </Badge>
        </IconButton>
      </Tooltip>

      <Tooltip title="Close">
        <IconButton onClick={settings.onCloseDrawer}>
          <Iconify icon={closeIcon} />
        </IconButton>
      </Tooltip>
    </Box>
  );

  const renderMode = (
    <BaseOption
      label="Dark mode"
      icon="moon"
      selected={settings.colorScheme === 'dark'}
      onClick={() => {
        settings.onUpdateField('colorScheme', mode === 'light' ? 'dark' : 'light');
        setMode(mode === 'light' ? 'dark' : 'light');
      }}
    />
  );

  const renderContrast = (
    <BaseOption
      label="Contrast"
      icon="contrast"
      selected={settings.contrast === 'hight'}
      onClick={() =>
        settings.onUpdateField('contrast', settings.contrast === 'default' ? 'hight' : 'default')
      }
    />
  );

  const renderRTL = (
    <BaseOption
      label="Right to left"
      icon="align-right"
      selected={settings.direction === 'rtl'}
      onClick={() =>
        settings.onUpdateField('direction', settings.direction === 'ltr' ? 'rtl' : 'ltr')
      }
    />
  );

  const renderCompact = (
    <BaseOption
      tooltip="Dashboard only and available at large resolutions > 1600px (xl)"
      label="Compact"
      icon="autofit-width"
      selected={settings.compactLayout}
      onClick={() => settings.onUpdateField('compactLayout', !settings.compactLayout)}
    />
  );

  const renderPresets = !hidePresets ? (
    <PresetsOptions
      value={settings.primaryColor}
      onClickOption={(newValue) => settings.onUpdateField('primaryColor', newValue)}
      options={presetOptions}
    />
  ) : null;

  const renderFont = !hideFont ? (
    <FontOptions
      value={settings.fontFamily}
      onClickOption={(newValue) => settings.onUpdateField('fontFamily', newValue)}
      options={[defaultFont, 'Inter Variable', 'DM Sans Variable', 'Nunito Sans Variable']}
    />
  ) : null;

  const renderCustomColor = (
    <Block title="Custom primary" sx={{ gap: 2 }}>
      <Stack spacing={1.5}>
        <TextField
          label="Hex value"
          value={customColorValue}
          onChange={handleCustomColorChange}
          placeholder="#0057FF"
          InputProps={{ sx: { fontFamily: 'monospace' } }}
          error={Boolean(customColorValue) && !isCustomColorValid}
          helperText={
            Boolean(customColorValue) && !isCustomColorValid
              ? 'Use a valid hex code such as #0057FF'
              : 'Supports #RRGGBB format'
          }
        />
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Box
            sx={{
              width: 48,
              height: 48,
              borderRadius: 1.5,
              bgcolor: isCustomColorValid ? customColorValue : varAlpha(theme.vars.palette.grey['500Channel'], 0.16),
              border: `1px solid ${varAlpha(theme.vars.palette.grey['500Channel'], 0.24)}`,
            }}
          />
          <Button
            variant="contained"
            disabled={!isCustomColorValid}
            onClick={handleApplyCustomColor}
          >
            Apply custom color
          </Button>
          {settings.primaryColor === 'custom' && (
            <Typography variant="caption" color="text.secondary">
              Custom color active
            </Typography>
          )}
        </Stack>
      </Stack>
    </Block>
  );

  return (
    <Drawer
      anchor="right"
      open={settings.openDrawer}
      onClose={settings.onCloseDrawer}
      slotProps={{ backdrop: { invisible: true } }}
      sx={{
        [`& .${drawerClasses.paper}`]: {
          ...paper({
            theme,
            color: varAlpha(theme.vars.palette.background.defaultChannel, 0.9),
          }),
          width: 360,
          ...sx,
        },
      }}
    >
      {renderHead}

      <TabContext value={tabValue}>
        <Box sx={{ px: 2.5 }}>
          <TabList
            onChange={handleTabChange}
            variant="fullWidth"
            aria-label="Settings sections"
          >
            <Tab label="Layout" value="layout" />
            <Tab label="Branding" value="branding" />
          </TabList>
        </Box>

        <Scrollbar>
          <TabPanel value="layout" sx={{ px: 2.5, pb: 5 }}>
            <Stack spacing={showNavOptions ? 6 : 4}>
              <Box gap={2} display="grid" gridTemplateColumns="repeat(2, 1fr)">
                {!hideColorScheme && renderMode}
                {!hideContrast && renderContrast}
                {!hideDirection && renderRTL}
                {!hideCompact && renderCompact}
              </Box>
              {navOptions}
            </Stack>
          </TabPanel>

          <TabPanel value="branding" sx={{ px: 2.5, pb: 5 }}>
            <Stack spacing={4}>
              {renderPresets}
              {renderCustomColor}
              {renderFont}
            </Stack>
          </TabPanel>
        </Scrollbar>
      </TabContext>
    </Drawer>
  );
}
