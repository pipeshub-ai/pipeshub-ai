import { useState, useEffect } from 'react';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Typography from '@mui/material/Typography';
import CircularProgress from '@mui/material/CircularProgress';

import { CONFIG } from 'src/config-global';

export default function GoogleWorkspaceOAuthCallback() {
  const [error, setError] = useState<string>('');
  const [processing, setProcessing] = useState(true);

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        const state = urlParams.get('state');
        const oauthError = urlParams.get('error');

        if (oauthError) {
          throw new Error(`OAuth error: ${oauthError}`);
        }

        if (!code) {
          throw new Error('No authorization code received');
        }

        if (!state) {
          throw new Error('No state parameter received');
        }

        // Parse and validate state parameter
        let stateData;
        try {
          stateData = JSON.parse(atob(state));
        } catch {
          throw new Error('Invalid state parameter');
        }

        const { service, timestamp } = stateData;
        
        if (!service || service !== 'googleWorkspace') {
          throw new Error('Invalid service in state data');
        }

        // Check if the state is not too old (5 minutes)
        const now = Date.now();
        if (now - timestamp > 5 * 60 * 1000) {
          throw new Error('OAuth state has expired. Please try again.');
        }

        // Exchange code for tokens using the backend
        const requestBody = {
          tempCode: code,
        };

        const response = await fetch(`${CONFIG.backendUrl}/api/v1/connectors/getTokenFromCode`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('accessToken')}`,
          },
          body: JSON.stringify(requestBody),
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Token exchange failed: ${errorText}`);
        }

        const result = await response.json();

        // Send success message to parent window
        if (window.opener) {
          window.opener.postMessage({
            type: 'GOOGLE_WORKSPACE_OAUTH_SUCCESS',
            data: result,
          }, window.location.origin);
        }

        // Close the popup
        window.close();

      } catch (err) {
        console.error('OAuth callback error:', err);
        setError(err instanceof Error ? err.message : 'OAuth authentication failed');
        
        // Send error message to parent window
        if (window.opener) {
          window.opener.postMessage({
            type: 'GOOGLE_WORKSPACE_OAUTH_ERROR',
            error: err instanceof Error ? err.message : 'OAuth authentication failed',
          }, window.location.origin);
        }

        // Close the popup after a short delay
        setTimeout(() => {
          window.close();
        }, 3000);
      } finally {
        setProcessing(false);
      }
    };

    handleCallback();
  }, []);

  if (processing) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          p: 3,
        }}
      >
        <CircularProgress size={48} sx={{ mb: 2 }} />
        <Typography variant="h6" sx={{ mb: 1 }}>
          Processing OAuth authentication...
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Please wait while we complete the authorization process.
        </Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          p: 3,
        }}
      >
        <Alert severity="error" sx={{ mb: 2, maxWidth: 400 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            OAuth Authentication Failed
          </Typography>
          <Typography variant="body2">
            {error}
          </Typography>
        </Alert>
        <Typography variant="body2" color="text.secondary">
          This window will close automatically.
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
        minHeight: '100vh',
        p: 3,
      }}
    >
      <CircularProgress size={48} sx={{ mb: 2 }} />
      <Typography variant="h6" sx={{ mb: 1 }}>
        Authorization Successful!
      </Typography>
      <Typography variant="body2" color="text.secondary">
        You can now close this window.
      </Typography>
    </Box>
  );
}
