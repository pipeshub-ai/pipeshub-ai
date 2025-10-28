import type { BoxProps } from '@mui/material/Box';

import Box from '@mui/material/Box';
import { keyframes } from '@mui/material/styles';

import { Logo } from 'src/components/logo';

// ----------------------------------------------------------------------

const pulse = keyframes`
  0% {
    transform: scale(1);
    opacity: 1;
  }
  45% {
    transform: scale(1.25);
    opacity: 0.8;
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
`;

const halo = keyframes`
  0% {
    transform: scale(1);
    opacity: 0.35;
  }
  45% {
    transform: scale(1.6);
    opacity: 0;
  }
  100% {
    transform: scale(1.6);
    opacity: 0;
  }
`;

type LogoSpinnerProps = BoxProps & {
  size?: number;
};

export function LogoSpinner({ size = 72, sx, ...other }: LogoSpinnerProps) {
  return (
    <Box
      sx={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        '&::after': {
          content: '""',
          position: 'absolute',
          inset: 0,
          borderRadius: '50%',
          border: (theme) => `1px solid ${theme.palette.primary.main}`,
          animation: `${halo} 1.8s ease-in-out infinite`,
        },
        ...sx,
      }}
      {...other}
    >
      <Box
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '100%',
          height: '100%',
          color: 'primary.main',
          animation: `${pulse} 1.8s ease-in-out infinite`,
        }}
      >
        <Logo disableLink iconSize={size} variant="symbol" sx={{ color: 'inherit' }} />
      </Box>
    </Box>
  );
}

LogoSpinner.displayName = 'LogoSpinner';
