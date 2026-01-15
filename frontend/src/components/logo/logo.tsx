import type { BoxProps } from '@mui/material/Box';

import { forwardRef, useState, useEffect } from 'react';

import Box from '@mui/material/Box';

import { RouterLink } from 'src/routes/components';

import { useWhiteLabel } from 'src/context/WhiteLabelContext';
import { logoClasses } from './classes';

// ----------------------------------------------------------------------

const DEFAULT_LOGO_PATH = '/logo/logo.svg';
const DEFAULT_SIZE = 40;

export type LogoProps = BoxProps & {
  href?: string;
  disableLink?: boolean;
};

export const Logo = forwardRef<HTMLDivElement, LogoProps>(
  ({ width, href = '/', height, disableLink = false, className, sx, ...other }, ref) => {
    const { logo, isWhiteLabeled, loading } = useWhiteLabel();
    const [imageLoaded, setImageLoaded] = useState(false);
    const [imageError, setImageError] = useState(false);

    // Reset states when logo changes
    useEffect(() => {
      if (logo) {
        setImageLoaded(false);
        setImageError(false);
      }
    }, [logo]);

    // Determine which logo to show
    const shouldShowCustomLogo = isWhiteLabeled && logo && !imageError;
    const shouldShowPlaceholder = isWhiteLabeled && loading && !logo;

    const baseSize = {
      width: width ?? DEFAULT_SIZE,
      height: height ?? DEFAULT_SIZE,
    };

    return (
      <Box
        ref={ref}
        component={RouterLink}
        href={href}
        className={logoClasses.root.concat(className ? ` ${className}` : '')}
        aria-label="Logo"
        sx={{
          ...baseSize,
          flexShrink: 0,
          display: 'inline-flex',
          verticalAlign: 'middle',
          ...(disableLink && { pointerEvents: 'none' }),
          ...sx,
        }}
        {...other}
      >
        {shouldShowPlaceholder ? (
          // Invisible placeholder while loading white-labeled logo
          <Box
            sx={{
              width: DEFAULT_SIZE,
              height: DEFAULT_SIZE,
              opacity: 0,
            }}
          />
        ) : shouldShowCustomLogo ? (
          // Custom organization logo
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 60,
              height: 30,
              cursor: 'pointer',
              position: 'relative',
            }}
          >
            <Box
              component="img"
              src={logo}
              alt="Organization Logo"
              onLoad={() => setImageLoaded(true)}
              onError={() => {
                console.warn('[Logo] Failed to load custom logo, falling back to default');
                setImageError(true);
              }}
              sx={{
                maxWidth: '100%',
                maxHeight: '100%',
                width: 'auto',
                height: 'auto',
                objectFit: 'contain',
                opacity: imageLoaded ? 1 : 0,
                transition: 'opacity 0.2s ease-in-out',
              }}
            />
            {!imageLoaded && !imageError && (
              // Placeholder while custom logo loads
              <Box
                component="img"
                src={DEFAULT_LOGO_PATH}
                alt="Loading"
                sx={{
                  position: 'absolute',
                  width: DEFAULT_SIZE,
                  height: DEFAULT_SIZE,
                  opacity: 0.3,
                }}
              />
            )}
          </Box>
        ) : (
          // Default Pipeshub logo
          <Box
            component="img"
            src={DEFAULT_LOGO_PATH}
            alt="Pipeshub Logo"
            sx={{
              width: DEFAULT_SIZE,
              height: DEFAULT_SIZE,
            }}
          />
        )}
      </Box>
    );
  }
);
