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
import ListItemButton from '@mui/material/ListItemButton';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';

const drawerWidth = 240;

export default function Sidebar() {
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
  const commonSettingsOptions = [
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
  ];

  // Business-specific settings options
  const businessSettingsOptions = [...commonSettingsOptions];

  // Use the appropriate settings options based on account type
  const settingsOptions = isBusiness ? businessSettingsOptions : commonSettingsOptions;

  return (
    <Drawer
      sx={{
        width: drawerWidth,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: drawerWidth,
          boxSizing: 'border-box',
        },
      }}
      variant="permanent"
      anchor="left"
    >
      {/* Show Business section only for business accounts */}
      {isBusiness && (
        <>
          <List sx={{ mt: 10 }}>
            <ListItem>
              <ListItemText primary="COMPANY" />
            </ListItem>
            <ListItem disablePadding>
              <ListItemButton
                onClick={() => navigate(`${baseUrl}/profile`)}
                selected={pathname === `${baseUrl}/profile`}
              >
                <ListItemIcon>
                  <Iconify icon={officeBuildingIcon} width="24" height="24" />
                </ListItemIcon>
                <ListItemText primary="Profile" />
              </ListItemButton>
            </ListItem>
            {isAdmin && (
              <>
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={() => navigate(`${baseUrl}/users`)}
                    selected={pathname === `${baseUrl}/users`}
                  >
                    <ListItemIcon>
                      <Iconify icon={accountGroupIcon} width="24" height="24" />
                    </ListItemIcon>
                    <ListItemText primary="Users & Groups" />
                  </ListItemButton>
                </ListItem>

                <ListItem disablePadding>
                  <ListItemButton
                    onClick={handleToggleSettings}
                    selected={isSettingsPath || settingsOpen}
                  >
                    <ListItemIcon>
                      <Iconify icon={cogIcon} width="24" height="24" />
                    </ListItemIcon>
                    <ListItemText primary="Settings" />
                    <Iconify icon={settingsOpen ? upIcon : downIcon} width="20" height="20" />
                  </ListItemButton>
                </ListItem>
                <Collapse in={settingsOpen} timeout="auto" unmountOnExit>
                  <List component="div" disablePadding>
                    {settingsOptions.map((option) => (
                      <ListItemButton
                        key={option.name}
                        sx={{ pl: 4 }}
                        onClick={() => navigate(option.path)}
                        selected={pathname === option.path}
                      >
                        <ListItemIcon sx={{ minWidth: 36 }}>
                          <Iconify icon={option.icon} width="20" height="20" />
                        </ListItemIcon>
                        <ListItemText
                          primary={option.name}
                          primaryTypographyProps={{ fontSize: '0.9rem' }}
                        />
                      </ListItemButton>
                    ))}
                  </List>
                </Collapse>
              </>
            )}
          </List>
          <Divider />
        </>
      )}

      {/* Personal section - for both account types */}
      <List sx={{ mt: isBusiness ? 0 : 10 }}>
        <ListItem>
          <ListItemText primary="PERSONAL" />
        </ListItem>
        <ListItem disablePadding>
          <ListItemButton
            onClick={() =>
              navigate(isBusiness ? `${baseUrl}/personal-profile` : `${baseUrl}/profile`)
            }
            selected={
              pathname === (isBusiness ? `${baseUrl}/personal-profile` : `${baseUrl}/profile`)
            }
          >
            <ListItemIcon>
              <Iconify icon={accountIcon} width="24" height="24" />
            </ListItemIcon>
            <ListItemText primary="Profile" />
          </ListItemButton>
        </ListItem>

        {/* For individual accounts, show settings in the personal section */}
        {!isBusiness && (
          <>
            <ListItem disablePadding>
              <ListItemButton
                onClick={handleToggleSettings}
                selected={isSettingsPath || settingsOpen}
              >
                <ListItemIcon>
                  <Iconify icon={cogIcon} width="24" height="24" />
                </ListItemIcon>
                <ListItemText primary="Settings" />
                <Iconify icon={settingsOpen ? upIcon : downIcon} width="20" height="20" />
              </ListItemButton>
            </ListItem>
            <Collapse in={settingsOpen} timeout="auto" unmountOnExit>
              <List component="div" disablePadding>
                {settingsOptions.map((option) => (
                  <ListItemButton
                    key={option.name}
                    sx={{ pl: 4 }}
                    onClick={() => navigate(option.path)}
                    selected={pathname === option.path}
                  >
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      <Iconify icon={option.icon} width="20" height="20" />
                    </ListItemIcon>
                    <ListItemText
                      primary={option.name}
                      primaryTypographyProps={{ fontSize: '0.9rem' }}
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
