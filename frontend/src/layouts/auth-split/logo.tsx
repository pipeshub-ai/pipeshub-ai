import type { BoxProps } from '@mui/material/Box';

import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { alpha, useTheme } from '@mui/material/styles';

// ----------------------------------------------------------------------

export function AuthSplitLogo({ sx, ...other }: BoxProps) {
  const theme = useTheme();

  return (
    <Box
      sx={{
        display: 'inline-flex',
        flexDirection: 'column',
        gap: 0,
        color: theme.palette.common.white,
        textTransform: 'none',
        letterSpacing: theme.typography.caption.letterSpacing,
        ...sx,
      }}
      {...other}
    >
      <Typography
        variant="caption"
        sx={{
          fontWeight: 600,
          lineHeight: 1.25,
          textTransform: 'none',
        }}
      >
        Relationship
        <br />
        Intelligence
      </Typography>
    </Box>
  );
}
