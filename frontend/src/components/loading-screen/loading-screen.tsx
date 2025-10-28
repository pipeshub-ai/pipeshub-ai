import type { BoxProps } from '@mui/material/Box';

import { useState, useEffect } from 'react';

import Box from '@mui/material/Box';
import Portal from '@mui/material/Portal';

import { LogoSpinner } from './logo-spinner';

// ----------------------------------------------------------------------

type Props = BoxProps & {
  portal?: boolean;
};

export function LoadingScreen({ portal, sx, ...other }: Props) {
  const [loaded, setLoaded] = useState(false);

  // Add a small delay before showing the animation for a smoother entry
  useEffect(() => {
    const timer = setTimeout(() => {
      setLoaded(true);
    }, 300);

    return () => clearTimeout(timer);
  }, []);

  const content = (
    <Box
      sx={{
        px: 5,
        width: 1,
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: (theme) =>
          `radial-gradient(circle, ${theme.palette.background.paper} 0%, ${theme.palette.background.default} 100%)`,
        transition: 'all 0.5s ease-in-out',
        opacity: loaded ? 1 : 0,
        ...sx,
      }}
      {...other}
    >
      <LogoSpinner
        size={96}
        sx={{
          opacity: loaded ? 1 : 0,
          transition: 'opacity 0.4s ease',
        }}
      />
    </Box>
  );

  if (portal) {
    return <Portal>{content}</Portal>;
  }

  return content;
}
