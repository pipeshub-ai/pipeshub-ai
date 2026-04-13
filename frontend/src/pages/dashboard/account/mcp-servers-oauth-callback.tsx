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

export default function McpServersOAuthCallback() {
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [message, setMessage] = useState<string>('');
  const [error, setError] = useState<string>('');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { serverType } = useParams<{ serverType: string }>();
  const { user } = useAuthContext();

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');

        if (!serverType) {
          throw new Error('No MCP server type found in URL');
        }

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

        const response = await axios.get(
          `${CONFIG.backendUrl}/api/v1/mcp-servers/oauth/callback`,
          {
            params: {
              code,
              state,
              error: oauthError,
              baseUrl: window.location.origin,
            },
          }
        );

        if (response?.data?.redirectUrl) {
          setStatus('success');
          setMessage('OAuth authentication successful! Redirecting...');
          setTimeout(() => {
            window.location.href = response.data.redirectUrl;
          }, 500);
          return;
        }

        setStatus('success');
        setMessage('OAuth authentication successful! Redirecting to MCP Servers...');

        const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';
        const basePath = isBusiness
          ? '/account/company-settings/settings/mcp-servers'
          : '/account/individual/settings/mcp-servers';

        setTimeout(() => {
          navigate(`${basePath}?tab=my-servers`, { replace: true });
        }, 2000);
      } catch (err) {
        setStatus('error');
        setError(err instanceof Error ? err.message : 'OAuth authentication failed');
        setMessage('OAuth authentication failed');
      }
    };

    handleCallback();
  }, [searchParams, navigate, user, serverType]);

  const handleGoToMcpServers = () => {
    const isBusiness = user?.accountType === 'business' || user?.accountType === 'organization';
    const basePath = isBusiness
      ? '/account/company-settings/settings/mcp-servers'
      : '/account/individual/settings/mcp-servers';
    navigate(`${basePath}?tab=my-servers`, { replace: true });
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
            <Button variant="contained" onClick={handleGoToMcpServers} sx={{ mt: 2 }}>
              Go to MCP Servers
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
            <Button variant="outlined" onClick={handleGoToMcpServers}>
              Back to MCP Servers
            </Button>
          </>
        )}
      </Box>
    </Container>
  );
}
