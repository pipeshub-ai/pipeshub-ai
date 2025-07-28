import type { BoxProps } from '@mui/material/Box';
import type { Breakpoint } from '@mui/material/styles';

import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import Tooltip from '@mui/material/Tooltip';
import { useTheme } from '@mui/material/styles';

import { RouterLink } from 'src/routes/components';

import { varAlpha, bgGradient } from 'src/theme/styles';

// ----------------------------------------------------------------------

type SectionProps = BoxProps & {
  title?: string;
  method?: string;
  imgUrl?: string;
  subtitle?: string;
  layoutQuery: Breakpoint;
  methods?: {
    path: string;
    icon: string;
    label: string;
  }[];
};

export function Section({
  sx,
  method,
  layoutQuery,
  methods,
  title = 'Manage the job',
  imgUrl = `/logo/welcomegif.gif`,
  subtitle = 'More effectively with optimized workflows.',
  ...other
}: SectionProps) {
  const theme = useTheme();

  return (
    <Box
      sx={{
        ...bgGradient({
          color: `0deg, ${varAlpha(theme.vars.palette.background.defaultChannel, 0.92)}, ${varAlpha(
            theme.vars.palette.background.defaultChannel,
            0.92
          )}`,
        }),
        maxWidth: 780,
        display: 'none',
        position: 'relative',
        backgroundColor: theme.palette.background.default, // Background color for the entire component
        width: '100%', // Take up full width
        height: '100%', // Take up full height
        minHeight: '100vh', // Ensure it takes at least the full viewport height
        overflow: 'hidden', // Prevent content from spilling outside the box
        [theme.breakpoints.up(layoutQuery)]: {
          display: 'flex',
          alignItems: 'flex-start', // Align to the left instead of center
          flexDirection: 'column',
          justifyContent: 'flex-start', // Start from the top
        },
        '&:before': {
          content: '""',
          position: 'absolute',
          left: 0,
          top: 0,
          width: '100%',
          height: '100%',
          backgroundColor: theme.palette.background.default,
          zIndex: -1,
        },
        ...sx,
      }}
      {...other}
    >
      <div>
        {/* <Typography variant="h3" sx={{ textAlign: 'center' }}>
          {title}
        </Typography> */}

        {/* {subtitle && (
          <Typography sx={{ color: 'text.secondary', textAlign: 'center', mt: 2 }}>
            {subtitle}
          </Typography>
        )} */}
      </div>

      <Box
        component="img"
        alt="Dashboard illustration"
        src={imgUrl}
        sx={{
          width: 'auto', // Auto width
          height: '100vh', // Full viewport height
          display: 'block', // Block display
          objectFit: 'contain', // Maintain aspect ratio
          position: 'fixed', // Fixed position relative to viewport
          top: 0, // Top of viewport
          left: 0, // Left side of viewport
          marginLeft: '0', // No margin on left
          marginRight: '0', // Auto margin on right
          zIndex: 0, // Higher z-index to keep above other elements
        }}
      />

      {!!methods?.length && method && (
        <Box component="ul" gap={2} display="flex">
          {methods.map((option) => {
            const selected = method === option.label.toLowerCase();

            return (
              <Box
                key={option.label}
                component="li"
                sx={{
                  ...(!selected && {
                    cursor: 'not-allowed',
                    filter: 'grayscale(1)',
                  }),
                }}
              >
                <Tooltip title={option.label} placement="top">
                  <Link
                    component={RouterLink}
                    href={option.path}
                    sx={{
                      ...(!selected && { pointerEvents: 'none' }),
                    }}
                  >
                    <Box
                      component="img"
                      alt={option.label}
                      src={option.icon}
                      sx={{ width: 32, height: 32 }}
                    />
                  </Link>
                </Tooltip>
              </Box>
            );
          })}
        </Box>
      )}
    </Box>
  );
}
