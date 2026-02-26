import { useState, useEffect } from 'react';
import cogIcon from '@iconify-icons/mdi/cog';
import robotIcon from '@iconify-icons/mdi/robot';
import toolsIcon from '@iconify-icons/mdi/tools';
import emailIcon from '@iconify-icons/mdi/email';
import upIcon from '@iconify-icons/mdi/chevron-up';
import accountIcon from '@iconify-icons/mdi/account';
import keyLinkIcon from '@iconify-icons/mdi/key-link';
import downIcon from '@iconify-icons/mdi/chevron-down';
import codeTagsIcon from '@iconify-icons/mdi/code-tags';
import { useLocation, useNavigate } from 'react-router';
import shieldLockIcon from '@iconify-icons/mdi/shield-lock';
import linkVariantIcon from '@iconify-icons/mdi/link-variant';
import messageTextIcon from '@iconify-icons/mdi/message-text';
import accountGroupIcon from '@iconify-icons/mdi/account-group';
import officeBuildingIcon from '@iconify-icons/mdi/office-building';

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

const drawerWidth = 280;

export default function Sidebar() {
  const theme = useTheme();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [developerSettingsOpen, setDeveloperSettingsOpen] = useState(false);
  const { user } = useAuthContext();
  const { isAdmin } = useAdmin();

  // Determine account type
  const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';

  // Base URL for routing depends on account type
  const baseUrl = isBusiness ? '/account/company-settings' : '/account/individual';

  // Check if current path is a developer settings path
  const isDeveloperSettingsPath = pathname.includes(`${baseUrl}/settings/oauth2`);

  // Check if current path is any settings path (excluding developer settings)
  const isSettingsPath = pathname.includes(`${baseUrl}/settings/`) && !isDeveloperSettingsPath;

  // Set settings open by default if we're on a settings page
  useEffect(() => {
    if (isSettingsPath) {
      setSettingsOpen(true);
    }
  }, [isSettingsPath]);

  // Set developer settings open by default if we're on a developer settings page
  useEffect(() => {
    if (isDeveloperSettingsPath) {
      setDeveloperSettingsOpen(true);
    }
  }, [isDeveloperSettingsPath]);

  // Toggle settings submenu
  const handleToggleSettings = () => {
    setSettingsOpen(!settingsOpen);
  };

  // Toggle developer settings submenu
  const handleToggleDeveloperSettings = () => {
    setDeveloperSettingsOpen(!developerSettingsOpen);
  };

  // Settings submenu items - common for both account types
  const allSettingsOptions = [
    {
      name: 'Authentication',
      icon: shieldLockIcon,
      path: `${baseUrl}/settings/authentication`,
      adminOnly: true,
    },
    {
      name: 'Mail',
      icon: emailIcon,
      path: `${baseUrl}/settings/mail`,
    },
    {
      name: 'Connectors',
      icon: linkVariantIcon,
      path: `${baseUrl}/settings/connector`,
      adminOnly: false, // Available to all business users
    },
    {
      name: 'Toolsets',
      icon: toolsIcon,
      path: `${baseUrl}/settings/toolsets`,
      adminOnly: false, // Available to all business users
    },
    {
      name: 'AI Models',
      icon: robotIcon,
      path: `${baseUrl}/settings/ai-models`,
      adminOnly: true,
    },
     {
      name: 'Slack Bot',
      icon: accountServiceIcon,
      path: `${baseUrl}/settings/slack-bot`,
      adminOnly: true,
    },
    {
      name: 'Platform',
      icon: cogIcon,
      path: `${baseUrl}/settings/platform`,
      adminOnly: true,
    },
    {
      name: 'Prompts',
      icon: messageTextIcon,
      path: `${baseUrl}/settings/prompts`,
      adminOnly: true,
    }
  ];

  // Developer settings options
  const allDeveloperSettingsOptions = [
    {
      name: 'OAuth 2.0 Apps',
      icon: keyLinkIcon,
      path: `${baseUrl}/settings/oauth2`,
      adminOnly: true,
    },
  ];

  // Filter settings options based on admin status for business accounts
  // For individual accounts, show all options
  const settingsOptions = isBusiness
    ? allSettingsOptions.filter((option) => !option.adminOnly || isAdmin)
    : allSettingsOptions;

  const developerSettingsOptions = allDeveloperSettingsOptions.filter(
    (option) => !option.adminOnly || isAdmin
  );

  return (
    <Drawer
      sx={{
        width: drawerWidth,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: drawerWidth,
          boxSizing: 'border-box',
          borderRight: `1px solid ${theme.palette.divider}`,
          backgroundColor:
            theme.palette.mode === 'dark'
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
                    bgcolor:
                      theme.palette.mode === 'dark'
                        ? alpha(theme.palette.primary.main, 0.15)
                        : alpha(theme.palette.primary.main, 0.08),
                    borderRight: `3px solid ${theme.palette.primary.main}`,
                    '&:hover': {
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.primary.main, 0.2)
                          : alpha(theme.palette.primary.main, 0.12),
                    },
                  },
                  '&:hover': {
                    bgcolor:
                      theme.palette.mode === 'dark'
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
              <ListItem disablePadding>
                <ListItemButton
                  onClick={() => navigate(`${baseUrl}/users`)}
                  selected={pathname === `${baseUrl}/users`}
                  sx={{
                    py: 1,
                    borderRadius: '0',
                    '&.Mui-selected': {
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.primary.main, 0.15)
                          : alpha(theme.palette.primary.main, 0.08),
                      borderRight: `3px solid ${theme.palette.primary.main}`,
                      '&:hover': {
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.primary.main, 0.2)
                            : alpha(theme.palette.primary.main, 0.12),
                      },
                    },
                    '&:hover': {
                      bgcolor:
                        theme.palette.mode === 'dark'
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
            )}

            {/* Settings - visible to all business users, but options filtered by admin status */}
            {settingsOptions.length > 0 && (
              <>
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={handleToggleSettings}
                    selected={isSettingsPath || settingsOpen}
                    sx={{
                      py: 1,
                      borderRadius: '0',
                      '&.Mui-selected': {
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.primary.main, 0.15)
                            : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.primary.main, 0.2)
                              : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor:
                          theme.palette.mode === 'dark'
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
                        fontWeight: isSettingsPath || settingsOpen ? 600 : 400,
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
                      bgcolor:
                        theme.palette.mode === 'dark'
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
                            bgcolor:
                              theme.palette.mode === 'dark'
                                ? alpha(theme.palette.primary.main, 0.15)
                                : alpha(theme.palette.primary.main, 0.08),
                            borderRight: `3px solid ${theme.palette.primary.main}`,
                            '&:hover': {
                              bgcolor:
                                theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.primary.main, 0.2)
                                  : alpha(theme.palette.primary.main, 0.12),
                            },
                          },
                          '&:hover': {
                            bgcolor:
                              theme.palette.mode === 'dark'
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

            {/* Developer Settings - visible based on admin status */}
            {developerSettingsOptions.length > 0 && (
              <>
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={handleToggleDeveloperSettings}
                    selected={isDeveloperSettingsPath || developerSettingsOpen}
                    sx={{
                      py: 1,
                      borderRadius: '0',
                      '&.Mui-selected': {
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.primary.main, 0.15)
                            : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.primary.main, 0.2)
                              : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.action.hover, 0.1)
                            : alpha(theme.palette.action.hover, 0.05),
                      },
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
                      <Iconify icon={codeTagsIcon} width={22} height={22} />
                    </ListItemIcon>
                    <ListItemText
                      primary="Developer Settings"
                      sx={{ minWidth: 0, overflow: 'hidden' }}
                      primaryTypographyProps={{
                        fontSize: '0.9375rem',
                        fontWeight: isDeveloperSettingsPath || developerSettingsOpen ? 600 : 400,
                        noWrap: true,
                      }}
                    />
                    <Iconify
                      icon={developerSettingsOpen ? upIcon : downIcon}
                      sx={{ flexShrink: 0, color: theme.palette.text.secondary }}
                      width={18}
                      height={18}
                    />
                  </ListItemButton>
                </ListItem>
                <Collapse in={developerSettingsOpen} timeout="auto" unmountOnExit>
                  <List
                    component="div"
                    disablePadding
                    sx={{
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.background.default, 0.3)
                          : alpha(theme.palette.background.default, 0.5),
                    }}
                  >
                    {developerSettingsOptions.map((option) => (
                      <ListItemButton
                        key={option.name}
                        sx={{
                          pl: 5,
                          py: 0.75,
                          '&.Mui-selected': {
                            bgcolor:
                              theme.palette.mode === 'dark'
                                ? alpha(theme.palette.primary.main, 0.15)
                                : alpha(theme.palette.primary.main, 0.08),
                            borderRight: `3px solid ${theme.palette.primary.main}`,
                            '&:hover': {
                              bgcolor:
                                theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.primary.main, 0.2)
                                  : alpha(theme.palette.primary.main, 0.12),
                            },
                          },
                          '&:hover': {
                            bgcolor:
                              theme.palette.mode === 'dark'
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
                bgcolor:
                  theme.palette.mode === 'dark'
                    ? alpha(theme.palette.primary.main, 0.15)
                    : alpha(theme.palette.primary.main, 0.08),
                borderRight: `3px solid ${theme.palette.primary.main}`,
                '&:hover': {
                  bgcolor:
                    theme.palette.mode === 'dark'
                      ? alpha(theme.palette.primary.main, 0.2)
                      : alpha(theme.palette.primary.main, 0.12),
                },
              },
              '&:hover': {
                bgcolor:
                  theme.palette.mode === 'dark'
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
                fontWeight:
                  pathname === (isBusiness ? `${baseUrl}/personal-profile` : `${baseUrl}/profile`)
                    ? 600
                    : 400,
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
                    bgcolor:
                      theme.palette.mode === 'dark'
                        ? alpha(theme.palette.primary.main, 0.15)
                        : alpha(theme.palette.primary.main, 0.08),
                    borderRight: `3px solid ${theme.palette.primary.main}`,
                    '&:hover': {
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.primary.main, 0.2)
                          : alpha(theme.palette.primary.main, 0.12),
                    },
                  },
                  '&:hover': {
                    bgcolor:
                      theme.palette.mode === 'dark'
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
                    fontWeight: isSettingsPath || settingsOpen ? 600 : 400,
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
                  bgcolor:
                    theme.palette.mode === 'dark'
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
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.primary.main, 0.15)
                            : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.primary.main, 0.2)
                              : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor:
                          theme.palette.mode === 'dark'
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

            {/* Developer Settings for individual accounts */}
            {developerSettingsOptions.length > 0 && (
              <>
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={handleToggleDeveloperSettings}
                    selected={isDeveloperSettingsPath || developerSettingsOpen}
                    sx={{
                      py: 1,
                      borderRadius: '0',
                      '&.Mui-selected': {
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.primary.main, 0.15)
                            : alpha(theme.palette.primary.main, 0.08),
                        borderRight: `3px solid ${theme.palette.primary.main}`,
                        '&:hover': {
                          bgcolor:
                            theme.palette.mode === 'dark'
                              ? alpha(theme.palette.primary.main, 0.2)
                              : alpha(theme.palette.primary.main, 0.12),
                        },
                      },
                      '&:hover': {
                        bgcolor:
                          theme.palette.mode === 'dark'
                            ? alpha(theme.palette.action.hover, 0.1)
                            : alpha(theme.palette.action.hover, 0.05),
                      },
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 40, color: theme.palette.text.secondary }}>
                      <Iconify icon={codeTagsIcon} width={22} height={22} />
                    </ListItemIcon>
                    <ListItemText
                      primary="Developer Settings"
                      sx={{ minWidth: 0, overflow: 'hidden' }}
                      primaryTypographyProps={{
                        fontSize: '0.9375rem',
                        fontWeight: isDeveloperSettingsPath || developerSettingsOpen ? 600 : 400,
                        noWrap: true,
                      }}
                    />
                    <Iconify
                      icon={developerSettingsOpen ? upIcon : downIcon}
                      sx={{ flexShrink: 0, color: theme.palette.text.secondary }}
                      width={18}
                      height={18}
                    />
                  </ListItemButton>
                </ListItem>
                <Collapse in={developerSettingsOpen} timeout="auto" unmountOnExit>
                  <List
                    component="div"
                    disablePadding
                    sx={{
                      bgcolor:
                        theme.palette.mode === 'dark'
                          ? alpha(theme.palette.background.default, 0.3)
                          : alpha(theme.palette.background.default, 0.5),
                    }}
                  >
                    {developerSettingsOptions.map((option) => (
                      <ListItemButton
                        key={option.name}
                        sx={{
                          pl: 5,
                          py: 0.75,
                          '&.Mui-selected': {
                            bgcolor:
                              theme.palette.mode === 'dark'
                                ? alpha(theme.palette.primary.main, 0.15)
                                : alpha(theme.palette.primary.main, 0.08),
                            borderRight: `3px solid ${theme.palette.primary.main}`,
                            '&:hover': {
                              bgcolor:
                                theme.palette.mode === 'dark'
                                  ? alpha(theme.palette.primary.main, 0.2)
                                  : alpha(theme.palette.primary.main, 0.12),
                            },
                          },
                          '&:hover': {
                            bgcolor:
                              theme.palette.mode === 'dark'
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
          </>
        )}
      </List>
    </Drawer>
  );
}
