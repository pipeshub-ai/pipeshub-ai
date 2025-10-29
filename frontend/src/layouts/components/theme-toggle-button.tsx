import type { StackProps } from '@mui/material/Stack';
import type { SxProps, Theme } from '@mui/material/styles';

import { m } from 'framer-motion';
import { useMemo } from 'react';

import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';

import { useColorScheme } from '@mui/material/styles';

import type { IconifyIcon } from '@iconify/react';

import monitorIcon from '@iconify-icons/mdi/monitor-dashboard';
import sunIcon from '@iconify-icons/mdi/weather-sunny';
import moonIcon from '@iconify-icons/mdi/weather-night';

import { Iconify } from 'src/components/iconify';

type ThemeValue = 'light' | 'dark' | 'system';

const OPTIONS: { value: ThemeValue; icon: IconifyIcon | string }[] = [
  { value: 'system', icon: monitorIcon },
  { value: 'light', icon: sunIcon },
  { value: 'dark', icon: moonIcon },
];

export type ThemeToggleButtonProps = Omit<StackProps, 'onClick'> & {
  sx?: SxProps<Theme>;
  disabled?: boolean;
};

export function ThemeToggleButton({ sx, disabled, ...other }: ThemeToggleButtonProps) {
  const { mode, setMode } = useColorScheme();

  const activeValue: ThemeValue = useMemo(() => {
    if (mode === 'light' || mode === 'dark') {
      return mode;
    }
    return 'system';
  }, [mode]);

  const handleChange = (value: ThemeValue) => {
    if (value === 'system') {
      setMode('system');
    } else {
      setMode(value);
    }
  };

  return (
    <Stack
      direction="row"
      spacing={0.5}
      role="radiogroup"
      sx={{
        alignItems: 'center',
        justifyContent: 'center',
        height: 40,
        px: 0.5,
        borderRadius: 999,
        bgcolor: (theme) => theme.palette.background.paper,
        border: (theme) => `1px solid ${theme.palette.divider}`,
        boxShadow: (theme) => theme.customShadows?.z1 ?? 'none',
        ...(disabled && { opacity: 0.6, pointerEvents: 'none' }),
        ...sx,
      }}
      {...other}
    >
      {OPTIONS.map(({ value, icon }) => {
        const selected = activeValue === value;

        return (
          <Button
            key={value}
            variant="text"
            color="inherit"
            onClick={() => handleChange(value)}
            disabled={disabled}
            disableElevation
            size="small"
            role="radio"
            aria-checked={selected}
            aria-label={`Switch to ${value} theme`}
            sx={{
              minWidth: 0,
              width: 40,
              height: 32,
              borderRadius: 999,
              px: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              textTransform: 'none',
              color: selected ? 'text.primary' : 'text.secondary',
              bgcolor: (theme) =>
                selected ? theme.vars.palette.action.selected : 'transparent',
              '&:hover': {
                bgcolor: (theme) =>
                  selected ? theme.vars.palette.action.selected : theme.vars.palette.action.hover,
              },
            }}
          >
            <m.span
              animate={{ scale: selected ? 1.05 : 1 }}
              transition={{ duration: 0.2 }}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <Iconify icon={icon} width={18} />
            </m.span>
          </Button>
        );
      })}
    </Stack>
  );
}
