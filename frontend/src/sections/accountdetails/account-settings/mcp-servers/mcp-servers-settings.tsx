import { useState, useEffect, useCallback } from 'react';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Table from '@mui/material/Table';
import Radio from '@mui/material/Radio';
import Button from '@mui/material/Button';
import TableRow from '@mui/material/TableRow';
import Container from '@mui/material/Container';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TextField from '@mui/material/TextField';
import RadioGroup from '@mui/material/RadioGroup';
import Typography from '@mui/material/Typography';
import FormControl from '@mui/material/FormControl';
import { alpha, useTheme } from '@mui/material/styles';
import TableContainer from '@mui/material/TableContainer';
import CircularProgress from '@mui/material/CircularProgress';
import FormControlLabel from '@mui/material/FormControlLabel';

import axios from 'src/utils/axios';

type AuthType = 'oauth' | 'bearer_token' | 'pre_registered';

interface McpConnection {
  mcpServerUrl: string;
  authType?: string;
  scope?: string;
  expiresAt?: number;
  createdAt: number;
  registrationMethod: string;
}

const METHOD_LABELS: Record<string, string> = {
  bearer_token: 'Bearer Token',
  pre_registered: 'Pre-registered',
  dcr: 'OAuth (DCR)',
  client_id_metadata_document: 'OAuth (Metadata)',
};

