import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams, useParams } from 'react-router-dom';
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

export default function ConnectorOAuthCallback() {
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [message, setMessage] = useState<string>('');
  const [error, setError] = useState<string>('');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { connectorName } = useParams<{ connectorName: string }>();
  const { user } = useAuthContext();

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');

        // Validate connector name from URL params
        if (!connectorName) {
          throw new Error('No connector name found in URL');
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

        // Call Node.js backend to handle OAuth callback
        const response = await axios.get(
          `${CONFIG.backendUrl}/api/v1/connectors/${connectorName}/oauth/callback?code=${code}&state=${state}&error=${oauthError}`,
          {
            params: {
              baseUrl: window.location.origin,
            },
          }
        );

        // Prefer JSON redirectUrl for client-side navigation (prevents CORS on redirects)
        if (response?.data?.redirectUrl) {
          const redirectUrl: string = response.data.redirectUrl;
          setStatus('success');
          setMessage('OAuth authentication successful! Redirecting...');
          setTimeout(() => {
            window.location.href = redirectUrl;
          }, 500);
          return;
        }

        // If not a JSON redirect, handle as normal response
        const responseData = response.data || {};

        // Success - redirect to connector settings page
        setStatus('success');
        setMessage('OAuth authentication successful! Redirecting to connector page...');

        // Determine redirect path based on account type
        const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';
        const basePath = isBusiness
          ? '/account/company-settings/settings/connector'
          : '/account/individual/settings/connector';
        const redirectPath = `${basePath}/${connectorName}`;

        // Redirect after a short delay
        setTimeout(() => {
          navigate(redirectPath, { replace: true });
        }, 2000);
      } catch (err) {
        setStatus('error');
        setError(err instanceof Error ? err.message : 'OAuth authentication failed');
        setMessage('OAuth authentication failed');
      }
    };

    handleCallback();
  }, [searchParams, navigate, user, connectorName]);

  const handleRetry = () => {
    // Redirect back to connector settings
    const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';
    const basePath = isBusiness
      ? '/account/company-settings/settings/connector'
      : '/account/individual/settings/connector';
    if (connectorName) {
      navigate(`${basePath}/${connectorName}`, { replace: true });
    } else {
      navigate(basePath, { replace: true });
    }
  };

  const handleGoToConnector = () => {
    if (connectorName) {
      const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';
      const basePath = isBusiness
        ? '/account/company-settings/settings/connector'
        : '/account/individual/settings/connector';
      navigate(`${basePath}/${connectorName}`, { replace: true });
    }
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
            {connectorName && (
              <Button variant="contained" onClick={handleGoToConnector} sx={{ mt: 2 }}>
                Go to {connectorName} Settings
              </Button>
            )}
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
            <Box sx={{ display: 'flex', gap: 2 }}>
              <Button variant="outlined" onClick={handleRetry}>
                Back to Connectors
              </Button>
              {connectorName && (
                <Button variant="contained" onClick={handleGoToConnector}>
                  Try Again
                </Button>
              )}
            </Box>
          </>
        )}
      </Box>
    </Container>
  );
}
