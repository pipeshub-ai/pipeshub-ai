import type { NavSectionProps } from 'src/components/nav-section';
import type { Theme, SxProps, Breakpoint } from '@mui/material/styles';

import { useNavigate } from 'react-router';
import { useState, useEffect } from 'react';
import githubIcon from '@iconify-icons/mdi/github';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import { useTheme } from '@mui/material/styles';
import IconButton, { iconButtonClasses } from '@mui/material/IconButton';

import { useBoolean } from 'src/hooks/use-boolean';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';
import { useSettingsContext } from 'src/components/settings';

import { getOrgLogo, getOrgIdFromToken } from 'src/sections/accountdetails/utils';

import { useAuthContext } from 'src/auth/hooks';

import { Main } from './main';
import { NavMobile } from './nav-mobile';
import { layoutClasses } from '../classes';
import { NavHorizontal } from './nav-horizontal';
import { useAccountMenu } from '../config-nav-account'; // Import the hook instead of the static array

import { MenuButton } from '../components/menu-button';
import { LayoutSection } from '../core/layout-section';
import { HeaderSection } from '../core/header-section';
import { StyledDivider, useNavColorVars } from './styles';
import { AccountDrawer } from '../components/account-drawer';
import { getDashboardNavData } from '../config-nav-dashboard';
import {ThemeToggleButton } from '../components/theme-toggle-button';
   
// ----------------------------------------------------------------------

export type DashboardLayoutProps = {
  sx?: SxProps<Theme>;
  children: React.ReactNode;
  header?: {
    sx?: SxProps<Theme>;
  };
  data?: {
    nav?: NavSectionProps['data'];
  };
};

export function DashboardLayout({ sx, children, header, data }: DashboardLayoutProps) {
  const theme = useTheme();
  const mobileNavOpen = useBoolean();
  const { user } = useAuthContext();
  const { isAdmin } = useAdmin();
  const settings = useSettingsContext();
  const navColorVars = useNavColorVars(theme, settings);
  const layoutQuery: Breakpoint = 'sm';
  const dynamicNavData = getDashboardNavData(user?.accountType, isAdmin);

  const navData = data?.nav ?? dynamicNavData;
  const navigate = useNavigate();

  // Get dynamic account menu items
  const accountMenuItems = useAccountMenu();

  const isNavMini = settings.navLayout === 'mini';
  const isNavHorizontal = settings.navLayout === 'horizontal';
  const isNavVertical = isNavMini || settings.navLayout === 'vertical';
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

  return (
    <LayoutSection
      /** **************************************
       * Header
       *************************************** */
      headerSection={
        <HeaderSection
          layoutQuery={layoutQuery}
          disableElevation={isNavVertical}
          slotProps={{
            toolbar: {
              sx: {
                ...(isNavHorizontal && {
                  bgcolor: 'var(--layout-nav-bg)',
                  [`& .${iconButtonClasses.root}`]: {
                    color: 'var(--layout-nav-text-secondary-color)',
                  },
                  [theme.breakpoints.up(layoutQuery)]: {
                    height: 'var(--layout-nav-horizontal-height)',
                  },
                   borderBottom: `1px solid ${theme.palette.divider}`,
                boxShadow: theme.shadows[1],
                }),
              },
            },
            container: {
              maxWidth: false,
            },
          }}
          sx={header?.sx}
          slots={{
            topArea: (
              <Alert severity="info" sx={{ display: 'none', borderRadius: 0 }}>
                This is an info Alert.
              </Alert>
            ),

            leftArea: (
              <>
                {/* -- Nav mobile -- */}
                <MenuButton
                  onClick={mobileNavOpen.onTrue}
                  sx={{
                    mr: 1,
                    ml: -1,
                    [theme.breakpoints.up(layoutQuery)]: { display: 'none' },
                  }}
                />
                <NavMobile
                  data={navData}
                  open={mobileNavOpen.value}
                  onClose={mobileNavOpen.onFalse}
                  cssVars={navColorVars.section}
                />
                {/* -- Logo -- */}
                {isNavHorizontal && customLogo ? (
                  <Box
                    onClick={() => navigate('/')}
                    sx={{
                      display: 'none',
                      [theme.breakpoints.up(layoutQuery)]: {
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      },
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
                    onClick={() => navigate('/')}
                    src="/logo/logo-blue.svg"
                    alt="Logo"
                    sx={{
                      display: 'none',
                      [theme.breakpoints.up(layoutQuery)]: {
                        display: 'inline-flex',
                      },
                      width: 60,
                      height: 30,
                      cursor: 'pointer',
                    }}
                  />
                )}
                {/* -- Divider -- */}
                {isNavHorizontal && (
                  <StyledDivider
                    sx={{ [theme.breakpoints.up(layoutQuery)]: { display: 'flex' } }}
                  />
                )}
                {isNavHorizontal && (
                  <NavHorizontal
                    data={navData}
                    layoutQuery={layoutQuery}
                    cssVars={navColorVars.section}
                  />
                )}
              </>
            ),
            rightArea: (
              <Box display="flex" alignItems="center" gap={{ xs: 0, sm: 0.75 }}>
                <IconButton
                  component="a"
                  href="https://github.com/pipeshub-ai/pipeshub-ai"
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="GitHub Repository"
                  size="large"
                >
                  <Iconify
                    icon={githubIcon}
                    color={theme.palette.mode === 'dark' ? 'white' : 'black'}
                    width={30}
                    height={30}
                  />
                </IconButton>
                {/* task center remaining  */}
                <ThemeToggleButton />
                {/* Pass the dynamic account menu items instead of static _account */}
                <AccountDrawer data={accountMenuItems} />
              </Box>
            ),
          }}
        />
      }
      /** **************************************
       * Footer
       *************************************** */
      footerSection={null}
      /** **************************************
       * Style
       *************************************** */
      cssVars={{
        ...navColorVars.layout,
        '--layout-transition-easing': 'linear',
        '--layout-transition-duration': '120ms',
        '--layout-nav-mini-width': '88px',
        '--layout-nav-vertical-width': '300px',
        '--layout-nav-horizontal-height': '64px',
        '--layout-dashboard-content-pt': theme.spacing(1),
        '--layout-dashboard-content-pb': theme.spacing(8),
        '--layout-dashboard-content-px': theme.spacing(5),
      }}
      sx={{
        [`& .${layoutClasses.hasSidebar}`]: {
          [theme.breakpoints.up(layoutQuery)]: {
            transition: theme.transitions.create(['padding-left'], {
              easing: 'var(--layout-transition-easing)',
              duration: 'var(--layout-transition-duration)',
            }),
            pl: isNavMini ? 'var(--layout-nav-mini-width)' : 'var(--layout-nav-vertical-width)',
          },
        },
        ...sx,
      }}
    >
      <Main isNavHorizontal={isNavHorizontal}>{children}</Main>
    </LayoutSection>
  );
}