export default function McpServersSettings() {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [connections, setConnections] = useState<McpConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  // Form state
  const [mcpServerUrl, setMcpServerUrl] = useState('');
  const [authType, setAuthType] = useState<AuthType>('oauth');
  const [bearerToken, setBearerToken] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');

  const fetchConnections = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await axios.get('/api/v1/mcp/auth/connections');
      setConnections(res.data || []);
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || 'Failed to load connections');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConnections();
  }, [fetchConnections]);

  const clearForm = () => {
    setMcpServerUrl('');
    setBearerToken('');
    setClientId('');
    setClientSecret('');
  };

  const handleConnectBearer = async () => {
    try {
      setConnecting(true);
      setError(null);

      await axios.post('/api/v1/mcp/auth/connect-bearer', {
        mcpServerUrl: mcpServerUrl.trim(),
        bearerToken: bearerToken.trim(),
      });

      clearForm();
      fetchConnections();
    } catch (err: any) {
      setError(
        err?.response?.data?.message || err?.message || 'Failed to connect with bearer token'
      );
    } finally {
      setConnecting(false);
    }
  };

  const handleConnectOAuth = async () => {
    try {
      setConnecting(true);
      setError(null);

      const body: Record<string, string> = {
        mcpServerUrl: mcpServerUrl.trim(),
        frontendBaseUrl: window.location.origin,
      };
      if (authType === 'pre_registered') {
        body.clientId = clientId.trim();
        if (clientSecret.trim()) body.clientSecret = clientSecret.trim();
      }

      const res = await axios.post('/api/v1/mcp/auth/start', body);

      // Open authorization URL in a new tab
      const oauthTab = window.open(res.data.authorizationUrl, '_blank');
      oauthTab?.focus();
      setConnecting(false);

      // Listen for postMessage from the callback tab
      const handleOAuthMessage = async (event: MessageEvent) => {
        if (event.origin !== window.location.origin) return;

        if (event.data.type === 'MCP_OAUTH_SUCCESS') {
          window.removeEventListener('message', handleOAuthMessage);
          clearForm();
          fetchConnections();
        } else if (event.data.type === 'MCP_OAUTH_ERROR') {
          window.removeEventListener('message', handleOAuthMessage);
          setError(event.data.error || 'OAuth authentication failed');
        }
      };

      window.addEventListener('message', handleOAuthMessage);

      // Clean up listener when popup is closed or after timeout
      const checkClosed = setInterval(() => {
        if (oauthTab && oauthTab.closed) {
          window.removeEventListener('message', handleOAuthMessage);
          clearInterval(checkClosed);
        }
      }, 1000);

      setTimeout(() => {
        window.removeEventListener('message', handleOAuthMessage);
        clearInterval(checkClosed);
      }, 300000); // 5 minute timeout
    } catch (err: any) {
      setConnecting(false);
      setError(
        err?.response?.data?.message || err?.message || 'Failed to start OAuth flow'
      );
    }
  };

  const handleConnect = () => {
    if (!mcpServerUrl.trim()) return;

    if (authType === 'bearer_token') {
      handleConnectBearer();
    } else {
      handleConnectOAuth();
    }
  };

  const isConnectDisabled = () => {
    if (connecting || !mcpServerUrl.trim()) return true;
    if (authType === 'bearer_token' && !bearerToken.trim()) return true;
    if (authType === 'pre_registered' && !clientId.trim()) return true;
    return false;
  };

  const handleDelete = async (serverUrl: string) => {
    try {
      setError(null);
      await axios.delete('/api/v1/mcp/auth/token', {
        params: { mcpServerUrl: serverUrl },
      });
      setConnections((prev) => prev.filter((c) => c.mcpServerUrl !== serverUrl));
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || 'Failed to delete connection');
    }
  };

  const formatDate = (ts: number) => new Date(ts).toLocaleString();

  const getMethodLabel = (method: string) => METHOD_LABELS[method] || method;

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
          position: 'relative',
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
          <Typography variant="h5" sx={{ fontWeight: 600 }}>
            MCP Server Connections
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Connect to external MCP servers using OAuth or bearer token authentication.
          </Typography>
        </Box>

        {/* Content Section */}
        <Box sx={{ p: 3 }}>
          {error && (
            <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          {/* Connect Form */}
          <Box
            sx={{ mb: 4, p: 2.5, border: '1px solid', borderColor: 'divider', borderRadius: 1.5 }}
          >
            <Typography variant="subtitle1" sx={{ mb: 2, fontWeight: 600 }}>
              Connect to MCP Server
            </Typography>

            {/* Auth Type Selector */}
            <FormControl sx={{ mb: 2 }}>
              <RadioGroup
                row
                value={authType}
                onChange={(e) => setAuthType(e.target.value as AuthType)}
              >
                <FormControlLabel value="oauth" control={<Radio size="small" />} label="OAuth" />
                <FormControlLabel
                  value="bearer_token"
                  control={<Radio size="small" />}
                  label="Bearer Token / API Key"
                />
                <FormControlLabel
                  value="pre_registered"
                  control={<Radio size="small" />}
                  label="Pre-registered Client"
                />
              </RadioGroup>
            </FormControl>

            {/* URL + Connect */}
            <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
              <TextField
                fullWidth
                size="small"
                label="MCP Server URL"
                placeholder="https://example.com/mcp"
                value={mcpServerUrl}
                onChange={(e) => setMcpServerUrl(e.target.value)}
                disabled={connecting}
              />
              <Button
                variant="contained"
                onClick={handleConnect}
                disabled={isConnectDisabled()}
                sx={{ minWidth: 120 }}
              >
                {connecting ? <CircularProgress size={20} /> : 'Connect'}
              </Button>
            </Box>

            {/* Bearer Token Field */}
            {authType === 'bearer_token' && (
              <Box sx={{ mt: 1.5 }}>
                <TextField
                  fullWidth
                  size="small"
                  label="Bearer Token / API Key"
                  type="password"
                  placeholder="Enter your bearer token or API key"
                  value={bearerToken}
                  onChange={(e) => setBearerToken(e.target.value)}
                  disabled={connecting}
                />
              </Box>
            )}

            {/* Pre-registered Client Fields */}
            {authType === 'pre_registered' && (
              <Box sx={{ display: 'flex', gap: 1, mt: 1.5 }}>
                <TextField
                  fullWidth
                  size="small"
                  label="Client ID"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  disabled={connecting}
                />
                <TextField
                  fullWidth
                  size="small"
                  label="Client Secret (optional)"
                  type="password"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  disabled={connecting}
                />
              </Box>
            )}
          </Box>

          {/* Connections List */}
          <Typography variant="subtitle1" sx={{ mb: 1.5, fontWeight: 600 }}>
            Connected Servers
          </Typography>

          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          ) : connections.length === 0 ? (
            <Typography color="text.secondary" sx={{ py: 2 }}>
              No MCP servers connected yet.
            </Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Server URL</TableCell>
                    <TableCell>Method</TableCell>
                    <TableCell>Connected</TableCell>
                    <TableCell>Expires</TableCell>
                    <TableCell align="right">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {connections.map((conn) => (
                    <TableRow key={conn.mcpServerUrl}>
                      <TableCell sx={{ maxWidth: 300, wordBreak: 'break-all' }}>
                        {conn.mcpServerUrl}
                      </TableCell>
                      <TableCell>{getMethodLabel(conn.registrationMethod)}</TableCell>
                      <TableCell>{formatDate(conn.createdAt)}</TableCell>
                      <TableCell>
                        {conn.authType === 'bearer_token'
                          ? 'Never'
                          : conn.expiresAt
                            ? formatDate(conn.expiresAt)
                            : 'No expiry'}
                      </TableCell>
                      <TableCell align="right">
                        <Button
                          size="small"
                          color="error"
                          onClick={() => handleDelete(conn.mcpServerUrl)}
                        >
                          Disconnect
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>
      </Box>
    </Container>
  );
}
