import type { Icon as IconifyIcon } from '@iconify/react';

import { Icon  } from '@iconify/react';
import React, { useRef, useState, useEffect } from 'react';
import { PublicClientApplication } from '@azure/msal-browser';

import Button from '@mui/material/Button';
import { alpha } from '@mui/material/styles';
import CircularProgress from '@mui/material/CircularProgress';

// Define the interface for the config prop
interface MicrosoftLoginConfig {
  icon: React.ComponentProps<typeof IconifyIcon>['icon'];
  label: string;
  color: string;
}

// Define the interface for credential response
interface CredentialResponse {
  idToken: string;
  accessToken: string;
}

// Define the props interface for MicrosoftLoginButton
interface MicrosoftLoginButtonProps {
  config: MicrosoftLoginConfig;
  method: 'microsoft' | 'azureAd';
  clientId: string;
  authority: string;
  onSuccess: (response: { credential: CredentialResponse; method: string }) => Promise<void>;
  onError: (errorMessage: string) => void;
}

// Microsoft Login Button Component
function MicrosoftLoginButton({
  config,
  method,
  clientId,
  authority,
  onSuccess,
  onError,
}: MicrosoftLoginButtonProps) {
  const [isInitialized, setIsInitialized] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const msalInstance = useRef<PublicClientApplication | null>(null);

  // Initialize MSAL instance
  useEffect(() => {
    let mounted = true;

    const initializeMsal = async () => {
      try {
        const msalConfig = {
          auth: {
            clientId,
            authority,
            redirectUri: `${window.location.origin}/auth/microsoft/callback`,
          },
          cache: {
            cacheLocation: 'sessionStorage',
          },
          system: {
            loggerOptions: {
              loggerCallback: (level: number, message: string) => {
                if (level > 3) {
                  // Only log warnings & errors
                }
              },
              piiLoggingEnabled: false,
            },
          },
        };

        // Create a new instance
        const instance = new PublicClientApplication(msalConfig);

        // Initialize it before setting the ref and state
        await instance.initialize();

        // Only update state if component is still mounted
        if (mounted) {
          msalInstance.current = instance;
          setIsInitialized(true);
        }
      } catch (error) {
        if (mounted) {
          onError(`Failed to initialize authentication for ${method}. Please try again.`);
        }
      }
    };

    initializeMsal();

    // Cleanup function to prevent state updates on unmounted component
    return () => {
      mounted = false;
    };
  }, [clientId, authority, method, onError]);

  const handleLogin = async () => {
    if (!isInitialized || isLoading || !msalInstance.current) {
      onError(`Authentication service is not ready. Please try again.`);
      return;
    }

    setIsLoading(true);
    try {
      const response = await msalInstance.current.loginPopup({
        scopes: ['openid', 'profile', 'email'],
        prompt: 'select_account',
      });

      if (!response) {
        throw new Error(`Error while signing in with ${method}`);
      }

      // Use account home_account_id as idToken if not present in response
      const idToken = response.idToken || response.account?.homeAccountId || '';
      const accessToken = response.accessToken || '';

      await onSuccess({
        credential: { idToken, accessToken },
        method,
      });
    } catch (error) {
      onError(`${method} login failed. Please try again.`);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Button
      fullWidth
      size="large"
      variant="outlined"
      startIcon={
        isLoading ? <CircularProgress size={20} color="inherit" /> : <Icon icon={config.icon} />
      }
      onClick={handleLogin}
      disabled={!isInitialized || isLoading}
      sx={{
        height: 48,
        color: 'text.secondary',
        borderColor: 'divider',
        textTransform: 'none',
        fontWeight: 500,
        borderRadius: 1.5,
        '&:hover': {
          borderColor: config.color,
          color: config.color,
          bgcolor: (theme) => alpha(config.color, 0.08),
        },
      }}
    >
      {isLoading ? 'Signing in...' : config.label}
    </Button>
  );
}

export default MicrosoftLoginButton;
