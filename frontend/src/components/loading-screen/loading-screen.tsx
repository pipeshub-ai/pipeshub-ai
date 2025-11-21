import type { BoxProps } from '@mui/material/Box';

import { useState, useEffect } from 'react';

import Box from '@mui/material/Box';
import Portal from '@mui/material/Portal';

// ----------------------------------------------------------------------

type Props = BoxProps & {
  portal?: boolean;
};

export function LoadingScreen({ portal, sx, ...other }: Props) {
  const [loaded, setLoaded] = useState(false);
  const [dotOpacity, setDotOpacity] = useState([0.4, 0.6, 0.8]);

  // Add a small delay before showing the animation for a smoother entry
  useEffect(() => {
    const timer = setTimeout(() => {
      setLoaded(true);
    }, 300);

    return () => clearTimeout(timer);
  }, []);

  // Simple dot animation using state changes instead of keyframes
  useEffect(() => {
    if (!loaded) return undefined; // Explicitly return undefined

    const interval = setInterval(() => {
      setDotOpacity((prev) => [prev[2], prev[0], prev[1]]);
    }, 500);

    return () => clearInterval(interval);
  }, [loaded]);

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
      <Box
        component="img"
        src="/logo/logo-full.svg"
        alt="Logo"
        sx={{
          width: { xs: 180, sm: 240, md: 280 },
          height: 'auto',
          transition: 'all 0.5s ease-in-out',
          transform: loaded ? 'scale(1)' : 'scale(0.8)',
          filter: 'drop-shadow(0 0 10px rgba(0, 60, 255, 0.2))',
        }}
      />

      {/* Add loading dots below the logo */}
      <Box
        sx={{
          mt: 4,
          display: 'flex',
          justifyContent: 'center',
          gap: 1,
        }}
      >
        {[0, 1, 2].map((i) => (
          <Box
            key={i}
            sx={{
              width: 12,
              height: 12,
              borderRadius: '50%',
              backgroundColor: '#70CDA7',
              opacity: dotOpacity[i],
              transition: 'opacity 0.3s ease',
            }}
          />
        ))}
      </Box>
    </Box>
  );

  if (portal) {
    return <Portal>{content}</Portal>;
  }

  return content;
}
