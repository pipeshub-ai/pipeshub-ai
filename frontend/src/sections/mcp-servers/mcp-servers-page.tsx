/**
 * MCP Servers Management Page
 *
 * Architecture highlights:
 * - Server-side search AND auth-status filtering (never filtered on the frontend)
 * - Infinite scroll via IntersectionObserver **callback refs** — the observer is
 *   attached the instant the sentinel node enters the DOM, regardless of when
 *   that happens relative to React's effect scheduling.
 * - `loadMoreConfigured` / `loadMoreCatalog` are stable (zero deps) useCallbacks
 *   that read all runtime state from refs, so the IntersectionObserver closure is
 *   never stale.
 * - Debounced search directly calls the page-1 fetcher — no intermediate useEffect
 *   watching `activeSearchQuery`, which would cause double fetches on mount.
 * - LinearProgress during search/filter changes; full skeleton only on initial load
 *   or tab switch.
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Box,
  Container,
  Typography,
  Grid,
  Card,
  CardContent,
  Button,
  Chip,
  Alert,
  Snackbar,
  Stack,
  alpha,
  useTheme,
  Paper,
  IconButton,
  Tooltip,
  InputAdornment,
  TextField,
  Fade,
  Tabs,
  Tab,
  Skeleton,
  LinearProgress,
  Avatar,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Divider,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import type { MCPServerInstance, MCPServerTemplate } from 'src/types/agent';
import * as McpServerApi from 'src/services/mcp-server-api';
import { useAdmin } from 'src/context/AdminContext';

import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle';
import refreshIcon from '@iconify-icons/mdi/refresh';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import clearIcon from '@iconify-icons/mdi/close-circle';
import linkIcon from '@iconify-icons/mdi/link-variant';
import appsIcon from '@iconify-icons/mdi/apps';
import listIcon from '@iconify-icons/mdi/format-list-bulleted';
import closeIcon from '@iconify-icons/mdi/close';
import keyIcon from '@iconify-icons/mdi/key';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import saveIcon from '@iconify-icons/eva/save-outline';
import plusCircleIcon from '@iconify-icons/mdi/plus-circle';
import boltIcon from '@iconify-icons/mdi/bolt';
import eyeIcon from '@iconify-icons/mdi/eye';
import clockCircleIcon from '@iconify-icons/mdi/clock-outline';
import settingsIcon from '@iconify-icons/mdi/settings';
import openInNewIcon from '@iconify-icons/mdi/open-in-new';
import shieldLockIcon from '@iconify-icons/mdi/shield-lock';

import { useSearchParams } from 'react-router-dom';

// ============================================================================
// Constants
// ============================================================================

const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 500;
const INITIAL_PAGE = 1;
const SKELETON_COUNT = 8;
const LOAD_MORE_SKELETON_COUNT = 4;
const SERVER_ICON = 'solar:server-bold-duotone';
const DEFAULT_ICON = '/assets/icons/connectors/default.svg';

// ============================================================================
// Types
// ============================================================================

interface SnackbarState {
  open: boolean;
  message: string;
  severity: 'success' | 'error' | 'info' | 'warning';
}

type TabValue = 'my-servers' | 'available';
type FilterType = 'all' | 'authenticated' | 'not-authenticated';

// ============================================================================
// Config Dialog
// ============================================================================

interface McpServerConfigDialogProps {
  instance?: MCPServerInstance;
  template?: MCPServerTemplate;
  isAdmin?: boolean;
  onClose: () => void;
  onSuccess: () => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

const McpServerConfigDialog: React.FC<McpServerConfigDialogProps> = ({
  instance,
  template,
  isAdmin = false,
  onClose,
  onSuccess,
  onShowToast,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const isManageMode = !!instance;

  const outlinedInputSx = {
    borderRadius: 1.25,
    backgroundColor: isDark
      ? alpha(theme.palette.background.paper, 0.6)
      : alpha(theme.palette.background.paper, 0.8),
    transition: 'all 0.2s',
    '&:hover': {
      backgroundColor: isDark
        ? alpha(theme.palette.background.paper, 0.8)
        : alpha(theme.palette.background.paper, 1),
    },
    '&.Mui-focused': {
      backgroundColor: isDark
        ? alpha(theme.palette.background.paper, 0.9)
        : theme.palette.background.paper,
    },
    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderWidth: 1.5,
      borderColor: theme.palette.primary.main,
    },
  } as const;

  const inputLabelSx = { fontSize: '0.875rem', fontWeight: 500 } as const;
  const inputTextSx = { fontSize: '0.875rem', padding: '10.5px 14px', fontWeight: 400 } as const;
  const helperTextSx = { fontSize: '0.75rem', fontWeight: 400, marginTop: 0.75, marginLeft: 1 } as const;

  const [apiToken, setApiToken] = useState('');
  const [instanceName, setInstanceName] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [oauthLoading, setOauthLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(instance?.isAuthenticated ?? false);
  const [discoveredTools, setDiscoveredTools] = useState<{ name: string; description: string }[]>([]);
  const [showAllTools, setShowAllTools] = useState(false);
  const [copiedRedirectUri, setCopiedRedirectUri] = useState(false);

  const mcpOAuthRedirectUri = useMemo(() => {
    const serverType = instance?.serverType || template?.typeId || '';
    const redirectPath = template?.redirectUri || (serverType ? `mcp-servers/oauth/callback/${serverType}` : '');
    if (!redirectPath) return '';
    return `${window.location.origin}/${redirectPath}`;
  }, [instance?.serverType, template?.typeId, template?.redirectUri]);

  const displayName = isManageMode
    ? (instance!.displayName || instance!.instanceName)
    : (template?.displayName || 'MCP Server');
  const authMode = isManageMode ? instance!.authMode : (template?.authMode || 'none');
  const supportedAuthTypes = isManageMode
    ? (instance!.supportedAuthTypes || [])
    : (template?.supportedAuthTypes || []);
  const iconPath = isManageMode
    ? (instance!.iconPath || DEFAULT_ICON)
    : (template?.iconPath || DEFAULT_ICON);
  const description = isManageMode ? instance!.description : template?.description;
  const transport = isManageMode ? instance!.transport : (template?.transport || 'stdio');
  const tools = instance?.tools || [];

  const needsApiToken = (supportedAuthTypes.includes('API_TOKEN') || supportedAuthTypes.includes('api_token') || authMode === 'api_token') && authMode !== 'oauth';
  const isOAuth = authMode === 'oauth' || supportedAuthTypes.includes('OAUTH') || supportedAuthTypes.includes('oauth2');
  const needsHeaders = authMode === 'headers' || supportedAuthTypes.includes('HEADERS');

  const handleAuthenticate = async () => {
    if (!instance) return;
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      await McpServerApi.authenticateInstance(instance.instanceId, { apiToken: apiToken.trim() });
      setIsAuthenticated(true);
      setSuccess('Authenticated successfully!');
      onShowToast?.('MCP Server authenticated successfully', 'success');
      onSuccess();
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Authentication failed');
    } finally {
      setSaving(false);
    }
  };

  const handleUpdateCredentials = async () => {
    if (!instance) return;
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      await McpServerApi.updateCredentials(instance.instanceId, { apiToken: apiToken.trim() });
      setIsAuthenticated(true);
      setSuccess('Credentials updated successfully!');
      onShowToast?.('Credentials updated', 'success');
      onSuccess();
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to update credentials');
    } finally {
      setSaving(false);
    }
  };

  const handleCreateFromCatalog = async () => {
    if (!template) return;
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);

      if (!instanceName.trim()) {
        setError('Instance name is required');
        setSaving(false);
        return;
      }

      const isTemplateOAuth = template.authMode === 'oauth' || template.supportedAuthTypes?.includes('OAUTH');
      if (isTemplateOAuth && !clientId.trim()) {
        setError('OAuth Client ID is required for OAuth servers');
        setSaving(false);
        return;
      }

      const body: McpServerApi.CreateCatalogInstanceBody = {
        instanceName: instanceName.trim(),
        serverType: template.typeId,
        displayName: template.displayName,
        description: template.description,
        authMode: template.authMode,
        iconPath: template.iconPath,
      };

      if (isTemplateOAuth && clientId.trim()) {
        body.clientId = clientId.trim();
        body.clientSecret = clientSecret.trim();
      }

      await McpServerApi.createInstance(body);
      setSuccess('MCP Server instance created successfully!');
      onShowToast?.('MCP Server instance created', 'success');
      onSuccess();
      setTimeout(onClose, 800);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to create instance');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!instance) return;
    const confirmMsg = isAdmin
      ? `Delete MCP server instance "${displayName}"? This will remove it for all users.`
      : `Remove your credentials for "${displayName}"?`;
    if (!window.confirm(confirmMsg)) return;

    try {
      setDeleting(true);
      setError(null);
      if (isAdmin) {
        await McpServerApi.deleteInstance(instance.instanceId);
        onShowToast?.('MCP Server instance deleted', 'success');
      } else {
        await McpServerApi.removeCredentials(instance.instanceId);
        onShowToast?.('Credentials removed', 'success');
      }
      onSuccess();
      setTimeout(onClose, 800);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to delete');
    } finally {
      setDeleting(false);
    }
  };

  const handleOAuthAuthorize = async () => {
    if (!instance) return;
    try {
      setOauthLoading(true);
      setError(null);
      setSuccess(null);

      const result = await McpServerApi.oauthAuthorize(instance.instanceId);

      if (result.authorizationUrl) {
        window.location.href = result.authorizationUrl;
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to start OAuth flow');
      setOauthLoading(false);
    }
  };

  const handleOAuthRefresh = async () => {
    if (!instance) return;
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      const result = await McpServerApi.oauthRefresh(instance.instanceId);
      setIsAuthenticated(true);
      setSuccess('Token refreshed successfully!');
      onShowToast?.('OAuth token refreshed', 'success');
      onSuccess();
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Token refresh failed. You may need to re-authorize.');
    } finally {
      setSaving(false);
    }
  };

  const handleDiscoverTools = async () => {
    if (!instance) return;
    try {
      setDiscovering(true);
      setError(null);
      const result = await McpServerApi.discoverInstanceTools(instance.instanceId);
      setDiscoveredTools(result.tools.map((t) => ({ name: t.name, description: t.description })));
      onShowToast?.(`Discovered ${result.toolCount} tools`, 'info');
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to discover tools');
    } finally {
      setDiscovering(false);
    }
  };

  const isAnyActionInProgress = saving || deleting || discovering || oauthLoading;
  const allTools = discoveredTools.length > 0 ? discoveredTools : tools.map((t) => ({ name: t.name, description: t.description }));

  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2.5,
          boxShadow: isDark
            ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
            : '0 20px 60px rgba(0, 0, 0, 0.12)',
        },
      }}
    >
      {/* Title */}
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 2.5,
          borderBottom: `1px solid ${alpha(theme.palette.divider, isDark ? 0.12 : 0.08)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1.5,
              backgroundColor: isDark
                ? alpha(theme.palette.common.white, 0.9)
                : alpha(theme.palette.grey[100], 0.8),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: isDark ? `1px solid ${alpha(theme.palette.common.white, 0.1)}` : 'none',
            }}
          >
            <img
              src={iconPath}
              alt={displayName}
              width={32}
              height={32}
              style={{ objectFit: 'contain' }}
              onError={(e: React.SyntheticEvent<HTMLImageElement>) => {
                (e.target as HTMLImageElement).src = DEFAULT_ICON;
              }}
            />
          </Box>
          <Box>
            <Typography
              variant="h6"
              sx={{ fontWeight: 600, mb: 0.5, color: theme.palette.text.primary, fontSize: '1.125rem', letterSpacing: '-0.01em' }}
            >
              {isManageMode ? displayName : `Configure ${displayName}`}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              {transport && (
                <Chip
                  label={transport.toUpperCase()}
                  size="small"
                  variant="outlined"
                  sx={{ fontSize: '0.6875rem', height: 20, fontWeight: 500 }}
                />
              )}
              {authMode !== 'none' && (
                <Chip
                  label={authMode.split('_').join(' ').toUpperCase()}
                  size="small"
                  variant="outlined"
                  sx={{ fontSize: '0.6875rem', height: 20, fontWeight: 500 }}
                />
              )}
            </Box>
          </Box>
        </Box>
        <IconButton
          onClick={onClose}
          size="small"
          sx={{ color: theme.palette.text.secondary, p: 1, '&:hover': { backgroundColor: alpha(theme.palette.text.secondary, 0.08) } }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      {/* Content */}
      <DialogContent sx={{ px: 3, py: 3 }}>
        <Stack spacing={3}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)} sx={{ borderRadius: 1.5 }}>
              {error}
            </Alert>
          )}
          {success && (
            <Alert severity="success" onClose={() => setSuccess(null)} sx={{ borderRadius: 1.5 }}>
              {success}
            </Alert>
          )}
          {isManageMode && isAuthenticated && !success && (
            <Alert severity="success" icon={<Iconify icon={checkCircleIcon} />} sx={{ borderRadius: 1.5 }}>
              You are authenticated and ready to use this MCP server.
            </Alert>
          )}

          {description && (
            <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem', lineHeight: 1.6 }}>
              {description}
            </Typography>
          )}

          {/* CREATE MODE */}
          {!isManageMode && template && (
            <Paper
              variant="outlined"
              sx={{ p: 2.5, borderRadius: 1.5, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 0.5 }}>
                  <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Iconify icon="mdi:cog" width={16} sx={{ color: theme.palette.primary.main }} />
                  </Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                    Configuration
                  </Typography>
                </Box>

                <Grid container spacing={2}>
                  <Grid item xs={12}>
                    <TextField
                      label="Instance Name"
                      value={instanceName}
                      onChange={(e) => setInstanceName(e.target.value)}
                      required
                      fullWidth
                      size="small"
                      placeholder={`e.g., ${template.displayName} - Production`}
                      sx={{
                        '& .MuiOutlinedInput-root': outlinedInputSx,
                        '& .MuiInputLabel-root': inputLabelSx,
                        '& .MuiOutlinedInput-input': inputTextSx,
                        '& .MuiFormHelperText-root': helperTextSx,
                      }}
                    />
                  </Grid>
                </Grid>

                {needsApiToken && (
                  <Grid container spacing={2}>
                    <Grid item xs={12}>
                      <TextField
                        label="API Token"
                        value={apiToken}
                        onChange={(e) => setApiToken(e.target.value)}
                        type="password"
                        fullWidth
                        size="small"
                        placeholder="Enter your API token"
                        helperText="Optional — you can authenticate later from My MCP Servers"
                        sx={{
                          '& .MuiOutlinedInput-root': outlinedInputSx,
                          '& .MuiInputLabel-root': inputLabelSx,
                          '& .MuiOutlinedInput-input': inputTextSx,
                          '& .MuiFormHelperText-root': helperTextSx,
                        }}
                      />
                    </Grid>
                  </Grid>
                )}

                {/* OAuth Client Credentials for OAuth servers */}
                {isOAuth && (
                  <>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mt: 1 }}>
                      <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <Iconify icon={shieldLockIcon} width={16} sx={{ color: theme.palette.primary.main }} />
                      </Box>
                      <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                        OAuth App Credentials
                      </Typography>
                    </Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', lineHeight: 1.5, mt: -0.5 }}>
                      Create an OAuth app with your provider and enter the credentials here. All users will authenticate through this OAuth app.
                    </Typography>

                    {mcpOAuthRedirectUri && (
                      <Box>
                        <Typography variant="caption" sx={{ fontWeight: 600, fontSize: '0.75rem', color: theme.palette.text.secondary, mb: 0.5, display: 'block' }}>
                          Redirect URI (copy this to your OAuth app configuration)
                        </Typography>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, bgcolor: alpha(theme.palette.grey[500], 0.08), borderRadius: 1, px: 1.5, py: 0.75 }}>
                          <Typography
                            variant="body2"
                            sx={{
                              fontFamily: 'monospace',
                              fontSize: '0.75rem',
                              flex: 1,
                              wordBreak: 'break-all',
                            }}
                          >
                            {mcpOAuthRedirectUri}
                          </Typography>
                          <Tooltip title={copiedRedirectUri ? 'Copied!' : 'Copy to clipboard'} arrow>
                            <IconButton
                              size="small"
                              onClick={() => {
                                navigator.clipboard.writeText(mcpOAuthRedirectUri);
                                setCopiedRedirectUri(true);
                                setTimeout(() => setCopiedRedirectUri(false), 2000);
                              }}
                              sx={{
                                color: copiedRedirectUri ? theme.palette.success.main : theme.palette.primary.main,
                                '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.08) },
                              }}
                            >
                              <Iconify icon={copiedRedirectUri ? checkCircleIcon : 'mdi:content-copy'} width={16} />
                            </IconButton>
                          </Tooltip>
                        </Box>
                      </Box>
                    )}

                    <Grid container spacing={2}>
                      <Grid item xs={12}>
                        <TextField
                          label="Client ID"
                          value={clientId}
                          onChange={(e) => setClientId(e.target.value)}
                          required
                          fullWidth
                          size="small"
                          placeholder="Enter your OAuth Client ID"
                          sx={{
                            '& .MuiOutlinedInput-root': outlinedInputSx,
                            '& .MuiInputLabel-root': inputLabelSx,
                            '& .MuiOutlinedInput-input': inputTextSx,
                            '& .MuiFormHelperText-root': helperTextSx,
                          }}
                        />
                      </Grid>
                      <Grid item xs={12}>
                        <TextField
                          label="Client Secret"
                          value={clientSecret}
                          onChange={(e) => setClientSecret(e.target.value)}
                          type="password"
                          fullWidth
                          size="small"
                          placeholder="Enter your OAuth Client Secret"
                          sx={{
                            '& .MuiOutlinedInput-root': outlinedInputSx,
                            '& .MuiInputLabel-root': inputLabelSx,
                            '& .MuiOutlinedInput-input': inputTextSx,
                            '& .MuiFormHelperText-root': helperTextSx,
                          }}
                        />
                      </Grid>
                    </Grid>
                  </>
                )}

                {template.tags && template.tags.length > 0 && (
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                    {template.tags.map((tag) => (
                      <Chip
                        key={tag}
                        label={tag}
                        size="small"
                        variant="outlined"
                        sx={{ borderRadius: 1, fontSize: '0.75rem', height: 22 }}
                      />
                    ))}
                  </Stack>
                )}
              </Stack>
            </Paper>
          )}

          {/* MANAGE MODE — Credentials Section */}
          {isManageMode && needsApiToken && (
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.text.primary, 0.05), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Iconify icon={keyIcon} width={16} sx={{ color: theme.palette.text.primary }} />
                  </Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                    Your Credentials
                  </Typography>
                </Box>

                <Grid container spacing={2}>
                  <Grid item xs={12}>
                    <TextField
                      label="API Token"
                      value={apiToken}
                      onChange={(e) => setApiToken(e.target.value)}
                      type="password"
                      fullWidth
                      size="small"
                      placeholder={isAuthenticated ? 'Enter new token to update' : 'Enter your API token'}
                      sx={{
                        '& .MuiOutlinedInput-root': outlinedInputSx,
                        '& .MuiInputLabel-root': inputLabelSx,
                        '& .MuiOutlinedInput-input': inputTextSx,
                        '& .MuiFormHelperText-root': helperTextSx,
                      }}
                    />
                  </Grid>
                </Grid>

                {isAuthenticated && (
                  <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                    You are authenticated. Enter a new token and click &quot;Update Credentials&quot; to change it.
                  </Alert>
                )}
              </Stack>
            </Paper>
          )}

          {/* MANAGE MODE — Headers Auth Section */}
          {isManageMode && needsHeaders && (
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.text.primary, 0.05), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Iconify icon={keyIcon} width={16} sx={{ color: theme.palette.text.primary }} />
                  </Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                    Authorization Header
                  </Typography>
                </Box>

                <Grid container spacing={2}>
                  <Grid item xs={12}>
                    <TextField
                      label="Authorization Value"
                      value={apiToken}
                      onChange={(e) => setApiToken(e.target.value)}
                      type="password"
                      fullWidth
                      size="small"
                      placeholder={isAuthenticated ? 'Enter new value to update' : 'e.g. Bearer sk-...'}
                      helperText="The value for the Authorization header sent to the MCP server"
                      sx={{
                        '& .MuiOutlinedInput-root': outlinedInputSx,
                        '& .MuiInputLabel-root': inputLabelSx,
                        '& .MuiOutlinedInput-input': inputTextSx,
                        '& .MuiFormHelperText-root': helperTextSx,
                      }}
                    />
                  </Grid>
                </Grid>

                {isAuthenticated && (
                  <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                    You are authenticated. Enter a new value and click &quot;Update Credentials&quot; to change it.
                  </Alert>
                )}
              </Stack>
            </Paper>
          )}

          {/* MANAGE MODE — OAuth Section */}
          {isManageMode && isOAuth && (
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Iconify icon={shieldLockIcon} width={16} sx={{ color: theme.palette.primary.main }} />
                  </Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                    OAuth Authentication
                  </Typography>
                </Box>

                {isAuthenticated ? (
                  <>
                    <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                      OAuth authorization is active. Your token is managed automatically.
                    </Alert>
                    {instance?.oauthExpiresAt && (
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                        Token expires: {new Date(instance.oauthExpiresAt * 1000).toLocaleString()}
                      </Typography>
                    )}
                    <Stack direction="row" spacing={1}>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={handleOAuthRefresh}
                        disabled={isAnyActionInProgress}
                        startIcon={saving ? <CircularProgress size={14} /> : <Iconify icon={refreshIcon} width={16} />}
                        sx={{ textTransform: 'none', borderRadius: 1 }}
                      >
                        {saving ? 'Refreshing...' : 'Refresh Token'}
                      </Button>
                      <Button
                        variant="outlined"
                        size="small"
                        color="warning"
                        onClick={handleOAuthAuthorize}
                        disabled={isAnyActionInProgress}
                        startIcon={oauthLoading ? <CircularProgress size={14} /> : <Iconify icon={openInNewIcon} width={16} />}
                        sx={{ textTransform: 'none', borderRadius: 1 }}
                      >
                        {oauthLoading ? 'Redirecting...' : 'Re-authorize'}
                      </Button>
                    </Stack>
                  </>
                ) : (
                  <>
                    {instance?.hasOAuthClientConfig === false ? (
                      <Alert severity="warning" sx={{ borderRadius: 1.25 }}>
                        OAuth client credentials (Client ID / Client Secret) have not been configured for this instance. An administrator must update them before users can authorize.
                      </Alert>
                    ) : (
                      <>
                        <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                          This MCP server requires OAuth authorization. Click the button below to authorize access.
                        </Alert>
                        <Button
                          variant="contained"
                          onClick={handleOAuthAuthorize}
                          disabled={isAnyActionInProgress}
                          startIcon={oauthLoading ? <CircularProgress size={14} sx={{ color: 'inherit' }} /> : <Iconify icon={openInNewIcon} width={16} />}
                          sx={{ textTransform: 'none', borderRadius: 1, alignSelf: 'flex-start', boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
                        >
                          {oauthLoading ? 'Redirecting...' : 'Authorize with OAuth'}
                        </Button>
                      </>
                    )}
                  </>
                )}
              </Stack>
            </Paper>
          )}

          {/* MANAGE MODE — Admin OAuth Client Config */}
          {isManageMode && isOAuth && isAdmin && (
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box sx={{ p: 0.625, borderRadius: 1, bgcolor: alpha(theme.palette.warning.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Iconify icon="mdi:cog" width={16} sx={{ color: theme.palette.warning.main }} />
                  </Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                    OAuth App Settings (Admin)
                  </Typography>
                </Box>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
                  Update the OAuth app credentials used by all users of this instance.
                </Typography>

                {/* Redirect URI for OAuth app registration */}
                {mcpOAuthRedirectUri && (
                  <Box>
                    <Typography variant="caption" sx={{ fontWeight: 600, fontSize: '0.75rem', color: theme.palette.text.secondary, mb: 0.5, display: 'block' }}>
                      Redirect URI (copy this to your OAuth app configuration)
                    </Typography>
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        p: 1.25,
                        borderRadius: 1.25,
                        border: `1px solid ${alpha(theme.palette.primary.main, 0.3)}`,
                        bgcolor: alpha(theme.palette.primary.main, 0.04),
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          flex: 1,
                          fontFamily: 'monospace',
                          fontSize: '0.8125rem',
                          fontWeight: 500,
                          userSelect: 'all',
                          lineHeight: 1.6,
                          wordBreak: 'break-all',
                        }}
                      >
                        {mcpOAuthRedirectUri}
                      </Typography>
                      <Tooltip title={copiedRedirectUri ? 'Copied!' : 'Copy to clipboard'} arrow>
                        <IconButton
                          size="small"
                          onClick={() => {
                            navigator.clipboard.writeText(mcpOAuthRedirectUri);
                            setCopiedRedirectUri(true);
                            setTimeout(() => setCopiedRedirectUri(false), 2000);
                          }}
                          sx={{
                            color: copiedRedirectUri ? theme.palette.success.main : theme.palette.primary.main,
                            '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.08) },
                          }}
                        >
                          <Iconify icon={copiedRedirectUri ? checkCircleIcon : 'mdi:content-copy'} width={16} />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  </Box>
                )}

                <Grid container spacing={2}>
                  <Grid item xs={12}>
                    <TextField
                      label="Client ID"
                      value={clientId}
                      onChange={(e) => setClientId(e.target.value)}
                      fullWidth
                      size="small"
                      placeholder="Enter OAuth Client ID"
                      sx={{
                        '& .MuiOutlinedInput-root': outlinedInputSx,
                        '& .MuiInputLabel-root': inputLabelSx,
                        '& .MuiOutlinedInput-input': inputTextSx,
                        '& .MuiFormHelperText-root': helperTextSx,
                      }}
                    />
                  </Grid>
                  <Grid item xs={12}>
                    <TextField
                      label="Client Secret"
                      value={clientSecret}
                      onChange={(e) => setClientSecret(e.target.value)}
                      type="password"
                      fullWidth
                      size="small"
                      placeholder="Enter OAuth Client Secret"
                      sx={{
                        '& .MuiOutlinedInput-root': outlinedInputSx,
                        '& .MuiInputLabel-root': inputLabelSx,
                        '& .MuiOutlinedInput-input': inputTextSx,
                        '& .MuiFormHelperText-root': helperTextSx,
                      }}
                    />
                  </Grid>
                </Grid>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={async () => {
                    if (!clientId.trim()) {
                      setError('Client ID is required');
                      return;
                    }
                    try {
                      setSaving(true);
                      setError(null);
                      await McpServerApi.setInstanceOauthConfig(instance!.instanceId, {
                        clientId: clientId.trim(),
                        clientSecret: clientSecret.trim(),
                      });
                      setSuccess('OAuth client credentials updated');
                      onShowToast?.('OAuth client credentials updated', 'success');
                      setClientId('');
                      setClientSecret('');
                    } catch (err: any) {
                      setError(err.response?.data?.detail || 'Failed to update OAuth credentials');
                    } finally {
                      setSaving(false);
                    }
                  }}
                  disabled={saving || !clientId.trim()}
                  startIcon={saving ? <CircularProgress size={14} /> : <Iconify icon={shieldLockIcon} width={16} />}
                  sx={{ textTransform: 'none', borderRadius: 1, alignSelf: 'flex-start' }}
                >
                  {saving ? 'Saving...' : 'Update OAuth Credentials'}
                </Button>
              </Stack>
            </Paper>
          )}

          {isManageMode && authMode === 'none' && (
            <Alert severity="info" sx={{ borderRadius: 1.25 }}>
              This MCP server does not require authentication.
            </Alert>
          )}

          {/* Tools Preview */}
          {allTools.length > 0 && (
            <Box>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  Available Tools ({allTools.length})
                </Typography>
                {allTools.length > 5 && (
                  <Button
                    size="small"
                    onClick={() => setShowAllTools(!showAllTools)}
                    sx={{ textTransform: 'none', fontSize: '0.75rem', minWidth: 'auto', px: 1, py: 0.5, color: theme.palette.primary.main }}
                  >
                    {showAllTools ? 'Show less' : `Show all (${allTools.length})`}
                  </Button>
                )}
              </Box>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {(showAllTools ? allTools : allTools.slice(0, 5)).map((tool) => (
                  <Tooltip key={tool.name} title={tool.description || ''} arrow>
                    <Chip
                      label={tool.name}
                      size="small"
                      variant="outlined"
                      sx={{ borderRadius: 1, fontSize: '0.8125rem', height: 26 }}
                    />
                  </Tooltip>
                ))}
              </Stack>
            </Box>
          )}

          {/* Discover Tools Button */}
          {isManageMode && isAuthenticated && (
            <Button
              variant="text"
              size="small"
              startIcon={discovering ? <CircularProgress size={14} /> : <Iconify icon={boltIcon} width={16} />}
              onClick={handleDiscoverTools}
              disabled={isAnyActionInProgress}
              sx={{ textTransform: 'none', alignSelf: 'flex-start' }}
            >
              {discovering ? 'Discovering...' : 'Discover Tools'}
            </Button>
          )}
        </Stack>
      </DialogContent>

      {/* Actions */}
      <DialogActions
        sx={{
          px: 3,
          py: 2.5,
          borderTop: `1px solid ${alpha(theme.palette.divider, isDark ? 0.12 : 0.08)}`,
          flexDirection: 'row',
          justifyContent: 'space-between',
        }}
      >
        {/* Left: Destructive */}
        <Box>
          {isManageMode && (
            <Button
              onClick={handleDelete}
              disabled={isAnyActionInProgress}
              variant="text"
              color="error"
              startIcon={deleting ? <CircularProgress size={14} color="error" /> : <Iconify icon={deleteIcon} width={16} />}
              sx={{ textTransform: 'none', borderRadius: 1, px: 2, '&:hover': { backgroundColor: alpha(theme.palette.error.main, 0.08) } }}
            >
              {deleting ? 'Deleting...' : isAdmin ? 'Delete Instance' : 'Remove Credentials'}
            </Button>
          )}
        </Box>

        {/* Right: Primary */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Button
            onClick={onClose}
            disabled={isAnyActionInProgress}
            variant="text"
            sx={{ textTransform: 'none', borderRadius: 1, px: 2, color: theme.palette.text.secondary }}
          >
            {isAuthenticated ? 'Close' : 'Cancel'}
          </Button>

          <Box sx={{ width: '1px', height: 20, bgcolor: alpha(theme.palette.divider, 0.4), mx: 0.5 }} />

          {isManageMode ? (
            (needsApiToken || needsHeaders) ? (
              <Button
                onClick={isAuthenticated ? handleUpdateCredentials : handleAuthenticate}
                variant="contained"
                disabled={isAnyActionInProgress || !apiToken.trim()}
                startIcon={saving ? <CircularProgress size={14} sx={{ color: 'inherit' }} /> : <Iconify icon={saveIcon} width={16} />}
                sx={{ textTransform: 'none', borderRadius: 1, px: 2.5, boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
              >
                {saving ? 'Saving...' : isAuthenticated ? 'Update Credentials' : 'Authenticate'}
              </Button>
            ) : null
          ) : (
            <Button
              onClick={handleCreateFromCatalog}
              variant="contained"
              disabled={isAnyActionInProgress}
              startIcon={saving ? <CircularProgress size={14} sx={{ color: 'inherit' }} /> : <Iconify icon={saveIcon} width={16} />}
              sx={{ textTransform: 'none', borderRadius: 1, px: 2.5, boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
            >
              {saving ? 'Creating...' : 'Create Instance'}
            </Button>
          )}
        </Box>
      </DialogActions>
    </Dialog>
  );
};

// ============================================================================
// Instance Card (My MCP Servers tab)
// ============================================================================

interface McpServerCardProps {
  server: MCPServerInstance;
  isAdmin?: boolean;
  onRefresh?: (showLoader?: boolean) => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

const McpServerCard: React.FC<McpServerCardProps> = ({ server, isAdmin = false, onRefresh, onShowToast }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [configOpen, setConfigOpen] = useState(false);
  const serverImage = server.iconPath || DEFAULT_ICON;
  const isAuthenticated = server.isAuthenticated ?? false;

  const getStatusConfig = () => {
    if (isAuthenticated) {
      return {
        label: 'Authenticated',
        color: theme.palette.success.main,
        bgColor: isDark ? alpha(theme.palette.success.main, 0.8) : alpha(theme.palette.success.main, 0.1),
        icon: checkCircleIcon,
      };
    }
    if (server.isConfigured !== false) {
      return {
        label: 'Not Authenticated',
        color: theme.palette.warning.main,
        bgColor: isDark ? alpha(theme.palette.warning.main, 0.8) : alpha(theme.palette.warning.main, 0.1),
        icon: clockCircleIcon,
      };
    }
    return {
      label: 'Setup Required',
      color: theme.palette.text.secondary,
      bgColor: isDark ? alpha(theme.palette.text.secondary, 0.8) : alpha(theme.palette.text.secondary, 0.08),
      icon: settingsIcon,
    };
  };

  const statusConfig = getStatusConfig();

  return (
    <>
      <Card
        elevation={0}
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          border: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
          cursor: 'pointer',
          transition: theme.transitions.create(['transform', 'box-shadow', 'border-color'], {
            duration: theme.transitions.duration.shorter,
            easing: theme.transitions.easing.easeOut,
          }),
          position: 'relative',
          '&:hover': {
            transform: 'translateY(-2px)',
            borderColor: alpha(theme.palette.primary.main, 0.5),
            boxShadow: isDark
              ? `0 8px 32px ${alpha('#000', 0.3)}`
              : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
            '& .server-avatar': { transform: 'scale(1.05)' },
          },
        }}
        onClick={() => setConfigOpen(true)}
      >
        {/* Transport Badge */}
        {server.transport && (
          <Box
            sx={{
              position: 'absolute',
              top: 8,
              left: 8,
              px: 0.75,
              py: 0.25,
              borderRadius: 0.75,
              fontSize: '0.6875rem',
              fontWeight: 600,
              color: theme.palette.text.secondary,
              backgroundColor: alpha(theme.palette.text.secondary, 0.08),
              border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
              textTransform: 'uppercase',
            }}
          >
            {server.transport}
          </Box>
        )}

        {isAuthenticated && (
          <Box
            sx={{
              position: 'absolute',
              top: 12,
              right: 12,
              width: 6,
              height: 6,
              borderRadius: '50%',
              backgroundColor: theme.palette.success.main,
              boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
            }}
          />
        )}

        <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', height: '100%', gap: 1.5, '&:last-child': { pb: 2 } }}>
          <Stack spacing={1.5} alignItems="center">
            <Avatar
              className="server-avatar"
              sx={{
                width: 48,
                height: 48,
                backgroundColor: isDark ? alpha(theme.palette.common.white, 0.9) : alpha(theme.palette.grey[100], 0.8),
                border: `1px solid ${theme.palette.divider}`,
                transition: theme.transitions.create('transform'),
              }}
            >
              <img
                src={serverImage}
                alt={server.displayName}
                width={24}
                height={24}
                style={{ objectFit: 'contain' }}
                onError={(e) => {
                  (e.target as HTMLImageElement).src = DEFAULT_ICON;
                }}
              />
            </Avatar>
            <Box sx={{ textAlign: 'center', width: '100%' }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, color: theme.palette.text.primary, mb: 0.25, lineHeight: 1.2 }}>
                {server.instanceName || server.displayName}
              </Typography>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary, fontSize: '0.75rem' }}>
                {server.displayName}
              </Typography>
              <Typography variant="caption" sx={{ display: 'block', color: theme.palette.text.disabled, fontSize: '0.6875rem' }}>
                {server.serverType}
              </Typography>
            </Box>
          </Stack>

          <Box sx={{ display: 'flex', justifyContent: 'center' }}>
            <Chip
              icon={<Iconify icon={statusConfig.icon} width={14} height={14} />}
              label={statusConfig.label}
              size="small"
              sx={{
                height: 24,
                fontSize: '0.75rem',
                fontWeight: 500,
                backgroundColor: statusConfig.bgColor,
                color: statusConfig.color,
                border: `1px solid ${alpha(statusConfig.color, 0.2)}`,
                '& .MuiChip-icon': { color: statusConfig.color },
              }}
            />
          </Box>

          <Stack direction="row" spacing={0.5} justifyContent="center" alignItems="center" sx={{ minHeight: 20 }}>
            {server.authMode && server.authMode !== 'none' && (
              <Typography
                variant="caption"
                sx={{
                  px: 1,
                  py: 0.25,
                  borderRadius: 0.5,
                  fontSize: '0.6875rem',
                  fontWeight: 500,
                  color: theme.palette.text.secondary,
                  backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                  border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
                }}
              >
                {server.authMode.split('_').join(' ')}
              </Typography>
            )}
            {(server.tools?.length ?? 0) > 0 && (
              <Tooltip title={`${server.tools!.length} tools available`} arrow>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.5,
                    px: 1,
                    py: 0.25,
                    borderRadius: 0.5,
                    fontSize: '0.6875rem',
                    fontWeight: 500,
                    color: theme.palette.info.main,
                    backgroundColor: alpha(theme.palette.info.main, 0.08),
                    border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                  }}
                >
                  <Iconify icon={boltIcon} width={10} height={10} />
                  <Typography variant="caption" sx={{ fontSize: '0.6875rem', fontWeight: 500, color: 'inherit' }}>
                    {server.tools!.length} tools
                  </Typography>
                </Box>
              </Tooltip>
            )}
          </Stack>

          <Button
            fullWidth
            variant="outlined"
            size="medium"
            startIcon={<Iconify icon={eyeIcon} width={16} height={16} />}
            onClick={(e) => {
              e.stopPropagation();
              setConfigOpen(true);
            }}
            sx={{
              mt: 'auto',
              height: 38,
              borderRadius: 1.5,
              textTransform: 'none',
              fontWeight: 600,
              fontSize: '0.8125rem',
              borderColor: alpha(theme.palette.primary.main, 0.3),
              '&:hover': { borderColor: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, 0.04) },
            }}
          >
            {isAuthenticated ? 'Manage' : 'Authenticate'}
          </Button>
        </CardContent>
      </Card>

      {configOpen && (
        <McpServerConfigDialog
          instance={server}
          isAdmin={isAdmin}
          onClose={() => setConfigOpen(false)}
          onSuccess={() => {
            setConfigOpen(false);
            onRefresh?.(false);
          }}
          onShowToast={onShowToast}
        />
      )}
    </>
  );
};

// ============================================================================
// Catalog Card (Available tab)
// ============================================================================

interface McpServerCatalogCardProps {
  template: MCPServerTemplate;
  isConfigured?: boolean;
  isAdmin?: boolean;
  onRefresh?: (showLoader?: boolean, forceRefreshBoth?: boolean) => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

const McpServerCatalogCard: React.FC<McpServerCatalogCardProps> = ({
  template,
  isConfigured = false,
  isAdmin = false,
  onRefresh,
  onShowToast,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [configOpen, setConfigOpen] = useState(false);
  const serverImage = template.iconPath || DEFAULT_ICON;

  return (
    <>
      <Card
        elevation={0}
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          border: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
          cursor: 'pointer',
          transition: theme.transitions.create(['transform', 'box-shadow', 'border-color'], {
            duration: theme.transitions.duration.shorter,
            easing: theme.transitions.easing.easeOut,
          }),
          position: 'relative',
          '&:hover': {
            transform: 'translateY(-2px)',
            borderColor: alpha(theme.palette.primary.main, 0.5),
            boxShadow: isDark
              ? `0 8px 32px ${alpha('#000', 0.3)}`
              : `0 8px 32px ${alpha(theme.palette.primary.main, 0.12)}`,
            '& .server-avatar': { transform: 'scale(1.05)' },
          },
        }}
        onClick={() => setConfigOpen(true)}
      >
        {/* Transport Badge */}
        <Box
          sx={{
            position: 'absolute',
            top: 8,
            left: 8,
            px: 0.75,
            py: 0.25,
            borderRadius: 0.75,
            fontSize: '0.6875rem',
            fontWeight: 600,
            color: theme.palette.text.secondary,
            backgroundColor: alpha(theme.palette.text.secondary, 0.08),
            border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
            textTransform: 'uppercase',
          }}
        >
          {template.transport}
        </Box>

        {isConfigured && (
          <Box
            sx={{
              position: 'absolute',
              top: 12,
              right: 12,
              width: 6,
              height: 6,
              borderRadius: '50%',
              backgroundColor: theme.palette.success.main,
              boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
            }}
          />
        )}

        <CardContent sx={{ p: 2, display: 'flex', flexDirection: 'column', height: '100%', gap: 1.5, '&:last-child': { pb: 2 } }}>
          <Stack spacing={1.5} alignItems="center">
            <Avatar
              className="server-avatar"
              sx={{
                width: 48,
                height: 48,
                backgroundColor: isDark ? alpha(theme.palette.common.white, 0.9) : alpha(theme.palette.grey[100], 0.8),
                border: `1px solid ${theme.palette.divider}`,
                transition: theme.transitions.create('transform'),
              }}
            >
              <img
                src={serverImage}
                alt={template.displayName}
                width={24}
                height={24}
                style={{ objectFit: 'contain' }}
                onError={(e) => {
                  (e.target as HTMLImageElement).src = DEFAULT_ICON;
                }}
              />
            </Avatar>
            <Box sx={{ textAlign: 'center', width: '100%' }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, color: theme.palette.text.primary, mb: 0.25, lineHeight: 1.2 }}>
                {template.displayName}
              </Typography>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary, fontSize: '0.8125rem' }}>
                {template.tags?.[0] || template.authMode}
              </Typography>
            </Box>
          </Stack>

          <Typography
            variant="caption"
            sx={{
              color: theme.palette.text.secondary,
              fontSize: '0.75rem',
              textAlign: 'center',
              minHeight: 32,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {template.description || 'No description available'}
          </Typography>

          <Stack direction="row" spacing={0.5} justifyContent="center" alignItems="center" sx={{ minHeight: 20 }}>
            {template.authMode && template.authMode !== 'none' && (
              <Typography
                variant="caption"
                sx={{
                  px: 1,
                  py: 0.25,
                  borderRadius: 0.5,
                  fontSize: '0.6875rem',
                  fontWeight: 500,
                  color: theme.palette.text.secondary,
                  backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                  border: `1px solid ${alpha(theme.palette.text.secondary, 0.12)}`,
                }}
              >
                {template.authMode.split('_').join(' ')}
              </Typography>
            )}
            {template.tags && template.tags.length > 1 && (
              <Typography
                variant="caption"
                sx={{
                  px: 1,
                  py: 0.25,
                  borderRadius: 0.5,
                  fontSize: '0.6875rem',
                  fontWeight: 500,
                  color: theme.palette.info.main,
                  backgroundColor: alpha(theme.palette.info.main, 0.08),
                  border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                }}
              >
                {template.tags.length} tags
              </Typography>
            )}
          </Stack>

          <Button
            fullWidth
            variant="outlined"
            size="medium"
            startIcon={<Iconify icon={plusCircleIcon} width={16} height={16} />}
            onClick={(e) => {
              e.stopPropagation();
              setConfigOpen(true);
            }}
            sx={{
              mt: 'auto',
              height: 38,
              borderRadius: 1.5,
              textTransform: 'none',
              fontWeight: 600,
              fontSize: '0.8125rem',
              borderColor: alpha(theme.palette.primary.main, 0.3),
              '&:hover': { borderColor: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, 0.04) },
            }}
          >
            Configure Server
          </Button>
        </CardContent>
      </Card>

      {configOpen && (
        <McpServerConfigDialog
          template={template}
          isAdmin={isAdmin}
          onClose={() => setConfigOpen(false)}
          onSuccess={() => {
            setConfigOpen(false);
            onRefresh?.(false, true);
          }}
          onShowToast={onShowToast}
        />
      )}
    </>
  );
};

// ============================================================================
// Custom MCP Server Dialog
// ============================================================================

interface CustomMcpServerDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

const TRANSPORT_OPTIONS = [
  { value: 'stdio', label: 'Standard I/O (stdio)', description: 'Local process — provide a command and arguments' },
  { value: 'streamable_http', label: 'Streamable HTTP', description: 'Remote server — provide a URL endpoint' },
  { value: 'sse', label: 'Server-Sent Events (SSE)', description: 'Remote server via SSE — provide a URL endpoint' },
];

const AUTH_MODE_OPTIONS = [
  { value: 'none', label: 'None', description: 'No authentication required' },
  { value: 'api_token', label: 'API Token', description: 'Token passed as environment variable or header' },
  { value: 'oauth', label: 'OAuth 2.0', description: 'OAuth authorization code flow' },
  { value: 'headers', label: 'Custom Headers', description: 'Static authorization headers' },
];

const CustomMcpServerDialog: React.FC<CustomMcpServerDialogProps> = ({
  open,
  onClose,
  onSuccess,
  onShowToast,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const outlinedInputSx = { borderRadius: 1.25 } as const;
  const inputLabelSx = { fontSize: '0.875rem', fontWeight: 500 } as const;
  const inputTextSx = { fontSize: '0.875rem', padding: '10.5px 14px', fontWeight: 400 } as const;
  const helperTextSx = { fontSize: '0.75rem', fontWeight: 400, marginTop: 0.75, marginLeft: 1 } as const;

  const [instanceName, setInstanceName] = useState('');
  const [description, setDescription] = useState('');
  const [transport, setTransport] = useState('stdio');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [url, setUrl] = useState('');
  const [authMode, setAuthMode] = useState('none');
  const [envVars, setEnvVars] = useState('');
  const [headerKey, setHeaderKey] = useState('Authorization');
  const [headerValue, setHeaderValue] = useState('');

  // OAuth fields
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isHttpTransport = transport === 'streamable_http' || transport === 'sse';

  const handleCreate = async () => {
    setError(null);

    if (!instanceName.trim()) {
      setError('Instance name is required');
      return;
    }
    if (transport === 'stdio' && !command.trim()) {
      setError('Command is required for stdio transport');
      return;
    }
    if (isHttpTransport && !url.trim()) {
      setError('URL is required for HTTP transport');
      return;
    }
    if (authMode === 'oauth' && !clientId.trim()) {
      setError('Client ID is required for OAuth authentication');
      return;
    }

    try {
      setSaving(true);

      const parsedArgs = args
        .split(/\s+/)
        .map((a) => a.trim())
        .filter(Boolean);

      const requiredEnv: string[] = [];
      if (envVars.trim()) {
        envVars.split(',').forEach((v) => {
          const trimmed = v.trim();
          if (trimmed) requiredEnv.push(trimmed);
        });
      }

      const body: Parameters<typeof McpServerApi.createInstance>[0] = {
        instanceName: instanceName.trim(),
        serverType: 'custom',
        displayName: instanceName.trim(),
        description: description.trim(),
        transport,
        authMode,
        supportedAuthTypes: authMode === 'none' ? [] : [authMode.toUpperCase()],
      };

      if (transport === 'stdio') {
        body.command = command.trim();
        body.args = parsedArgs;
        body.requiredEnv = requiredEnv;
      } else {
        body.url = url.trim();
      }

      if (authMode === 'oauth' && clientId.trim()) {
        body.clientId = clientId.trim();
        body.clientSecret = clientSecret.trim();
      }

      const result = await McpServerApi.createInstance(body);

      const newInstanceId = (result.instance as any)?._id || result.instance?.instanceId;
      if (authMode === 'headers' && headerValue.trim() && newInstanceId) {
        try {
          await McpServerApi.authenticateInstance(newInstanceId, {
            headerName: headerKey.trim() || 'Authorization',
            headerValue: headerValue.trim(),
            apiToken: headerValue.trim(),
          });
        } catch {
          // non-fatal: instance created, auth can be done later
        }
      }

      onShowToast?.('Custom MCP server created', 'success');
      onSuccess();
      onClose();
    } catch (err: any) {
      setError(err.response?.data?.detail || err.response?.data?.message || 'Failed to create server');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          maxHeight: '85vh',
        },
      }}
    >
      <DialogTitle sx={{ pb: 1.5, pr: 6 }}>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Box
            sx={{
              width: 36,
              height: 36,
              borderRadius: 1.5,
              backgroundColor: alpha(theme.palette.primary.main, 0.1),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Iconify icon={plusCircleIcon} width={20} sx={{ color: theme.palette.primary.main }} />
          </Box>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700, fontSize: '1.125rem' }}>
              Add Custom MCP Server
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
              Connect any MCP-compatible server
            </Typography>
          </Box>
        </Stack>
        <IconButton
          onClick={onClose}
          sx={{ position: 'absolute', right: 8, top: 8, color: theme.palette.text.secondary }}
        >
          <Iconify icon={closeIcon} width={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ px: 3, py: 2.5 }}>
        <Stack spacing={2.5}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)} sx={{ borderRadius: 1.5 }}>
              {error}
            </Alert>
          )}

          {/* Basic Info */}
          <TextField
            label="Server Name"
            value={instanceName}
            onChange={(e) => setInstanceName(e.target.value)}
            required
            fullWidth
            size="small"
            placeholder="e.g. My GitHub MCP Server"
            sx={{
              '& .MuiOutlinedInput-root': outlinedInputSx,
              '& .MuiInputLabel-root': inputLabelSx,
              '& .MuiOutlinedInput-input': inputTextSx,
            }}
          />

          <TextField
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            fullWidth
            size="small"
            placeholder="Optional description"
            multiline
            rows={2}
            sx={{
              '& .MuiOutlinedInput-root': outlinedInputSx,
              '& .MuiInputLabel-root': inputLabelSx,
            }}
          />

          <Divider sx={{ my: 0.5 }} />

          {/* Transport */}
          <FormControl fullWidth size="small">
            <InputLabel sx={inputLabelSx}>Transport</InputLabel>
            <Select
              value={transport}
              label="Transport"
              onChange={(e) => setTransport(e.target.value)}
              sx={{ borderRadius: 1.25, fontSize: '0.875rem' }}
            >
              {TRANSPORT_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  <Stack>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>{opt.label}</Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6875rem' }}>
                      {opt.description}
                    </Typography>
                  </Stack>
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {/* stdio fields */}
          {transport === 'stdio' && (
            <>
              <TextField
                label="Command"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                required
                fullWidth
                size="small"
                placeholder="e.g. npx, uvx, docker"
                helperText="The executable to run"
                sx={{
                  '& .MuiOutlinedInput-root': outlinedInputSx,
                  '& .MuiInputLabel-root': inputLabelSx,
                  '& .MuiOutlinedInput-input': inputTextSx,
                  '& .MuiFormHelperText-root': helperTextSx,
                }}
              />
              <TextField
                label="Arguments"
                value={args}
                onChange={(e) => setArgs(e.target.value)}
                fullWidth
                size="small"
                placeholder="e.g. -y @modelcontextprotocol/server-github"
                helperText="Space-separated arguments passed to the command"
                sx={{
                  '& .MuiOutlinedInput-root': outlinedInputSx,
                  '& .MuiInputLabel-root': inputLabelSx,
                  '& .MuiOutlinedInput-input': inputTextSx,
                  '& .MuiFormHelperText-root': helperTextSx,
                }}
              />
              <TextField
                label="Required Environment Variables"
                value={envVars}
                onChange={(e) => setEnvVars(e.target.value)}
                fullWidth
                size="small"
                placeholder="e.g. GITHUB_TOKEN, CUSTOM_API_KEY"
                helperText="Comma-separated env var names the server needs"
                sx={{
                  '& .MuiOutlinedInput-root': outlinedInputSx,
                  '& .MuiInputLabel-root': inputLabelSx,
                  '& .MuiOutlinedInput-input': inputTextSx,
                  '& .MuiFormHelperText-root': helperTextSx,
                }}
              />
            </>
          )}

          {/* HTTP fields */}
          {isHttpTransport && (
            <TextField
              label="Server URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              fullWidth
              size="small"
              placeholder="e.g. https://mcp.example.com/v1/mcp"
              helperText="The HTTP endpoint for the MCP server"
              sx={{
                '& .MuiOutlinedInput-root': outlinedInputSx,
                '& .MuiInputLabel-root': inputLabelSx,
                '& .MuiOutlinedInput-input': inputTextSx,
                '& .MuiFormHelperText-root': helperTextSx,
              }}
            />
          )}

          <Divider sx={{ my: 0.5 }} />

          {/* Auth Mode */}
          <FormControl fullWidth size="small">
            <InputLabel sx={inputLabelSx}>Authentication</InputLabel>
            <Select
              value={authMode}
              label="Authentication"
              onChange={(e) => setAuthMode(e.target.value)}
              sx={{ borderRadius: 1.25, fontSize: '0.875rem' }}
            >
              {AUTH_MODE_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  <Stack>
                    <Typography variant="body2" sx={{ fontWeight: 500 }}>{opt.label}</Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6875rem' }}>
                      {opt.description}
                    </Typography>
                  </Stack>
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          {/* OAuth fields */}
          {authMode === 'oauth' && (
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                  OAuth App Credentials
                </Typography>
                <TextField
                  label="Client ID"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  required
                  fullWidth
                  size="small"
                  sx={{
                    '& .MuiOutlinedInput-root': outlinedInputSx,
                    '& .MuiInputLabel-root': inputLabelSx,
                    '& .MuiOutlinedInput-input': inputTextSx,
                  }}
                />
                <TextField
                  label="Client Secret"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  type="password"
                  fullWidth
                  size="small"
                  sx={{
                    '& .MuiOutlinedInput-root': outlinedInputSx,
                    '& .MuiInputLabel-root': inputLabelSx,
                    '& .MuiOutlinedInput-input': inputTextSx,
                  }}
                />
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6875rem' }}>
                  Users will authenticate via OAuth after the server is created. The authorization/token URLs will be discovered from the MCP server or can be configured later.
                </Typography>
              </Stack>
            </Paper>
          )}

          {/* Headers auth fields */}
          {authMode === 'headers' && isHttpTransport && (
            <Paper
              variant="outlined"
              sx={{ p: 2, borderRadius: 1.25, bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper }}
            >
              <Stack spacing={2}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                  Authorization Header
                </Typography>
                <TextField
                  label="Header Name"
                  value={headerKey}
                  onChange={(e) => setHeaderKey(e.target.value)}
                  fullWidth
                  size="small"
                  placeholder="Authorization"
                  sx={{
                    '& .MuiOutlinedInput-root': outlinedInputSx,
                    '& .MuiInputLabel-root': inputLabelSx,
                    '& .MuiOutlinedInput-input': inputTextSx,
                  }}
                />
                <TextField
                  label="Header Value"
                  value={headerValue}
                  onChange={(e) => setHeaderValue(e.target.value)}
                  type="password"
                  fullWidth
                  size="small"
                  placeholder="Bearer sk-..."
                  helperText="Optional — you can configure this later"
                  sx={{
                    '& .MuiOutlinedInput-root': outlinedInputSx,
                    '& .MuiInputLabel-root': inputLabelSx,
                    '& .MuiOutlinedInput-input': inputTextSx,
                    '& .MuiFormHelperText-root': helperTextSx,
                  }}
                />
              </Stack>
            </Paper>
          )}

          {/* API token hint for stdio */}
          {authMode === 'api_token' && transport === 'stdio' && (
            <Alert severity="info" sx={{ borderRadius: 1.25, fontSize: '0.8125rem' }}>
              The API token will be set as the first required environment variable when users authenticate.
            </Alert>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2, borderTop: `1px solid ${theme.palette.divider}` }}>
        <Button
          onClick={onClose}
          sx={{ textTransform: 'none', borderRadius: 1, color: theme.palette.text.secondary }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleCreate}
          disabled={saving || !instanceName.trim()}
          startIcon={saving ? <CircularProgress size={16} sx={{ color: 'inherit' }} /> : <Iconify icon={plusCircleIcon} width={18} />}
          sx={{ textTransform: 'none', borderRadius: 1, boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
        >
          {saving ? 'Creating...' : 'Create Server'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ============================================================================
// Main Page Component
// ============================================================================

const McpServersPage: React.FC = () => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { isAdmin } = useAdmin();
  const [searchParams, setSearchParams] = useSearchParams();

  // ──────────────────────────────────────────────────────────────────────────
  // STATE
  // ──────────────────────────────────────────────────────────────────────────

  const [activeTab, setActiveTab] = useState<TabValue>('my-servers');

  const [configuredServers, setConfiguredServers] = useState<MCPServerInstance[]>([]);
  const [catalogServers, setCatalogServers] = useState<MCPServerTemplate[]>([]);

  const [configuredTotal, setConfiguredTotal] = useState(0);
  const [catalogTotal, setCatalogTotal] = useState(0);

  const [hasMoreConfigured, setHasMoreConfigured] = useState(false);
  const [hasMoreCatalog, setHasMoreCatalog] = useState(false);

  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [isRefetching, setIsRefetching] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  const [searchInput, setSearchInput] = useState('');
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedFilter, setSelectedFilter] = useState<FilterType>('all');

  const [snackbar, setSnackbar] = useState<SnackbarState>({ open: false, message: '', severity: 'success' });
  const [customDialogOpen, setCustomDialogOpen] = useState(false);

  // ──────────────────────────────────────────────────────────────────────────
  // REFS
  // ──────────────────────────────────────────────────────────────────────────

  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const requestIdRef = useRef(0);

  const configuredPageRef = useRef(INITIAL_PAGE);
  const catalogPageRef = useRef(INITIAL_PAGE);

  const isLoadingMoreRef = useRef(false);
  const isRefetchingRef = useRef(false);
  const hasMoreConfiguredRef = useRef(false);
  const hasMoreCatalogRef = useRef(false);

  const activeSearchRef = useRef('');
  const selectedFilterRef = useRef<FilterType>('all');
  const activeTabRef = useRef<TabValue>('my-servers');

  const isReadyRef = useRef(false);

  const configuredObserverRef = useRef<IntersectionObserver | null>(null);
  const catalogObserverRef = useRef<IntersectionObserver | null>(null);

  // ──────────────────────────────────────────────────────────────────────────
  // COMPUTED VALUES
  // ──────────────────────────────────────────────────────────────────────────

  const configuredServersMap = useMemo(() => {
    const map = new Map<string, MCPServerInstance[]>();
    configuredServers.forEach((s) => {
      const key = s.serverType?.toLowerCase() || '';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(s);
    });
    return map;
  }, [configuredServers]);

  const isServerConfigured = useCallback(
    (typeId: string): boolean => {
      const instances = configuredServersMap.get(typeId.toLowerCase());
      return !!(instances && instances.some((inst) => inst.isAuthenticated));
    },
    [configuredServersMap]
  );

  const filterOptions = useMemo(
    () => [
      { key: 'all' as FilterType, label: 'All', icon: listIcon },
      { key: 'authenticated' as FilterType, label: 'Authenticated', icon: checkCircleIcon },
      { key: 'not-authenticated' as FilterType, label: 'Not Authenticated', icon: alertCircleIcon },
    ],
    []
  );

  const loadingSkeletons = useMemo(() => Array.from({ length: SKELETON_COUNT }, (_, i) => i), []);
  const loadMoreSkeletons = useMemo(() => Array.from({ length: LOAD_MORE_SKELETON_COUNT }, (_, i) => i), []);

  // ──────────────────────────────────────────────────────────────────────────
  // CORE FETCHERS
  // ──────────────────────────────────────────────────────────────────────────

  const fetchConfiguredPage1 = useCallback(async (mode: 'initial' | 'refetch') => {
    if (mode === 'initial') {
      isReadyRef.current = false;
      setIsInitialLoad(true);
    } else {
      isRefetchingRef.current = true;
      setIsRefetching(true);
    }

    requestIdRef.current += 1;
    const reqId = requestIdRef.current;
    configuredPageRef.current = INITIAL_PAGE;

    try {
      const authStatus =
        selectedFilterRef.current !== 'all'
          ? selectedFilterRef.current
          : undefined;

      const { mcpServers, pagination } = await McpServerApi.getMyMcpServers({
        search: activeSearchRef.current || undefined,
        authStatus,
        page: 1,
        limit: ITEMS_PER_PAGE,
      });

      if (reqId !== requestIdRef.current) return;

      setConfiguredServers(mcpServers);
      setConfiguredTotal(pagination.total);
      const hasNext = pagination.page < pagination.totalPages;
      hasMoreConfiguredRef.current = hasNext;
      setHasMoreConfigured(hasNext);
      isReadyRef.current = true;
    } catch (error) {
      console.error('Failed to load MCP servers:', error);
      setSnackbar({ open: true, message: 'Failed to load MCP servers. Please try again.', severity: 'error' });
    } finally {
      setIsInitialLoad(false);
      isRefetchingRef.current = false;
      setIsRefetching(false);
    }
  }, []);

  const fetchCatalogPage1 = useCallback(async (mode: 'initial' | 'refetch') => {
    if (mode === 'initial') {
      isReadyRef.current = false;
      setIsInitialLoad(true);
    } else {
      isRefetchingRef.current = true;
      setIsRefetching(true);
    }

    requestIdRef.current += 1;
    const reqId = requestIdRef.current;
    catalogPageRef.current = INITIAL_PAGE;

    try {
      const result = await McpServerApi.getCatalog({
        search: activeSearchRef.current || undefined,
        page: 1,
        limit: ITEMS_PER_PAGE,
      });

      if (reqId !== requestIdRef.current) return;

      setCatalogServers(result.items || []);
      setCatalogTotal(result.total);
      const hasNext = result.page < result.totalPages;
      hasMoreCatalogRef.current = hasNext;
      setHasMoreCatalog(hasNext);
      isReadyRef.current = true;
    } catch (error) {
      console.error('Failed to load MCP server catalog:', error);
      setSnackbar({ open: true, message: 'Failed to load catalog. Please try again.', severity: 'error' });
    } finally {
      setIsInitialLoad(false);
      isRefetchingRef.current = false;
      setIsRefetching(false);
    }
  }, []);

  // ──────────────────────────────────────────────────────────────────────────
  // LOAD MORE
  // ──────────────────────────────────────────────────────────────────────────

  const loadMoreConfigured = useCallback(async () => {
    if (
      !isReadyRef.current ||
      isLoadingMoreRef.current ||
      isRefetchingRef.current ||
      !hasMoreConfiguredRef.current ||
      activeTabRef.current !== 'my-servers'
    )
      return;

    isLoadingMoreRef.current = true;
    setIsLoadingMore(true);

    const nextPage = configuredPageRef.current + 1;
    try {
      const authStatus =
        selectedFilterRef.current !== 'all'
          ? selectedFilterRef.current
          : undefined;

      const { mcpServers, pagination } = await McpServerApi.getMyMcpServers({
        search: activeSearchRef.current || undefined,
        authStatus,
        page: nextPage,
        limit: ITEMS_PER_PAGE,
      });

      configuredPageRef.current = nextPage;
      setConfiguredServers((prev) => [...prev, ...mcpServers]);
      setConfiguredTotal(pagination.total);
      const hasNext = pagination.page < pagination.totalPages;
      hasMoreConfiguredRef.current = hasNext;
      setHasMoreConfigured(hasNext);
    } catch (error) {
      console.error('Failed to load more MCP servers:', error);
    } finally {
      isLoadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, []);

  const loadMoreCatalog = useCallback(async () => {
    if (
      !isReadyRef.current ||
      isLoadingMoreRef.current ||
      isRefetchingRef.current ||
      !hasMoreCatalogRef.current ||
      activeTabRef.current !== 'available'
    )
      return;

    isLoadingMoreRef.current = true;
    setIsLoadingMore(true);

    const nextPage = catalogPageRef.current + 1;
    try {
      const result = await McpServerApi.getCatalog({
        search: activeSearchRef.current || undefined,
        page: nextPage,
        limit: ITEMS_PER_PAGE,
      });

      catalogPageRef.current = nextPage;
      const newItems = result.items || [];
      setCatalogServers((prev) => {
        const existingIds = new Set(prev.map((t) => t.typeId));
        return [...prev, ...newItems.filter((t) => !existingIds.has(t.typeId))];
      });
      setCatalogTotal(result.total);
      const hasNext = result.page < result.totalPages;
      hasMoreCatalogRef.current = hasNext;
      setHasMoreCatalog(hasNext);
    } catch (error) {
      console.error('Failed to load more catalog items:', error);
    } finally {
      isLoadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, []);

  // ──────────────────────────────────────────────────────────────────────────
  // SENTINEL CALLBACK REFS
  // ──────────────────────────────────────────────────────────────────────────

  const setSentinelConfigured = useCallback(
    (node: HTMLDivElement | null) => {
      if (configuredObserverRef.current) {
        configuredObserverRef.current.disconnect();
        configuredObserverRef.current = null;
      }
      if (!node) return;
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting) loadMoreConfigured();
        },
        { rootMargin: '300px', threshold: 0 }
      );
      observer.observe(node);
      configuredObserverRef.current = observer;
    },
    [loadMoreConfigured]
  );

  const setSentinelCatalog = useCallback(
    (node: HTMLDivElement | null) => {
      if (catalogObserverRef.current) {
        catalogObserverRef.current.disconnect();
        catalogObserverRef.current = null;
      }
      if (!node) return;
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting) loadMoreCatalog();
        },
        { rootMargin: '300px', threshold: 0 }
      );
      observer.observe(node);
      catalogObserverRef.current = observer;
    },
    [loadMoreCatalog]
  );

  // ──────────────────────────────────────────────────────────────────────────
  // INITIAL LOAD
  // ──────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    const tabParam = searchParams.get('tab');
    const initialTab: TabValue = tabParam === 'available' ? 'available' : 'my-servers';
    activeTabRef.current = initialTab;
    setActiveTab(initialTab);

    if (!tabParam) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('tab', initialTab);
          return next;
        },
        { replace: true }
      );
    }

    if (initialTab === 'my-servers') {
      fetchConfiguredPage1('initial');
      if (isAdmin) {
        McpServerApi.getCatalog({ page: 1, limit: 1 })
          .then((r) => setCatalogTotal(r.total))
          .catch(() => {});
      }
    } else {
      fetchCatalogPage1('initial');
      McpServerApi.getMyMcpServers({ page: 1, limit: 1 })
        .then((r) => setConfiguredTotal(r.pagination.total))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // OAuth callback is now handled by the dedicated callback page
  // at /dashboard/mcp-servers/oauth/callback/:serverType (no sessionStorage needed)

  // ──────────────────────────────────────────────────────────────────────────
  // DEBOUNCED SEARCH
  // ──────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => {
      const trimmed = searchInput.trim();
      const prev = activeSearchRef.current;
      activeSearchRef.current = trimmed;
      setActiveSearchQuery(trimmed);

      if (trimmed !== prev) {
        isReadyRef.current = false;
        if (activeTabRef.current === 'my-servers') {
          fetchConfiguredPage1('refetch');
        } else {
          fetchCatalogPage1('refetch');
        }
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  // ──────────────────────────────────────────────────────────────────────────
  // EVENT HANDLERS
  // ──────────────────────────────────────────────────────────────────────────

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newTab: TabValue) => {
      if (newTab === activeTabRef.current) return;

      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('tab', newTab);
          return next;
        },
        { replace: true }
      );

      setSearchInput('');
      setActiveSearchQuery('');
      activeSearchRef.current = '';
      setSelectedFilter('all');
      selectedFilterRef.current = 'all';

      activeTabRef.current = newTab;
      setActiveTab(newTab);
      isReadyRef.current = false;

      if (newTab === 'my-servers') {
        setConfiguredServers([]);
        hasMoreConfiguredRef.current = false;
        setHasMoreConfigured(false);
        fetchConfiguredPage1('initial');
      } else {
        setCatalogServers([]);
        hasMoreCatalogRef.current = false;
        setHasMoreCatalog(false);
        fetchCatalogPage1('initial');
      }
    },
    [fetchConfiguredPage1, fetchCatalogPage1, setSearchParams]
  );

  const refreshAllData = useCallback(
    async (showLoader = true, forceRefreshBoth = false) => {
      if (isRefetchingRef.current || isLoadingMoreRef.current) return;
      const mode = showLoader ? 'initial' : 'refetch';

      if (forceRefreshBoth || activeTabRef.current === 'my-servers') {
        await fetchConfiguredPage1(mode);
      } else {
        await fetchCatalogPage1(mode);
      }
    },
    [fetchConfiguredPage1, fetchCatalogPage1]
  );

  const handleFilterChange = useCallback(
    (filter: FilterType) => {
      setSelectedFilter(filter);
      selectedFilterRef.current = filter;
      isReadyRef.current = false;
      fetchConfiguredPage1('refetch');
    },
    [fetchConfiguredPage1]
  );

  const handleClearSearch = useCallback(() => {
    setSearchInput('');
  }, []);

  const handleCloseSnackbar = useCallback(() => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  }, []);

  const handleShowToast = useCallback(
    (message: string, severity: 'success' | 'error' | 'info' | 'warning' = 'success') => {
      setSnackbar({ open: true, message, severity });
    },
    []
  );

  // ──────────────────────────────────────────────────────────────────────────
  // RENDER HELPERS
  // ──────────────────────────────────────────────────────────────────────────

  const renderServerCard = useCallback(
    (server: MCPServerInstance) => (
      <McpServerCard
        key={server.instanceId}
        server={server}
        isAdmin={isAdmin}
        onRefresh={refreshAllData}
        onShowToast={handleShowToast}
      />
    ),
    [isAdmin, refreshAllData, handleShowToast]
  );

  const renderCatalogCard = useCallback(
    (template: MCPServerTemplate) => {
      const isConfigured = isServerConfigured(template.typeId);
      return (
        <McpServerCatalogCard
          key={template.typeId}
          template={template}
          isConfigured={isConfigured}
          isAdmin={isAdmin}
          onRefresh={refreshAllData}
          onShowToast={handleShowToast}
        />
      );
    },
    [isServerConfigured, isAdmin, refreshAllData, handleShowToast]
  );

  // ============================================================================
  // RENDER
  // ============================================================================

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Box
        sx={{
          borderRadius: 2,
          backgroundColor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
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
                  icon={SERVER_ICON}
                  width={20}
                  height={20}
                  sx={{ color: theme.palette.primary.main }}
                />
              </Box>
              <Box>
                <Typography
                  variant="h5"
                  sx={{ fontWeight: 700, fontSize: '1.5rem', color: theme.palette.text.primary }}
                >
                  MCP Servers
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ color: theme.palette.text.secondary, fontSize: '0.875rem' }}
                >
                  Configure and manage your MCP server integrations
                </Typography>
              </Box>
            </Stack>

            <Stack direction="row" alignItems="center" spacing={1}>
              {isAdmin && (
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<Iconify icon={plusCircleIcon} width={18} />}
                  onClick={() => setCustomDialogOpen(true)}
                  sx={{
                    textTransform: 'none',
                    borderRadius: 1.5,
                    fontWeight: 600,
                    fontSize: '0.8125rem',
                    boxShadow: 'none',
                    '&:hover': { boxShadow: 'none' },
                  }}
                >
                  Add Custom Server
                </Button>
              )}
              <Tooltip title="Refresh">
                <IconButton
                  onClick={(e) => {
                    e.preventDefault();
                    refreshAllData();
                  }}
                  disabled={isInitialLoad || isLoadingMore}
                >
                  <Iconify
                    icon={refreshIcon}
                    width={20}
                    height={20}
                    sx={{
                      animation: isInitialLoad || isLoadingMore ? 'spin 1s linear infinite' : 'none',
                      '@keyframes spin': { '0%': { transform: 'rotate(0deg)' }, '100%': { transform: 'rotate(360deg)' } },
                    }}
                  />
                </IconButton>
              </Tooltip>
            </Stack>
          </Stack>

          {/* Tabs */}
          <Box sx={{ borderBottom: 1, borderColor: 'divider', mt: 2 }}>
            <Tabs
              value={activeTab}
              onChange={handleTabChange}
              sx={{ '& .MuiTab-root': { textTransform: 'none', fontWeight: 600, minHeight: 48 } }}
            >
              <Tab
                icon={<Iconify icon={checkCircleIcon} width={18} height={18} />}
                iconPosition="start"
                label={
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <span>My MCP Servers</span>
                    {configuredTotal > 0 && (
                      <Chip
                        label={configuredTotal}
                        size="small"
                        sx={{
                          height: 20,
                          fontSize: '0.6875rem',
                          fontWeight: 700,
                          minWidth: 20,
                          '& .MuiChip-label': { px: 0.75 },
                          backgroundColor:
                            activeTab === 'my-servers'
                              ? isDark
                                ? alpha(theme.palette.primary.contrastText, 0.9)
                                : alpha(theme.palette.primary.main, 0.8)
                              : isDark
                              ? alpha(theme.palette.text.primary, 0.4)
                              : alpha(theme.palette.text.primary, 0.12),
                          color:
                            activeTab === 'my-servers'
                              ? theme.palette.primary.contrastText
                              : theme.palette.text.primary,
                          border:
                            activeTab === 'my-servers'
                              ? `1px solid ${alpha(theme.palette.primary.contrastText, 0.3)}`
                              : `1px solid ${alpha(theme.palette.text.primary, 0.2)}`,
                        }}
                      />
                    )}
                  </Box>
                }
                value="my-servers"
                sx={{ mr: 1 }}
              />
              {isAdmin && (
                <Tab
                  icon={<Iconify icon={appsIcon} width={18} height={18} />}
                  iconPosition="start"
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <span>Available</span>
                      {catalogTotal > 0 && (
                        <Chip
                          label={catalogTotal}
                          size="small"
                          sx={{
                            height: 20,
                            fontSize: '0.6875rem',
                            fontWeight: 700,
                            minWidth: 20,
                            '& .MuiChip-label': { px: 0.75 },
                            backgroundColor:
                              activeTab === 'available'
                                ? isDark
                                  ? alpha(theme.palette.primary.contrastText, 0.9)
                                  : alpha(theme.palette.primary.main, 0.8)
                                : isDark
                                ? alpha(theme.palette.text.primary, 0.4)
                                : alpha(theme.palette.text.primary, 0.12),
                            color:
                              activeTab === 'available'
                                ? theme.palette.primary.contrastText
                                : theme.palette.text.primary,
                            border:
                              activeTab === 'available'
                                ? `1px solid ${alpha(theme.palette.primary.contrastText, 0.3)}`
                                : `1px solid ${alpha(theme.palette.text.primary, 0.2)}`,
                          }}
                        />
                      )}
                    </Box>
                  }
                  value="available"
                />
              )}
            </Tabs>
          </Box>
        </Box>

        {/* LinearProgress */}
        <LinearProgress
          sx={{ height: 2, opacity: isRefetching ? 1 : 0, transition: 'opacity 0.2s ease' }}
        />

        {/* Content */}
        <Box sx={{ p: 3 }}>
          {/* Search and Filters */}
          <Stack spacing={2} sx={{ mb: 3 }}>
            <TextField
              placeholder={
                activeTab === 'my-servers'
                  ? 'Search configured MCP servers...'
                  : 'Search available MCP servers...'
              }
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              size="small"
              fullWidth
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={magnifyIcon} width={20} height={20} sx={{ color: theme.palette.text.secondary }} />
                  </InputAdornment>
                ),
                endAdornment: searchInput && (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      onClick={handleClearSearch}
                      edge="end"
                      sx={{ color: theme.palette.text.secondary, '&:hover': { color: theme.palette.text.primary } }}
                    >
                      <Iconify icon={clearIcon} width={18} height={18} />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  height: 48,
                  borderRadius: 1.5,
                  backgroundColor: isDark
                    ? alpha(theme.palette.background.default, 0.4)
                    : theme.palette.background.paper,
                },
              }}
            />

            {/* Auth filter — My Servers only */}
            {activeTab === 'my-servers' && (
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="body2" sx={{ color: theme.palette.text.secondary, fontWeight: 500, mr: 1 }}>
                  Filter:
                </Typography>
                {filterOptions.map((option) => {
                  const isSelected = selectedFilter === option.key;
                  return (
                    <Button
                      key={option.key}
                      variant={isSelected ? 'contained' : 'outlined'}
                      size="small"
                      onClick={() => handleFilterChange(option.key)}
                      startIcon={<Iconify icon={option.icon} width={16} height={16} />}
                      sx={{
                        textTransform: 'none',
                        borderRadius: 1.5,
                        fontWeight: 600,
                        fontSize: '0.8125rem',
                        height: 32,
                        ...(isSelected
                          ? { backgroundColor: theme.palette.primary.main, color: theme.palette.primary.contrastText }
                          : { borderColor: theme.palette.divider, color: theme.palette.text.primary }),
                      }}
                    >
                      {option.label}
                    </Button>
                  );
                })}
              </Stack>
            )}
          </Stack>

          {/* Info Alert */}
          <Alert
            severity="info"
            sx={{
              mb: 3,
              borderRadius: 1.5,
              borderColor: alpha(theme.palette.info.main, 0.2),
              backgroundColor: alpha(theme.palette.info.main, 0.04),
            }}
          >
            <Typography variant="body2">
              {activeTab === 'my-servers' || !isAdmin
                ? 'Authenticate against your MCP server instances. Authenticated servers can be added to your agents.'
                : 'Browse available MCP server types. Administrators can create MCP server instances from here.'}
            </Typography>
          </Alert>

          {/* Tab Content */}
          {(!isAdmin && activeTab !== 'my-servers') ? null : isInitialLoad ? (
            <Grid container spacing={2.5}>
              {loadingSkeletons.map((i) => (
                <Grid item xs={12} sm={6} md={4} lg={3} key={i}>
                  <Skeleton
                    variant="rectangular"
                    height={220}
                    sx={{ borderRadius: 2, animation: 'pulse 1.5s ease-in-out infinite' }}
                  />
                </Grid>
              ))}
            </Grid>
          ) : activeTab === 'my-servers' || !isAdmin ? (
            /* My MCP Servers Tab */
            configuredServers.length === 0 && !isRefetching ? (
              <Fade in timeout={300}>
                <Paper
                  elevation={0}
                  sx={{
                    py: 6,
                    px: 4,
                    textAlign: 'center',
                    borderRadius: 2,
                    border: `1px solid ${theme.palette.divider}`,
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  }}
                >
                  <Box
                    sx={{
                      width: 80,
                      height: 80,
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mx: 'auto',
                      mb: 3,
                    }}
                  >
                    <Iconify
                      icon={activeSearchQuery ? magnifyIcon : linkIcon}
                      width={32}
                      height={32}
                      sx={{ color: theme.palette.text.disabled }}
                    />
                  </Box>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    {activeSearchQuery
                      ? 'No MCP servers found'
                      : selectedFilter !== 'all'
                      ? `No ${selectedFilter} MCP servers`
                      : 'No configured MCP servers'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {activeSearchQuery
                      ? `No MCP servers match "${activeSearchQuery}"`
                      : selectedFilter !== 'all'
                      ? `No MCP servers with status "${selectedFilter}" exist yet.`
                      : 'No MCP server instances are available. Ask your administrator to create instances.'}
                  </Typography>
                </Paper>
              </Fade>
            ) : (
              <>
                <Box sx={{ opacity: isRefetching ? 0.55 : 1, transition: 'opacity 0.2s ease' }}>
                  <Grid container spacing={2.5}>
                    {configuredServers.map((server) => (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={server.instanceId}>
                        {renderServerCard(server)}
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                <Box ref={setSentinelConfigured} sx={{ height: 0 }} />

                {isLoadingMore && activeTab === 'my-servers' && (
                  <Fade in timeout={150}>
                    <Grid container spacing={2.5} sx={{ mt: 1 }}>
                      {loadMoreSkeletons.map((i) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={`lm-cfg-${i}`}>
                          <Skeleton variant="rectangular" height={220} sx={{ borderRadius: 2 }} />
                        </Grid>
                      ))}
                    </Grid>
                  </Fade>
                )}

                {!hasMoreConfigured && configuredServers.length > 0 && !isLoadingMore && (
                  <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ py: 2, opacity: 0.6 }}>
                    All {configuredTotal} server{configuredTotal !== 1 ? 's' : ''} loaded
                  </Typography>
                )}
              </>
            )
          ) : (
            /* Available Tab */
            catalogServers.length === 0 && !isRefetching ? (
              <Fade in timeout={300}>
                <Paper
                  elevation={0}
                  sx={{
                    py: 6,
                    px: 4,
                    textAlign: 'center',
                    borderRadius: 2,
                    border: `1px solid ${theme.palette.divider}`,
                    backgroundColor: alpha(theme.palette.background.default, 0.5),
                  }}
                >
                  <Box
                    sx={{
                      width: 80,
                      height: 80,
                      borderRadius: 2,
                      backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      mx: 'auto',
                      mb: 3,
                    }}
                  >
                    <Iconify
                      icon={activeSearchQuery ? magnifyIcon : appsIcon}
                      width={32}
                      height={32}
                      sx={{ color: theme.palette.text.disabled }}
                    />
                  </Box>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    {activeSearchQuery ? 'No MCP servers found' : 'No MCP servers available'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {activeSearchQuery
                      ? `No MCP servers match "${activeSearchQuery}"`
                      : 'No MCP servers have been registered in the catalog yet'}
                  </Typography>
                </Paper>
              </Fade>
            ) : (
              <>
                <Box sx={{ opacity: isRefetching ? 0.55 : 1, transition: 'opacity 0.2s ease' }}>
                  <Grid container spacing={2.5}>
                    {catalogServers.map((template) => (
                      <Grid item xs={12} sm={6} md={4} lg={3} key={template.typeId}>
                        {renderCatalogCard(template)}
                      </Grid>
                    ))}
                  </Grid>
                </Box>

                <Box ref={setSentinelCatalog} sx={{ height: 0 }} />

                {isLoadingMore && activeTab === 'available' && (
                  <Fade in timeout={150}>
                    <Grid container spacing={2.5} sx={{ mt: 1 }}>
                      {loadMoreSkeletons.map((i) => (
                        <Grid item xs={12} sm={6} md={4} lg={3} key={`lm-cat-${i}`}>
                          <Skeleton variant="rectangular" height={220} sx={{ borderRadius: 2 }} />
                        </Grid>
                      ))}
                    </Grid>
                  </Fade>
                )}

                {!hasMoreCatalog && catalogServers.length > 0 && !isLoadingMore && (
                  <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ py: 2, opacity: 0.6 }}>
                    All {catalogTotal} server{catalogTotal !== 1 ? 's' : ''} loaded
                  </Typography>
                )}
              </>
            )
          )}
        </Box>
      </Box>

      {/* Custom MCP Server Dialog */}
      <CustomMcpServerDialog
        open={customDialogOpen}
        onClose={() => setCustomDialogOpen(false)}
        onSuccess={() => {
          setCustomDialogOpen(false);
          refreshAllData(false, true);
        }}
        onShowToast={handleShowToast}
      />

      {/* Page-level Snackbar */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 8 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{ borderRadius: 1.5, fontWeight: 600 }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default McpServersPage;
