import { useState } from 'react';
import keyIcon from '@iconify-icons/mdi/key';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import LoadingButton from '@mui/lab/LoadingButton';

import { Iconify } from 'src/components/iconify';

interface OAuthSignInProps {
  email: string;
  authConfig: {
    providerName?: string;
    authorizationUrl?: string;
    clientId: string;
    scope?: string;
    redirectUri?: string;
  };
  onSuccess?: (credentials: { accessToken?: string; idToken?: string }) => void;
  onError?: (error: string) => void;
}

export default function OAuthSignIn({ 
  email, 
  authConfig, 
  onSuccess, 
  onError 
}: OAuthSignInProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const {
    providerName = 'OAuth Provider',
    authorizationUrl,
    clientId,
    scope = 'openid email profile',
    redirectUri = `${window.location.origin}/auth/oauth/callback`,
  } = authConfig;

  const handleOAuthLogin = async () => {
    setLoading(true);
    setError('');

    try {
      if (!authorizationUrl) {
        throw new Error('Authorization URL not configured for this OAuth provider');
      }

      // Create OAuth authorization URL
      const params = new URLSearchParams({
        client_id: clientId,
        response_type: 'code',
        scope,
        redirect_uri: redirectUri,
        state: btoa(JSON.stringify({ email, provider: providerName })),
      });

      const oauthUrl = `${authorizationUrl}?${params.toString()}`;

      // Open OAuth flow in popup
      const popupWidth = 500;
      const popupHeight = 600;
      const left = window.screen.width / 2 - popupWidth / 2;
      const top = window.screen.height / 2 - popupHeight / 2;

      const popup = window.open(
        oauthUrl,
        `${providerName} Sign In`,
        `width=${popupWidth},height=${popupHeight},top=${top},left=${left},resizable=no,scrollbars=yes,status=no`
      );

      if (!popup) {
        throw new Error('Popup blocked! Please enable popups for this site.');
      }

      // Listen for messages from the popup
      const handleMessage = (event: MessageEvent) => {
        if (event.origin !== window.location.origin) {
          return;
        }

        if (event.data.type === 'OAUTH_SUCCESS') {
          const { accessToken, idToken } = event.data;
          window.removeEventListener('message', handleMessage);
          popup.close();
          
          if (onSuccess) {
            onSuccess({ accessToken, idToken });
          }
          setLoading(false);
        } else if (event.data.type === 'OAUTH_ERROR') {
          window.removeEventListener('message', handleMessage);
          popup.close();
          setError(event.data.error || 'OAuth authentication failed');
          if (onError) {
            onError(event.data.error || 'OAuth authentication failed');
          }
          setLoading(false);
        }
      };

      window.addEventListener('message', handleMessage);

      // Monitor popup for closure
      const checkPopupClosed = setInterval(() => {
        if (popup.closed) {
          clearInterval(checkPopupClosed);
          window.removeEventListener('message', handleMessage);
          if (loading) {
            setError('OAuth authentication was cancelled');
            setLoading(false);
          }
        }
      }, 1000);

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to initialize OAuth sign-in');
      if (onError) {
        onError(err instanceof Error ? err.message : 'Failed to initialize OAuth sign-in');
      }
      setLoading(false);
    }
  };

  return (
    <Box>
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      <LoadingButton
        fullWidth
        size="large"
        variant="outlined"
        loading={loading}
        onClick={handleOAuthLogin}
        startIcon={<Iconify icon={keyIcon} />}
      >
        Continue with {providerName}
      </LoadingButton>
    </Box>
  );
}