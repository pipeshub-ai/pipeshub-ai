import type { IconButtonProps } from '@mui/material/IconButton';
import { m } from 'framer-motion';
import Badge from '@mui/material/Badge';
import SvgIcon from '@mui/material/SvgIcon';
import IconButton from '@mui/material/IconButton';
import { useSettingsContext } from 'src/components/settings/context';
import { useTheme } from '@mui/material/styles';

export type SettingsButtonProps = IconButtonProps;

export function SettingsButton({ sx, ...other }: SettingsButtonProps) {
  const settings = useSettingsContext();
  const theme = useTheme();
  const isDarkMode = theme.palette.mode === 'dark';

  return (
    <IconButton
      aria-label="UI settings"
      onClick={settings.onToggleDrawer}
      sx={{
        p: 0,
        width: 40,
        height: 40,
        backgroundColor: 'transparent',
        '&:hover': {
          backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.04)',
        },
        transition: 'background-color 200ms cubic-bezier(0.4, 0, 0.2, 1)',
        ...sx,
      }}
      {...other}
    >
      <Badge 
        color="primary" 
        variant="dot" 
        invisible={!settings.canReset}
        sx={{
          '& .MuiBadge-badge': {
            boxShadow: isDarkMode ? '0 0 0 2px #1a1a1a' : '0 0 0 2px #ffffff',
          },
        }}
      >
        <SvgIcon
          component={m.svg}
          animate={{ 
            y: [0, -1, 0, 1, 0],
          }}
          transition={{ 
            duration: 4, 
            ease: "easeInOut", 
            repeat: Infinity,
            repeatDelay: 2
          }}
          sx={{
            color: isDarkMode ? 'grey.300' : 'grey.700',
            '&:hover': {
              color: theme.palette.primary.main,
            },
          }}
          viewBox="0 0 24 24"
        >
          {/* Palette Icon */}
          <path
            fill="currentColor"
            d="M12 2C6.49 2 2 6.49 2 12s4.49 10 10 10c1.38 0 2.5-1.12 2.5-2.5 0-.61-.23-1.2-.64-1.67-.08-.1-.13-.21-.13-.33 0-.28.22-.5.5-.5H16c3.31 0 6-2.69 6-6 0-4.96-4.49-9-10-9zm5.5 11c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm-3-4c-.83 0-1.5-.67-1.5-1.5S13.67 6 14.5 6s1.5.67 1.5 1.5S15.33 9 14.5 9zM5 11.5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5S7.33 13 6.5 13 5 12.33 5 11.5zm6-4c0 .83-.67 1.5-1.5 1.5S8 8.33 8 7.5 8.67 6 9.5 6s1.5.67 1.5 1.5z"
            opacity={isDarkMode ? "0.9" : "0.8"}
          />
          <path
            fill="currentColor"
            d="M12 2C6.49 2 2 6.49 2 12s4.49 10 10 10c1.38 0 2.5-1.12 2.5-2.5 0-.61-.23-1.2-.64-1.67-.08-.1-.13-.21-.13-.33 0-.28.22-.5.5-.5H16c3.31 0 6-2.69 6-6 0-4.96-4.49-9-10-9zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8c0 2.21-1.79 4-4 4h-1.77c-.28 0-.5.22-.5.5 0 .12.05.23.13.33.41.47.64 1.06.64 1.67 0 1.38-1.12 2.5-2.5 2.5z"
            opacity="0.4"
          />
        </SvgIcon>
      </Badge>
    </IconButton>
  );
}