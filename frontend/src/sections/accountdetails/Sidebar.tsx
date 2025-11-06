import cogIcon from '@iconify-icons/mdi/cog';
import robotIcon from '@iconify-icons/mdi/robot';
import React, { useState, useEffect } from 'react';
import upIcon from '@iconify-icons/mdi/chevron-up';
import accountIcon from '@iconify-icons/mdi/account';
import downIcon from '@iconify-icons/mdi/chevron-down';
import { useLocation, useNavigate } from 'react-router';
import shieldLockIcon from '@iconify-icons/mdi/shield-lock';
import linkVariantIcon from '@iconify-icons/mdi/link-variant';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import officeBuildingIcon from '@iconify-icons/mdi/office-building';
import accountServiceIcon from '@iconify-icons/mdi/account-service-outline';

import List from '@mui/material/List';
import Drawer from '@mui/material/Drawer';
import Divider from '@mui/material/Divider';
import ListItem from '@mui/material/ListItem';
import Collapse from '@mui/material/Collapse';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import { alpha, useTheme } from '@mui/material/styles';
import ListItemButton from '@mui/material/ListItemButton';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';

const drawerWidth = 240;

export default function Sidebar() {
  const theme = useTheme();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { user } = useAuthContext();
  const { isAdmin } = useAdmin();

  // Determine account type
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  // Base URL for routing depends on account type
  const baseUrl = isBusiness ? '/account/company-settings' : '/account/individual';

  // Check if current path is any settings path
  const isSettingsPath = pathname.includes(`${baseUrl}/settings/`);

  // Set settings open by default if we're on a settings page
  useEffect(() => {
    if (isSettingsPath) {
      setSettingsOpen(true);
    }
  }, [isSettingsPath]);

  // Toggle settings submenu
  const handleToggleSettings = () => {
    setSettingsOpen(!settingsOpen);
  };

  // Settings submenu items - common for both account types
  const settingsOptions = [
    {
      name: 'Authentication',
      icon: shieldLockIcon,
      path: `${baseUrl}/settings/authentication`,
    },
    {
      name: 'Connectors',
      icon: linkVariantIcon,
      path: `${baseUrl}/settings/connector`,
    },
    {
      name: 'Services',
      icon: accountServiceIcon,
      path: `${baseUrl}/settings/services`,
    },
    {
      name: 'AI Models',
      icon: robotIcon,
      path: `${baseUrl}/settings/ai-models`,
    },
    {
      name: 'Platform',
      icon: cogIcon,
      path: `${baseUrl}/settings/platform`,
    },
  ];


  return (
    <Drawer
      sx={{
        width: drawerWidth,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: drawerWidth,
          boxSizing: 'border-box',
          borderRight: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.mode === 'dark' 
            ? alpha(theme.palette.background.paper, 0.1)
            : theme.palette.background.paper,
        },
      }}
      variant="permanent"
      anchor="left"
    >
      {/* Show Business section only for business accounts */}
      {isBusiness && (
        <>
          <List sx={{ mt: 8 }}>
            <ListItem>
              <ListItemText 
                primary="COMPANY" 
                primaryTypographyProps={{ 
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  letterSpacing: '0.05em',
                  color: theme.palette.text.secondary,
                  marginBottom: '4px',
                }}
              />
            </ListItem>
            <ListItem disablePadding>
              <ListItemButton
                onClick={() => navigate(`${baseUrl}/profile`)}
                selected={pathname === `${baseUrl}/profile`}
                sx={{
                  py: 1,
                  borderRadius: '0',
                  '&.Mui-selected': {
                    bgcolor: theme.palette.mode === 'dark' 
                      ? alpha(theme.palette.primary.main, 0.15)
                      : alpha(theme.palette.primary.main, 0.08),
                    borderRight: `3px solid ${theme.palette.primary.main}`,
                    '&:hover': {
                      bgcolor: theme.palette.mode === 'dark' 
                        ? alpha(theme.palette.primary.main, 0.2)
                        : alpha(theme.palette.primary.main, 0.12),
                    },
                  },
                  '&:hover': {
                    bgcolor: theme.palette.mode === 'dark' 
                      ? alpha(theme.palette.action.hover, 0.1)
                      : alpha(theme.palette.action.hover, 0.05),
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
                  <Iconify icon={officeBuildingIcon} width={22} height={22} />
                </ListItemIcon>
                <ListItemText 
                  primary="Profile" 
                  primaryTypographyProps={{ 
                    fontSize: '0.9375rem',
                    fontWeight: pathname === `${baseUrl}/profile` ? 600 : 400,
                  }}
                />
              </ListItemButton>
            </ListItem>
            {isAdmin && (
              <>
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={() => navigate(`${baseUrl}/users`)}
                    selected={pathname === `${baseUrl}/users`}
                    sx={{
                      py: 1,
                      borderRadius: '0',
                      '&.Mui-selected': {
                        bgcolor: theme.palette.mode === 'dark' 
                          ? alpha(theme.palette.primary.main, 0.15)
                          : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor: theme.palette.mode === 'dark' 
                            ? alpha(theme.palette.primary.main, 0.2)
                            : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor: theme.palette.mode === 'dark' 
                          ? alpha(theme.palette.action.hover, 0.1)
                          : alpha(theme.palette.action.hover, 0.05),
                      },
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
                      <Iconify icon={accountGroupIcon} width={22} height={22} />
                    </ListItemIcon>
                    <ListItemText 
                      primary="Users & Groups" 
                      primaryTypographyProps={{ 
                        fontSize: '0.9375rem',
                        fontWeight: pathname === `${baseUrl}/users` ? 600 : 400,
                      }}
                    />
                  </ListItemButton>
                </ListItem>

                <ListItem disablePadding>
                  <ListItemButton
                    onClick={handleToggleSettings}
                    selected={isSettingsPath || settingsOpen}
                    sx={{
                      py: 1,
                      borderRadius: '0',
                      '&.Mui-selected': {
                        bgcolor: theme.palette.mode === 'dark' 
                          ? alpha(theme.palette.primary.main, 0.15)
                          : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor: theme.palette.mode === 'dark' 
                            ? alpha(theme.palette.primary.main, 0.2)
                            : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor: theme.palette.mode === 'dark' 
                          ? alpha(theme.palette.action.hover, 0.1)
                          : alpha(theme.palette.action.hover, 0.05),
                      },
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
                      <Iconify icon={cogIcon} width={22} height={22} />
                    </ListItemIcon>
                    <ListItemText 
                      primary="Settings" 
                      primaryTypographyProps={{ 
                        fontSize: '0.9375rem',
                        fontWeight: (isSettingsPath || settingsOpen) ? 600 : 400,
                      }}
                    />
                    <Iconify 
                      icon={settingsOpen ? upIcon : downIcon} 
                      width={18} 
                      height={18} 
                      sx={{ color: theme.palette.text.secondary }}
                    />
                  </ListItemButton>
                </ListItem>
                <Collapse in={settingsOpen} timeout="auto" unmountOnExit>
                  <List 
                    component="div" 
                    disablePadding
                    sx={{
                      bgcolor: theme.palette.mode === 'dark' 
                        ? alpha(theme.palette.background.default, 0.3)
                        : alpha(theme.palette.background.default, 0.5),
                    }}
                  >
                    {settingsOptions.map((option) => (
                      <ListItemButton
                        key={option.name}
                        sx={{ 
                          pl: 5,
                          py: 0.75,
                          '&.Mui-selected': {
                            bgcolor: theme.palette.mode === 'dark' 
                              ? alpha(theme.palette.primary.main, 0.15)
                              : alpha(theme.palette.primary.main, 0.08),
                            borderRight: `3px solid ${theme.palette.primary.main}`,
                            '&:hover': {
                              bgcolor: theme.palette.mode === 'dark' 
                                ? alpha(theme.palette.primary.main, 0.2)
                                : alpha(theme.palette.primary.main, 0.12),
                            },
                          },
                          '&:hover': {
                            bgcolor: theme.palette.mode === 'dark' 
                              ? alpha(theme.palette.action.hover, 0.1)
                              : alpha(theme.palette.action.hover, 0.05),
                          },
                        }}
                        onClick={() => navigate(option.path)}
                        selected={pathname === option.path}
                      >
                        <ListItemIcon sx={{ minWidth: 32, color: theme.palette.text.secondary }}>
                          <Iconify icon={option.icon} width={20} height={20} />
                        </ListItemIcon>
                        <ListItemText
                          primary={option.name}
                          primaryTypographyProps={{ 
                            fontSize: '0.875rem',
                            fontWeight: pathname === option.path ? 600 : 400,
                          }}
                        />
                      </ListItemButton>
                    ))}
                  </List>
                </Collapse>
              </>
            )}
          </List>
          <Divider sx={{ borderColor: theme.palette.divider }} />
        </>
      )}

      {/* Personal section - for both account types */}
      <List sx={{ mt: isBusiness ? 1 : 8 }}>
        <ListItem>
          <ListItemText 
            primary="PERSONAL" 
            primaryTypographyProps={{ 
              fontSize: '0.8125rem',
              fontWeight: 600,
              letterSpacing: '0.05em',
              color: theme.palette.text.secondary,
              marginBottom: '4px',
            }}
          />
        </ListItem>
        <ListItem disablePadding>
          <ListItemButton
            onClick={() =>
              navigate(isBusiness ? `${baseUrl}/personal-profile` : `${baseUrl}/profile`)
            }
            selected={
              pathname === (isBusiness ? `${baseUrl}/personal-profile` : `${baseUrl}/profile`)
            }
            sx={{
              py: 1,
              borderRadius: '0',
              '&.Mui-selected': {
                bgcolor: theme.palette.mode === 'dark' 
                  ? alpha(theme.palette.primary.main, 0.15)
                  : alpha(theme.palette.primary.main, 0.08),
                borderRight: `3px solid ${theme.palette.primary.main}`,
                '&:hover': {
                  bgcolor: theme.palette.mode === 'dark' 
                    ? alpha(theme.palette.primary.main, 0.2)
                    : alpha(theme.palette.primary.main, 0.12),
                },
              },
              '&:hover': {
                bgcolor: theme.palette.mode === 'dark' 
                  ? alpha(theme.palette.action.hover, 0.1)
                  : alpha(theme.palette.action.hover, 0.05),
              },
            }}
          >
            <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
              <Iconify icon={accountIcon} width={22} height={22} />
            </ListItemIcon>
            <ListItemText 
              primary="Profile" 
              primaryTypographyProps={{ 
                fontSize: '0.9375rem',
                fontWeight: pathname === (isBusiness ? `${baseUrl}/personal-profile` : `${baseUrl}/profile`) ? 600 : 400,
              }}
            />
          </ListItemButton>
        </ListItem>

        {/* For individual accounts, show settings in the personal section */}
        {!isBusiness && (
          <>
            <ListItem disablePadding>
              <ListItemButton
                onClick={handleToggleSettings}
                selected={isSettingsPath || settingsOpen}
                sx={{
                  py: 1,
                  borderRadius: '0',
                  '&.Mui-selected': {
                    bgcolor: theme.palette.mode === 'dark' 
                      ? alpha(theme.palette.primary.main, 0.15)
                      : alpha(theme.palette.primary.main, 0.08),
                    borderRight: `3px solid ${theme.palette.primary.main}`,
                    '&:hover': {
                      bgcolor: theme.palette.mode === 'dark' 
                        ? alpha(theme.palette.primary.main, 0.2)
                        : alpha(theme.palette.primary.main, 0.12),
                    },
                  },
                  '&:hover': {
                    bgcolor: theme.palette.mode === 'dark' 
                      ? alpha(theme.palette.action.hover, 0.1)
                      : alpha(theme.palette.action.hover, 0.05),
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
                  <Iconify icon={cogIcon} width={22} height={22} />
                </ListItemIcon>
                <ListItemText 
                  primary="Settings" 
                  primaryTypographyProps={{ 
                    fontSize: '0.9375rem',
                    fontWeight: (isSettingsPath || settingsOpen) ? 600 : 400,
                  }}
                />
                <Iconify 
                  icon={settingsOpen ? upIcon : downIcon} 
                  width={18} 
                  height={18} 
                  sx={{ color: theme.palette.text.secondary }}
                />
              </ListItemButton>
            </ListItem>
            <Collapse in={settingsOpen} timeout="auto" unmountOnExit>
              <List 
                component="div" 
                disablePadding
                sx={{
                  bgcolor: theme.palette.mode === 'dark' 
                    ? alpha(theme.palette.background.default, 0.3)
                    : alpha(theme.palette.background.default, 0.5),
                }}
              >
                {settingsOptions.map((option) => (
                  <ListItemButton
                    key={option.name}
                    sx={{ 
                      pl: 5,
                      py: 0.75,
                      '&.Mui-selected': {
                        bgcolor: theme.palette.mode === 'dark' 
                          ? alpha(theme.palette.primary.main, 0.15)
                          : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor: theme.palette.mode === 'dark' 
                            ? alpha(theme.palette.primary.main, 0.2)
                            : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor: theme.palette.mode === 'dark' 
                          ? alpha(theme.palette.action.hover, 0.1)
                          : alpha(theme.palette.action.hover, 0.05),
                      },
                    }}
                    onClick={() => navigate(option.path)}
                    selected={pathname === option.path}
                  >
                    <ListItemIcon sx={{ minWidth: 32, color: theme.palette.text.secondary }}>
                      <Iconify icon={option.icon} width={20} height={20} />
                    </ListItemIcon>
                    <ListItemText
                      primary={option.name}
                      primaryTypographyProps={{ 
                        fontSize: '0.875rem',
                        fontWeight: pathname === option.path ? 600 : 400,
                      }}
                    />
                  </ListItemButton>
                ))}
              </List>
            </Collapse>
          </>
        )}
      </List>
    </Drawer>
  );
}