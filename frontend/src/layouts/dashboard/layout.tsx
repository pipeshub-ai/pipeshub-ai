import type { NavSectionProps } from 'src/components/nav-section';
import type { Theme, SxProps, Breakpoint } from '@mui/material/styles';

import { useNavigate } from 'react-router';
import { useState, useEffect } from 'react';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import { useTheme } from '@mui/material/styles';
import { iconButtonClasses } from '@mui/material/IconButton';

import { useBoolean } from 'src/hooks/use-boolean';

import { useSettingsContext } from 'src/components/settings';

import { getOrgLogo, getOrgIdFromToken } from 'src/sections/accountdetails/utils';

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
import { SettingsButton } from '../components/settings-button';
import { navData as dashboardNavData } from '../config-nav-dashboard';

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
  const settings = useSettingsContext();
  const navColorVars = useNavColorVars(theme, settings);
  const layoutQuery: Breakpoint = 'sm';
  const navData = data?.nav ?? dashboardNavData;
  const navigate = useNavigate();

  // Get dynamic account menu items
  const accountMenuItems = useAccountMenu();

  const isNavMini = settings.navLayout === 'mini';
  const isNavHorizontal = settings.navLayout === 'horizontal';
  const isNavVertical = isNavMini || settings.navLayout === 'vertical';
  const [customLogo, setCustomLogo] = useState<string | null>('');
  useEffect(() => {
    const fetchLogo = async () => {
      try {
        const orgId = await getOrgIdFromToken();
        const logoUrl = await getOrgLogo(orgId);
        setCustomLogo(logoUrl);
      } catch (err) {
        console.error(err, 'error in fetching logo');
      }
    };

    fetchLogo();
  }, []);

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
                {isNavHorizontal &&
                  (customLogo ? (
                    <Box
                      component="img"
                      onClick={() => navigate('/')}
                      src={customLogo}
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
                  ))}
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
                {/* task center remaining  */}
                <SettingsButton />
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
