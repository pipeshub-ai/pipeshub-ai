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
    const [imageError, setImageError] = useState(false);

    // Reset error state when logo changes
    useEffect(() => {
      if (logo) {
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

    const commonLogoStyles = {
      width: DEFAULT_SIZE,
      minWidth: DEFAULT_SIZE,
      height: DEFAULT_SIZE,
      minHeight: DEFAULT_SIZE,
      flexShrink: 0,
      objectFit: 'contain',
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
              ...commonLogoStyles,
              opacity: 0,
            }}
          />
        ) : (
          <Box
            component="img"
            src={shouldShowCustomLogo ? logo! : DEFAULT_LOGO_PATH}
            alt={shouldShowCustomLogo ? 'Organization Logo' : 'Pipeshub Logo'}
            onError={() => {
              if (shouldShowCustomLogo) {
                console.warn('[Logo] Failed to load custom logo, falling back to default');
                setImageError(true);
              }
            }}
            sx={commonLogoStyles}
          />
        )}
      </Box>
    );
  }
);
