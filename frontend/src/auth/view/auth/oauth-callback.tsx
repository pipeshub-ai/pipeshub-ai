import { useState, useEffect, useRef } from 'react';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Typography from '@mui/material/Typography';
import CircularProgress from '@mui/material/CircularProgress';

import { CONFIG } from 'src/config-global';

export default function OAuthCallback() {
  const [error, setError] = useState<string>('');
  const [processing, setProcessing] = useState(true);


  const hasExchanged = useRef(false); 

  useEffect(() => {
    const handleCallback = async () => {
      if (hasExchanged.current) return;
      hasExchanged.current = true; 

      try {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        const state = urlParams.get('state');
        const oauthError = urlParams.get('error');

        if (oauthError) throw new Error(`OAuth error: ${oauthError}`);
        if (!code) throw new Error('No authorization code received');
        if (!state) throw new Error('No state parameter received');

        let stateData;
        try {
          stateData = JSON.parse(atob(state));
        } catch {
          throw new Error('Invalid state parameter');
        }

        const { email, provider } = stateData;

        const requestBody = {
          code,
          email, 
          provider,
          redirectUri: `${window.location.origin}/auth/oauth/callback`,
        };

        const response = await fetch(`${CONFIG.backendUrl}/api/v1/userAccount/oauth/exchange`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.message || `Exchange failed: ${response.status}`);
        }

        const tokens = await response.json();

        if (window.opener) {
          window.opener.postMessage({
            type: 'OAUTH_SUCCESS',
            accessToken: tokens.access_token,
          }, window.location.origin);
        }

        window.close();

      } catch (err) {
        setError(err instanceof Error ? err.message : 'OAuth authentication failed');
        setProcessing(false);

        if (window.opener) {
          window.opener.postMessage({
            type: 'OAUTH_ERROR',
            error: err instanceof Error ? err.message : 'OAuth authentication failed',
          }, window.location.origin);
        }
      }
    };

    handleCallback();
  }, []); 

  if (error) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          p: 3
        }}
      >
        <Alert severity="error" sx={{ mb: 2, maxWidth: 400 }}>
          {error}
        </Alert>
        <Typography variant="body2" color="text.secondary">
          You can close this window
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh'
      }}
    >
      <CircularProgress size={40} sx={{ mb: 2 }} />
      <Typography variant="body1">
        Processing OAuth authentication...
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        Please wait while we complete your sign-in
      </Typography>
    </Box>
  );
}