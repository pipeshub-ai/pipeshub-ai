import type { BoxProps } from '@mui/material/Box';
import type { Breakpoint } from '@mui/material/styles';

import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { alpha, useTheme } from '@mui/material/styles';

import { RouterLink } from 'src/routes/components';


// ----------------------------------------------------------------------

type SectionProps = BoxProps & {
  title?: string;
  method?: string;
  imgUrl?: string;
  videoUrl?: string;
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
  videoUrl = '/left-panel.mp4',
  subtitle,
  ...other
}: SectionProps) {
  const theme = useTheme();

  const mediaSx = {
    position: 'absolute' as const,
    inset: 0,
    width: 1,
    height: 1,
    objectFit: 'cover' as const,
  };

  const renderMedia = videoUrl ? (
    <Box
      component="video"
      src={videoUrl}
      autoPlay
      muted
      loop
      playsInline
      poster={imgUrl}
      aria-hidden
      sx={mediaSx}
    />
  ) : (
    <Box component="img" alt="Auth visual" src={imgUrl} sx={mediaSx} aria-hidden />
  );

  return (
    <Box
      sx={{
        display: 'none',
        position: 'relative',
        overflow: 'hidden',
        borderRadius: 3,
        minHeight: { xs: 0, [layoutQuery]: '100%' },
        backgroundColor: alpha(theme.palette.background.default, 0.3),
        [theme.breakpoints.up(layoutQuery)]: {
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          color: 'common.white',
          p: { md: theme.spacing(5), lg: theme.spacing(6) },
        },
        ...sx,
      }}
      {...other}
    >
      {renderMedia}

      <Box
        sx={{
          position: 'absolute',
          inset: 0,
          bgcolor: alpha(theme.palette.common.black, 0.48),
        }}
      />

      <Box
        sx={{
          position: 'relative',
          zIndex: 1,
          display: 'none',
          flexDirection: 'column',
          justifyContent: 'space-between',
          gap: 4,
          height: 1,
          [theme.breakpoints.up(layoutQuery)]: {
            display: 'flex',
          },
        }}
      >
        <Box>
          {subtitle && (
            <Typography variant="overline" sx={{ opacity: 0.64 }}>
              {subtitle}
            </Typography>
          )}

          <Typography
            variant="h3"
            sx={{
              mt: subtitle ? 2 : 0,
              fontWeight: 700,
              lineHeight: 1.15,
            }}
          >
            {title}
          </Typography>
        </Box>

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
    </Box>
  );
}
