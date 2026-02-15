import { useState, useEffect, useCallback } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { useAuthContext } from 'src/auth/hooks';
import axios from 'src/utils/axios';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Typography from '@mui/material/Typography';
import CircularProgress from '@mui/material/CircularProgress';
import Button from '@mui/material/Button';
import Container from '@mui/material/Container';

import { CONFIG } from 'src/config-global';
import { Iconify } from 'src/components/iconify';
import checkIcon from '@iconify-icons/mdi/check';
import errorIcon from '@iconify-icons/mdi/error';

export default function ToolsetOAuthCallback() {
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [message, setMessage] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [searchParams] = useSearchParams();
  const { toolsetType } = useParams<{ toolsetType: string }>();
  const { user } = useAuthContext();

  // Close the popup window after successful authentication
  const closeWindow = useCallback(() => {
    // Try to close the popup window
    // If this is a popup opened by window.open(), window.close() will work
    // If not a popup (e.g., navigated directly), it may not close
    try {
      if (window.opener) {
        // Notify parent window that auth is complete
        window.opener.postMessage(
          { type: 'TOOLSET_OAUTH_SUCCESS', toolsetType },
          window.location.origin
        );
      }
      window.close();
    } catch (e) {
      // If window.close() fails (not a popup), the user can close manually
      console.log('Could not auto-close window:', e);
    }
  }, [toolsetType]);

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');

        // Validate toolset type from URL params
        if (!toolsetType) {
          throw new Error('No toolset type found in URL');
        }

        // Handle OAuth errors
        if (oauthError) {
          throw new Error(`OAuth error: ${oauthError}`);
        }

        if (!code) {
          throw new Error('No authorization code received');
        }

        if (!state) {
          throw new Error('No state parameter received');
        }

        setMessage('Processing OAuth authentication...');

        // Call Node.js backend to handle OAuth callback (same pattern as connectors)
        const response = await axios.get(
          `${CONFIG.backendUrl}/api/v1/toolsets/oauth/callback?code=${code}&state=${state}&error=${oauthError}`,
          {
            params: {
              base_url: window.location.origin,
            },
          }
        );

        // Check for success (either success flag or redirectUrl indicates success)
        if (response?.data?.success || response?.data?.redirectUrl || response?.data?.redirect_url) {
          setStatus('success');
          setMessage('OAuth authentication successful! This window will close automatically...');

          // Auto-close the popup window after a short delay
          setTimeout(() => {
            closeWindow();
          }, 1500);
          return;
        }

        // Default success handling - close the popup
        setStatus('success');
        setMessage('OAuth authentication successful! This window will close automatically...');

        setTimeout(() => {
          closeWindow();
        }, 1500);
      } catch (err) {
        setStatus('error');
        setError(err instanceof Error ? err.message : 'OAuth authentication failed');
        setMessage('OAuth authentication failed');
        
        // Notify parent window about the error
        try {
          if (window.opener) {
            window.opener.postMessage(
              { type: 'TOOLSET_OAUTH_ERROR', toolsetType, error: err instanceof Error ? err.message : 'OAuth failed' },
              window.location.origin
            );
          }
        } catch (e) {
          console.log('Could not notify parent window:', e);
        }
      }
    };

    handleCallback();
  }, [searchParams, user, toolsetType, closeWindow]);

  const handleCloseWindow = () => {
    closeWindow();
  };

  return (
    <Container maxWidth="sm" sx={{ py: 8 }}>
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '60vh',
          textAlign: 'center',
        }}
      >
        {status === 'processing' && (
          <>
            <CircularProgress size={60} sx={{ mb: 3 }} />
            <Typography variant="h5" sx={{ mb: 2, fontWeight: 600 }}>
              {message}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Please wait while we complete your authentication...
            </Typography>
          </>
        )}

        {status === 'success' && (
          <>
            <Box
              sx={{
                width: 80,
                height: 80,
                borderRadius: '50%',
                backgroundColor: 'success.main',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mb: 3,
              }}
            >
              <Iconify icon={checkIcon} width={40} height={40} color="white" />
            </Box>
            <Typography variant="h5" sx={{ mb: 2, fontWeight: 600, color: 'success.main' }}>
              Authentication Successful!
            </Typography>
            <Typography variant="body1" sx={{ mb: 3 }}>
              {message}
            </Typography>
            <Button variant="contained" onClick={handleCloseWindow} sx={{ mt: 2 }}>
              Close Window
            </Button>
          </>
        )}

        {status === 'error' && (
          <>
            <Box
              sx={{
                width: 80,
                height: 80,
                borderRadius: '50%',
                backgroundColor: 'error.main',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mb: 3,
              }}
            >
              <Iconify icon={errorIcon} width={40} height={40} color="white" />
            </Box>
            <Typography variant="h5" sx={{ mb: 2, fontWeight: 600, color: 'error.main' }}>
              Authentication Failed
            </Typography>
            <Alert severity="error" sx={{ mb: 3, textAlign: 'left' }}>
              {error}
            </Alert>
            <Button variant="contained" onClick={handleCloseWindow}>
              Close Window
            </Button>
          </>
        )}
      </Box>
    </Container>
  );
}
