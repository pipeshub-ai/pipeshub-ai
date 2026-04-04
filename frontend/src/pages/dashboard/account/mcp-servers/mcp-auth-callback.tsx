import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Container from '@mui/material/Container';
import Typography from '@mui/material/Typography';
import CircularProgress from '@mui/material/CircularProgress';

import checkIcon from '@iconify-icons/mdi/check';
import errorIcon from '@iconify-icons/mdi/error';

import axios from 'src/utils/axios';
import { Iconify } from 'src/components/iconify';

export default function McpAuthCallback() {
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [error, setError] = useState('');
  const [searchParams] = useSearchParams();

  const closeWindow = useCallback(() => {
    setTimeout(() => {
      window.close();
    }, 2000);
  }, []);

  useEffect(() => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');
    const oauthError = searchParams.get('error');

    if (oauthError) {
      setStatus('error');
      setError(oauthError);
      window.opener?.postMessage(
        { type: 'MCP_OAUTH_ERROR', error: oauthError },
        window.location.origin
      );
      return;
    }

    if (!code || !state) {
      setStatus('error');
      setError('Missing authorization code or state parameter');
      window.opener?.postMessage(
        { type: 'MCP_OAUTH_ERROR', error: 'Missing authorization code or state parameter' },
        window.location.origin
      );
      return;
    }

    const exchangeCode = async () => {
      try {
        const response = await axios.post('/api/v1/mcp/auth/callback', { code, state });
        const { mcpServerUrl } = response.data;

        setStatus('success');
        window.opener?.postMessage(
          { type: 'MCP_OAUTH_SUCCESS', mcpServerUrl },
          window.location.origin
        );
        closeWindow();
      } catch (err: any) {
        const msg =
          err?.response?.data?.message || err?.message || 'Token exchange failed';
        setStatus('error');
        setError(msg);
        window.opener?.postMessage(
          { type: 'MCP_OAUTH_ERROR', error: msg },
          window.location.origin
        );
      }
    };

    exchangeCode();
  }, [searchParams, closeWindow]);

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
              Processing MCP authentication...
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
              MCP Server Connected!
            </Typography>
            <Typography variant="body1" sx={{ mb: 3 }}>
              This window will close automatically...
            </Typography>
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
              Connection Failed
            </Typography>
            <Alert severity="error" sx={{ mb: 3, textAlign: 'left' }}>
              {error}
            </Alert>
            <Button variant="outlined" onClick={() => window.close()}>
              Close Window
            </Button>
          </>
        )}
      </Box>
    </Container>
  );
}
