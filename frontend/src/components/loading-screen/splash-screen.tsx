import type { BoxProps } from '@mui/material/Box';

import { useState, useEffect } from 'react';

import Box from '@mui/material/Box';
import Portal from '@mui/material/Portal';

import { LogoSpinner } from './logo-spinner';

// ----------------------------------------------------------------------

type Props = BoxProps & {
  portal?: boolean;
};

export function SplashScreen({ portal = true, sx, ...other }: Props) {
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setLoaded(true);
    }, 100);
    
    return () => clearTimeout(timer);
  }, []);

  const content = (
    <Box sx={{ overflow: 'hidden' }}>
      <Box
        sx={{
          right: 0,
          width: 1,
          bottom: 0,
          height: 1,
          zIndex: 9998,
          display: 'flex',
          flexDirection: 'column',
          position: 'fixed',
          alignItems: 'center',
          justifyContent: 'center',
          bgcolor: (theme) => 
            `radial-gradient(circle, ${theme.palette.background.paper} 10%, ${theme.palette.background.default} 100%)`,
          opacity: loaded ? 1 : 0,
          transition: 'opacity 0.3s ease',
          ...sx,
        }}
        {...other}
      >
        <LogoSpinner
          size={112}
          sx={{
            opacity: loaded ? 1 : 0,
            transition: 'opacity 0.4s ease',
          }}
        />
      </Box>
    </Box>
  );

  if (portal) {
    return <Portal>{content}</Portal>;
  }

  return content;
}
