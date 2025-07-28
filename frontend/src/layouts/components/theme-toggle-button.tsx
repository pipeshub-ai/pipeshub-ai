import type { IconButtonProps } from '@mui/material/IconButton';

import { useState } from 'react';
import { m, AnimatePresence } from 'framer-motion';
import sunBoldIcon from '@iconify-icons/solar/sun-bold';
import moonBoldIcon from '@iconify-icons/solar/moon-bold';

import Tooltip from '@mui/material/Tooltip';
import IconButton from '@mui/material/IconButton';
import { useTheme, useColorScheme } from '@mui/material/styles';

import { Iconify } from 'src/components/iconify';

export type ThemeToggleButtonProps = IconButtonProps;

export function ThemeToggleButton({ sx, ...other }: ThemeToggleButtonProps) {
  const theme = useTheme();
  const { mode, setMode } = useColorScheme();
  const isDarkMode = mode === 'dark';
  const [isTransitioning, setIsTransitioning] = useState(false);

  const toggleTheme = () => {
    setIsTransitioning(true);
    setMode(isDarkMode ? 'light' : 'dark');
    // Reset transition state after animation completes
    setTimeout(() => setIsTransitioning(false), 400);
  };

  return (
    <Tooltip title={`Switch to ${isDarkMode ? 'light' : 'dark'} mode`} arrow placement="bottom">
      <IconButton
        aria-label={`Switch to ${isDarkMode ? 'light' : 'dark'} mode`}
        onClick={toggleTheme}
        sx={{
          p: 0,
          width: 40,
          height: 40,
          backgroundColor: 'transparent',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          overflow: 'hidden',
          '&:hover': {
            backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.04)',
          },
          transition: 'background-color 300ms cubic-bezier(0.4, 0, 0.2, 1)',
          ...sx,
        }}
        {...other}
      >
        <AnimatePresence mode="wait">
          <m.div
            key={isDarkMode ? 'sun' : 'moon'}
            initial={{
              scale: 0.5,
              opacity: 0,
              rotate: isDarkMode ? -90 : 90,
            }}
            animate={{
              scale: 1,
              opacity: 1,
              rotate: 0,
            }}
            exit={{
              scale: 0.5,
              opacity: 0,
              rotate: isDarkMode ? 90 : -90,
            }}
            transition={{
              duration: 0.4,
              ease: [0.4, 0, 0.2, 1],
            }}
          >
            <m.div
              animate={{
                rotate: isTransitioning ? [0, 360] : 0,
              }}
              transition={{
                duration: isTransitioning ? 0.4 : 0,
                ease: 'easeInOut',
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon={isDarkMode ? sunBoldIcon : moonBoldIcon}
                sx={{
                  width: 24,
                  height: 24,
                  color: isDarkMode ? '#FFA726' : '#5C6BC0',
                  filter: isDarkMode
                    ? 'drop-shadow(0 0 8px rgba(255, 167, 38, 0.3))'
                    : 'drop-shadow(0 0 8px rgba(92, 107, 192, 0.3))',
                }}
              />
            </m.div>
          </m.div>
        </AnimatePresence>
      </IconButton>
    </Tooltip>
  );
}
