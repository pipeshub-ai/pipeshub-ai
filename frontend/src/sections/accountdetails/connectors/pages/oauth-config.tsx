/**
 * OAuth Configuration Page
 *
 * Features:
 * - My OAuth Apps: Manage created OAuth app configurations (landing page)
 * - OAuth Registry: Browse OAuth-enabled connector/tool types
 * - Create OAuth App: Create new OAuth configurations
 * - Multiple OAuth Apps: Support for multiple apps per connector type (feature flag)
 */

import React, { useState } from 'react';
import {
  Container,
  Box,
  alpha,
  useTheme,
  Button,
  Stack,
} from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import appsIcon from '@iconify-icons/mdi/apps';
import keyIcon from '@iconify-icons/mdi/key';
import OAuthRegistry from '../components/oauth/oauth-registry';
import MyOAuthApps from '../components/oauth/my-oauth-apps';

// ----------------------------------------------------------------------

/**
 * Main OAuth Configuration Component
 */
const OAuthConfig: React.FC = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const isDark = theme.palette.mode === 'dark';
  const [showRegistry, setShowRegistry] = useState(false);

  const handleShowRegistry = () => {
    setShowRegistry(true);
  };

  const handleBackToApps = () => {
    setShowRegistry(false);
  };

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      {showRegistry ? (
        <OAuthRegistry onBack={handleBackToApps} />
      ) : (
        <Box
          sx={{
            borderRadius: 2,
            backgroundColor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            overflow: 'hidden',
          }}
        >
          {/* Header Section */}
          <Box
            sx={{
              p: 3,
              borderBottom: `1px solid ${theme.palette.divider}`,
              backgroundColor: isDark
                ? alpha(theme.palette.background.default, 0.3)
                : alpha(theme.palette.grey[50], 0.5),
            }}
          >
            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Box
                  sx={{
                    width: 40,
                    height: 40,
                    borderRadius: 1.5,
                    backgroundColor: alpha(theme.palette.primary.main, 0.1),
                    border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Iconify
                    icon={keyIcon}
                    width={20}
                    height={20}
                    sx={{ color: theme.palette.primary.main }}
                  />
                </Box>
                <Box>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                    <h1
                      style={{
                        margin: 0,
                        fontSize: '1.5rem',
                        fontWeight: 700,
                        color: theme.palette.text.primary,
                      }}
                    >
                      OAuth Configuration
                    </h1>
                  </Box>
                  <p
                    style={{
                      margin: 0,
                      fontSize: '0.875rem',
                      color: theme.palette.text.secondary,
                    }}
                  >
                    Manage OAuth app configurations for connectors and toolsets
                  </p>
                </Box>
              </Stack>

              <Button
                variant="contained"
                color="primary"
                startIcon={<Iconify icon={appsIcon} width={18} height={18} />}
                onClick={handleShowRegistry}
                sx={{
                  textTransform: 'none',
                  fontWeight: 600,
                  borderRadius: 1.5,
                  px: 3,
                  height: 40,
                }}
              >
                Add OAuth App
              </Button>
            </Stack>
          </Box>

          {/* Content Section */}
          <Box sx={{ p: 3 }}>
            <MyOAuthApps />
          </Box>
        </Box>
      )}
    </Container>
  );
};

export default OAuthConfig;

