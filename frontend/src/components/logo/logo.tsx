import type { BoxProps } from '@mui/material/Box';

import { useId, forwardRef, useState, useEffect } from 'react';

import Box from '@mui/material/Box';

import { RouterLink } from 'src/routes/components';

import { getOrgIdFromToken, getOrgLogo } from 'src/sections/accountdetails/utils';
import { useAuthContext } from 'src/auth/hooks';
import { logoClasses } from './classes';

// ----------------------------------------------------------------------

export type LogoProps = BoxProps & {
  href?: string;
  isSingle?: boolean;
  disableLink?: boolean;
};

export const Logo = forwardRef<HTMLDivElement, LogoProps>(
  (
    { width, href = '/', height, isSingle = true, disableLink = false, className, sx, ...other },
    ref
  ) => {
    const { user } = useAuthContext();

    const [customLogo, setCustomLogo] = useState<string | null>('');
    const isBusiness =
      user?.accountType === 'business' ||
      user?.accountType === 'organization' ||
      user?.role === 'business';
    useEffect(() => {
      const fetchLogo = async () => {
        try {
          const orgId = await getOrgIdFromToken();
          if (isBusiness) {
            const logoUrl = await getOrgLogo(orgId);
            setCustomLogo(logoUrl);
          }
        } catch (err) {
          console.error(err, 'error in fetching logo');
        }
      };

      fetchLogo();
    }, [isBusiness]);

    const baseSize = {
      width: width ?? 40,
      height: height ?? 40,
      ...(!isSingle && {
        width: width ?? 102,
        height: height ?? 36,
      }),
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
        {customLogo ? (
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
              src={customLogo}
              alt="Logo"
              sx={{
                maxWidth: '100%',
                maxHeight: '100%',
                width: 'auto',
                height: 'auto',
                objectFit: 'contain',
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
              }}
            />
          </Box>
        ) : (
          <Box
            component="img"
            src="/logo/logo.svg"
            alt="Logo"
            sx={{
              display: 'inline-flex',
              width: 40,
              height: 40,
              cursor: 'pointer',
            }}
          />
        )}
      </Box>
    );
  }
);
