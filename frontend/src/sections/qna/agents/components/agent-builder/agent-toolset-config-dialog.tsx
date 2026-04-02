/**
 * AgentToolsetConfigDialog
 *
 * Manages toolset credentials scoped to a service-account agent.
 * Always operates in MANAGE mode – the agent authenticates against an
 * already-created instance, but its credentials are stored under
 * /services/toolsets/{instanceId}/{agentKey} (not per-user).
 *
 * Supports:
 *   - Non-OAuth (API_TOKEN / BEARER_TOKEN / USERNAME_PASSWORD): enter credentials
 *   - OAuth: open popup → poll for success
 *   - Reauthenticate (clear stored tokens)
 *   - Remove credentials
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
  Alert,
  Snackbar,
  Stack,
  Typography,
  Box,
  Chip,
  CircularProgress,
  Grid,
  alpha,
  useTheme,
  IconButton,
  Paper,
  Skeleton,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import ToolsetApiService from 'src/services/toolset-api';
import type { MyToolset } from 'src/services/toolset-api';
import { FieldRenderer } from 'src/sections/accountdetails/connectors/components/field-renderers';

// Icons
import keyIcon from '@iconify-icons/mdi/key';
import lockIcon from '@iconify-icons/mdi/lock';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import closeIcon from '@iconify-icons/mdi/close';
import saveIcon from '@iconify-icons/eva/save-outline';
import deleteIcon from '@iconify-icons/mdi/delete-outline';
import refreshIcon from '@iconify-icons/mdi/refresh';
import robotIcon from '@iconify-icons/mdi/robot';
import AgentApiService from '../../services/api';

// ============================================================================
// Types
// ============================================================================

export interface AgentToolsetConfigDialogProps {
  /** The toolset instance to manage */
  toolset: MyToolset;
  /** The instance ID to manage credentials for */
  instanceId: string;
  /** The agent key (service account) – credentials stored under /agentKey in etcd */
  agentKey: string;
  onClose: () => void;
  onSuccess: () => void;
  onShowToast?: (message: string, severity?: 'success' | 'error' | 'info' | 'warning') => void;
}

interface ToolsetSchema {
  toolset?: {
    config?: {
      auth?: {
        schemas?: Record<string, { fields: any[]; redirectUri?: string }>;
        [key: string]: any;
      };
    };
    [key: string]: any;
  };
  [key: string]: any;
}

const filterFieldsByUsage = (fields: any[], mode: 'CONFIGURE' | 'AUTHENTICATE'): any[] => {
  if (!Array.isArray(fields)) return [];
  return fields.filter((field: any) => {
    const usage = String(field?.usage || 'BOTH').toUpperCase();
    if (usage === 'BOTH') return true;
    if (mode === 'CONFIGURE') return usage !== 'AUTHENTICATE';
    return usage !== 'CONFIGURE';
  });
};

// ============================================================================
// Component
// ============================================================================

const AgentToolsetConfigDialog: React.FC<AgentToolsetConfigDialogProps> = ({
  toolset,
  instanceId,
  agentKey,
  onClose,
  onSuccess,
  onShowToast,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Derive key info from props
  const authType = toolset.authType ?? 'NONE';
  const displayName = toolset.displayName || toolset.instanceName || 'Toolset';
  const iconPath = toolset.iconPath || '/assets/icons/toolsets/default.svg';
  const category = toolset.category || 'app';
  const tools = toolset.tools || [];

  // ── Schema ──
  const [toolsetSchema, setToolsetSchema] = useState<ToolsetSchema | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(true);

  // ── Credentials form (non-OAuth) ──
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [saveAttempted, setSaveAttempted] = useState(false);

  // ── UI state ──
  const [saving, setSaving] = useState(false);
  const [authenticating, setAuthenticating] = useState(false);
  const [reauthenticating, setReauthenticating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(toolset.isAuthenticated ?? false);

  // Local toast
  const [localToast, setLocalToast] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info' | 'warning';
  }>({ open: false, message: '', severity: 'success' });

  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const showLocalToast = useCallback(
    (message: string, severity: 'success' | 'error' | 'info' | 'warning' = 'success') => {
      setLocalToast({ open: true, message, severity });
      onShowToast?.(message, severity);
    },
    [onShowToast]
  );

  // Cleanup polling on unmount
  useEffect(
    () => () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    },
    []
  );

  // ── Load schema ──
  useEffect(() => {
    const toolsetType = toolset.toolsetType ?? '';
    if (!toolsetType) {
      setSchemaLoading(false);
      return;
    }
    const load = async () => {
      try {
        setSchemaLoading(true);
        const schema = await ToolsetApiService.getToolsetSchema(toolsetType);
        setToolsetSchema((schema as ToolsetSchema) ?? null);
      } catch (err) {
        console.error('Failed to load toolset schema:', err);
      } finally {
        setSchemaLoading(false);
      }
    };
    load();
  }, [toolset.toolsetType]);

  // ── Hydrate form with existing auth values ──
  useEffect(() => {
    if (!toolset.auth || authType === 'OAUTH' || authType === 'NONE') return;
    const existingAuth = toolset.auth;
    const hydrated: Record<string, any> = {};
    (manageAuthSchema.fields || []).forEach((field: any) => {
      const value = (existingAuth as Record<string, any>)[field.name];
      if (value !== undefined && value !== null) {
        hydrated[field.name] = Array.isArray(value) ? value.join(',') : value;
      }
    });
    if (Object.keys(hydrated).length > 0) {
      setFormData((prev) => ({ ...hydrated, ...prev }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolset.auth]);

  // ── Schema helpers ──
  const manageAuthSchema = useMemo(() => {
    if (!toolsetSchema || authType === 'OAUTH' || authType === 'NONE') return { fields: [] };
    const toolsetData = (toolsetSchema as any).toolset || toolsetSchema;
    const authConfig = toolsetData.config?.auth || toolsetData.auth || {};
    const schemas = authConfig.schemas || {};
    const rawSchema = schemas[authType] || { fields: [] };
    return {
      ...rawSchema,
      fields: filterFieldsByUsage(rawSchema.fields || [], 'AUTHENTICATE'),
    };
  }, [toolsetSchema, authType]);

  // ── Validate form ──
  const validateForm = useCallback(() => {
    const errors: Record<string, string> = {};
    (manageAuthSchema.fields || []).forEach((field: any) => {
      const value = formData[field.name];
      if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
        errors[field.name] = `${field.displayName} is required`;
      }
    });
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  }, [manageAuthSchema, formData]);

  // ── Authenticate (non-OAuth) ──
  const handleSaveCredentials = async () => {
    setSaveAttempted(true);
    if (!validateForm()) {
      setError('Please fill in all required fields.');
      return;
    }
    try {
      setSaving(true);
      setError(null);
      if (isAuthenticated) {
        await AgentApiService.updateAgentToolsetCredentials(agentKey, instanceId, formData);
      } else {
        await AgentApiService.authenticateAgentToolset(agentKey, instanceId, formData);
      }
      setIsAuthenticated(true);
      const msg = 'Agent credentials saved successfully!';
      setSuccess(msg);
      showLocalToast(msg, 'success');
      onSuccess();
    } catch (err: any) {
      const msg =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        'Failed to save credentials. Please try again.';
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  // ── OAuth authenticate ──
  const handleOAuthAuthenticate = async () => {
    try {
      setAuthenticating(true);
      setError(null);

      const result = await AgentApiService.getAgentToolsetOAuthUrl(
        agentKey,
        instanceId,
        window.location.origin
      );
      if (!result.success || !result.authorizationUrl) {
        throw new Error('Failed to get authorization URL');
      }

      const width = 600;
      const height = 700;
      const left = window.screen.width / 2 - width / 2;
      const top = window.screen.height / 2 - height / 2;
      const popup = window.open(
        result.authorizationUrl,
        'oauth_agent_popup',
        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`
      );
      if (!popup) {
        throw new Error('Popup blocked. Please allow popups for this site and try again.');
      }
      popup.focus();

      let statusChecked = false;
      let pollCount = 0;
      const maxPolls = 300; // 5 minutes total at 1s intervals

      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);

      pollIntervalRef.current = setInterval(() => {
        pollCount += 1;

        if (pollCount >= maxPolls) {
          clearInterval(pollIntervalRef.current!);
          pollIntervalRef.current = null;
          if (!popup.closed) popup.close();
          setAuthenticating(false);
          setError('Authentication timed out. Please try again.');
          return;
        }

        // Wait until the popup closes, then verify with a retry loop
        if (!popup.closed || statusChecked) return;
        statusChecked = true;
        clearInterval(pollIntervalRef.current!);
        pollIntervalRef.current = null;

        // Retry verification: the OAuth callback may still be in-flight when the
        // popup closes (especially on slow connections). Retry up to 5 times with
        // 1.5s gaps before giving up. This is more robust than a single fixed delay.
        const maxVerifyAttempts = 5;

        const verifyWithRetry = async (attempt: number): Promise<void> => {
          if (attempt >= maxVerifyAttempts) {
            setAuthenticating(false);
            setError('Authentication was not completed. Please try again.');
            showLocalToast('Authentication was not completed.', 'warning');
            return;
          }

          await new Promise((resolve) => setTimeout(resolve, 1500));
          try {
            const agentToolsets = await AgentApiService.getAgentToolsets(agentKey, {
              includeRegistry: false,
            });
            const updated = agentToolsets.toolsets?.find(
              (t: any) => t.instanceId === instanceId
            );
            if (updated?.isAuthenticated) {
              setAuthenticating(false);
              setIsAuthenticated(true);
              const msg = 'Agent OAuth authentication successful!';
              setSuccess(msg);
              showLocalToast(msg, 'success');
              onSuccess();
              return;
            }
          } catch (err) {
            if (attempt + 1 >= maxVerifyAttempts) {
              console.error('Failed to verify agent auth status:', err);
            }
          }
          await verifyWithRetry(attempt + 1);
        };

        verifyWithRetry(0).catch((err) => {
          console.error('OAuth verify retry failed:', err);
          setAuthenticating(false);
        });
      }, 1000);
    } catch (err: any) {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      const msg =
        err.response?.data?.detail || err.message || 'Failed to start OAuth authentication.';
      setError(msg);
      setAuthenticating(false);
    }
  };

  // ── Reauthenticate ──
  const handleReauthenticate = async () => {
    try {
      setReauthenticating(true);
      setError(null);
      await AgentApiService.reauthenticateAgentToolset(agentKey, instanceId);
      setIsAuthenticated(false);
      const msg = 'Agent credentials cleared. Click "Authenticate" to reconnect.';
      setSuccess(msg);
      showLocalToast(msg, 'info');
      onSuccess();
    } catch (err: any) {
      setError(
        err.response?.data?.detail ||
          err.response?.data?.message ||
          'Failed to clear agent credentials.'
      );
    } finally {
      setReauthenticating(false);
    }
  };

  // ── Remove credentials ──
  const handleRemoveCredentials = () => {
    setRemoveConfirmOpen(true);
  };

  const handleRemoveConfirmed = async () => {
    setRemoveConfirmOpen(false);
    try {
      setDeleting(true);
      setError(null);
      await AgentApiService.removeAgentToolsetCredentials(agentKey, instanceId);
      setIsAuthenticated(false);
      const msg = 'Agent credentials removed successfully.';
      setSuccess(msg);
      showLocalToast(msg, 'success');
      onSuccess();
    } catch (err: any) {
      const msg =
        err.response?.data?.detail ||
        err.response?.data?.message ||
        'Failed to remove agent credentials.';
      setError(msg);
      showLocalToast(msg, 'error');
    } finally {
      setDeleting(false);
    }
  };

  const isOAuth = authType === 'OAUTH';
  const isAnyActionInProgress = saving || authenticating || deleting || reauthenticating;

  // ── Loading state ──
  if (schemaLoading) {
    return (
      <Dialog
        open
        onClose={onClose}
        maxWidth="sm"
        fullWidth
        PaperProps={{
          sx: { borderRadius: 2.5 },
        }}
      >
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 3,
            py: 2.5,
            borderBottom: `1px solid ${theme.palette.divider}`,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, width: '100%' }}>
            <Skeleton variant="rectangular" width={48} height={48} sx={{ borderRadius: 1.5 }} />
            <Box sx={{ flex: 1 }}>
              <Skeleton variant="text" width="60%" height={28} />
              <Skeleton variant="text" width="40%" height={20} />
            </Box>
          </Box>
        </DialogTitle>
        <DialogContent sx={{ px: 3, py: 3 }}>
          <Stack spacing={2}>
            <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1.25 }} />
            <Skeleton variant="rectangular" height={60} sx={{ borderRadius: 1.25 }} />
          </Stack>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <>
      <Dialog
        open
        onClose={onClose}
        maxWidth="sm"
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
        {/* ── Title ── */}
        <DialogTitle
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 3,
            py: 2.5,
            borderBottom: isDark
              ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
              : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {/* Toolset icon */}
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
                onError={(e) => {
                  (e.target as HTMLImageElement).src = '/assets/icons/toolsets/default.svg';
                }}
              />
            </Box>
            <Box>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  mb: 0.5,
                  color: theme.palette.text.primary,
                  fontSize: '1.125rem',
                  letterSpacing: '-0.01em',
                }}
              >
                {displayName}
                {toolset.instanceName && toolset.instanceName !== displayName
                  ? ` — ${toolset.instanceName}`
                  : ''}
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.75 }}>
                <Chip
                  label={category}
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 22,
                    fontSize: '0.6875rem',
                    fontWeight: 500,
                    letterSpacing: '0.02em',
                    borderRadius: 1,
                    color: theme.palette.text.secondary,
                    borderColor: alpha(theme.palette.divider, isDark ? 0.65 : 0.95),
                    bgcolor: 'transparent',
                  }}
                />
                <Chip
                  label={authType.split('_').join(' ')}
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 22,
                    fontSize: '0.6875rem',
                    fontWeight: 500,
                    letterSpacing: '0.02em',
                    borderRadius: 1,
                    color: theme.palette.text.secondary,
                    borderColor: alpha(theme.palette.divider, isDark ? 0.65 : 0.95),
                    bgcolor: 'transparent',
                  }}
                />
                <Chip
                  icon={<Iconify icon={robotIcon} width={12} />}
                  label="Agent credentials"
                  size="small"
                  variant="outlined"
                  sx={{
                    height: 22,
                    fontSize: '0.6875rem',
                    fontWeight: 600,
                    letterSpacing: '0.02em',
                    borderRadius: 1,
                    color: theme.palette.text.primary,
                    borderColor: alpha(theme.palette.divider, isDark ? 0.7 : 1),
                    bgcolor: isDark
                      ? alpha(theme.palette.common.white, 0.05)
                      : alpha(theme.palette.common.black, 0.03),
                    '& .MuiChip-icon': { color: theme.palette.text.secondary },
                  }}
                />
              </Box>
            </Box>
          </Box>

          <IconButton
            onClick={onClose}
            size="small"
            sx={{
              color: theme.palette.text.secondary,
              p: 1,
              '&:hover': { backgroundColor: alpha(theme.palette.text.secondary, 0.08) },
            }}
          >
            <Iconify icon={closeIcon} width={20} height={20} />
          </IconButton>
        </DialogTitle>

        {/* ── Content ── */}
        <DialogContent sx={{ px: 3, py: 3 }}>
          <Stack spacing={3}>
            {/* Status / alerts */}
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
            {isAuthenticated && !success && (
              <Alert
                severity="success"
                icon={<Iconify icon={checkCircleIcon} />}
                sx={{ borderRadius: 1.5 }}
              >
                This agent is authenticated and ready to use this toolset.
              </Alert>
            )}

            {/* Description */}
            {toolset.description && (
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem', lineHeight: 1.6 }}>
                {toolset.description}
              </Typography>
            )}

            {/* Info banner explaining service-account scope */}
            <Paper
              variant="outlined"
              sx={{
                p: 1.75,
                borderRadius: 1.5,
                bgcolor: isDark
                  ? alpha(theme.palette.common.white, 0.03)
                  : alpha(theme.palette.common.black, 0.02),
                borderColor: alpha(theme.palette.divider, isDark ? 0.85 : 1),
                display: 'flex',
                alignItems: 'flex-start',
                gap: 1.25,
              }}
            >
              <Iconify
                icon={robotIcon}
                width={18}
                sx={{
                  color: theme.palette.text.secondary,
                  mt: 0.125,
                  flexShrink: 0,
                  opacity: 0.9,
                }}
              />
              <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem', lineHeight: 1.55 }}>
                Credentials configured here are stored for the <strong>agent</strong>, not for any individual user.
                All users of this agent will share these credentials when the toolset is invoked.
              </Typography>
            </Paper>

            {/* OAuth section */}
            {isOAuth && (
              <Paper
                variant="outlined"
                sx={{
                  p: 2.5,
                  borderRadius: 1.5,
                  bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper,
                }}
              >
                <Stack spacing={2}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 0.5 }}>
                    <Box
                      sx={{
                        p: 0.625,
                        borderRadius: 1,
                        bgcolor: alpha(theme.palette.primary.main, 0.1),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Iconify icon={lockIcon} width={16} color={theme.palette.primary.main} />
                    </Box>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                      OAuth Authentication
                    </Typography>
                  </Box>
                  {isAuthenticated ? (
                    <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                      Agent is connected via OAuth. Use <strong>Re-authenticate</strong> to start a new
                      OAuth flow, or <strong>Remove Credentials</strong> to disconnect.
                    </Alert>
                  ) : (
                    <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                      Click <strong>Authenticate</strong> to connect the agent via OAuth. A popup will
                      guide you through the provider&apos;s authorization flow.
                    </Alert>
                  )}
                </Stack>
              </Paper>
            )}

            {/* Non-OAuth credential fields */}
            {!isOAuth && authType !== 'NONE' && (
              <Paper
                variant="outlined"
                sx={{
                  p: 2.5,
                  borderRadius: 1.5,
                  bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper,
                }}
              >
                <Stack spacing={2}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 0.5 }}>
                    <Box
                      sx={{
                        p: 0.625,
                        borderRadius: 1,
                        bgcolor: alpha(theme.palette.text.primary, 0.05),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Iconify icon={keyIcon} width={16} sx={{ color: theme.palette.text.primary }} />
                    </Box>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.9375rem' }}>
                      Agent Credentials
                    </Typography>
                  </Box>

                  {manageAuthSchema.fields && manageAuthSchema.fields.length > 0 ? (
                    <Grid container spacing={2}>
                      {manageAuthSchema.fields.map((field: any) => (
                        <Grid item xs={12} key={field.name}>
                          <FieldRenderer
                            field={field}
                            value={formData[field.name] ?? ''}
                            onChange={(value) => {
                              setFormData((prev) => ({ ...prev, [field.name]: value }));
                              setFormErrors((prev) => {
                                const next = { ...prev };
                                delete next[field.name];
                                return next;
                              });
                            }}
                            error={saveAttempted ? formErrors[field.name] : undefined}
                          />
                        </Grid>
                      ))}
                    </Grid>
                  ) : (
                    <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                      No credentials required for this authentication type.
                    </Alert>
                  )}

                  {isAuthenticated && (
                    <Alert severity="success" sx={{ borderRadius: 1.25 }}>
                      Agent is authenticated. Enter new credentials and click <strong>Save Credentials</strong> to update.
                    </Alert>
                  )}
                </Stack>
              </Paper>
            )}

            {authType === 'NONE' && (
              <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                This toolset does not require authentication.
              </Alert>
            )}

            {/* Tool preview */}
            {tools.length > 0 && (
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                    Available Tools ({tools.length})
                  </Typography>
                </Box>
                <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.75 }}>
                  {tools.slice(0, 8).map((t: any) => (
                    <Chip
                      key={t.fullName || t.name}
                      label={t.name}
                      size="small"
                      variant="outlined"
                      sx={{
                        height: 24,
                        fontSize: '0.75rem',
                        fontWeight: 500,
                        letterSpacing: '0.01em',
                        borderRadius: 1.25,
                        color: theme.palette.text.secondary,
                        borderColor: alpha(theme.palette.divider, isDark ? 0.65 : 0.95),
                        bgcolor: 'transparent',
                        transition: 'border-color 0.15s ease, background-color 0.15s ease',
                        '&:hover': {
                          borderColor: alpha(theme.palette.text.primary, 0.18),
                          bgcolor: alpha(theme.palette.text.primary, isDark ? 0.05 : 0.03),
                        },
                      }}
                    />
                  ))}
                  {tools.length > 8 && (
                    <Chip
                      label={`+${tools.length - 8} more`}
                      size="small"
                      variant="outlined"
                      sx={{
                        height: 24,
                        fontSize: '0.75rem',
                        fontWeight: 500,
                        letterSpacing: '0.02em',
                        borderRadius: 1.25,
                        color: theme.palette.text.secondary,
                        borderColor: alpha(theme.palette.divider, isDark ? 0.65 : 0.95),
                        bgcolor: isDark
                          ? alpha(theme.palette.common.white, 0.04)
                          : alpha(theme.palette.common.black, 0.03),
                      }}
                    />
                  )}
                </Stack>
              </Box>
            )}
          </Stack>
        </DialogContent>

        {/* ── Actions ── */}
        <DialogActions
          sx={{
            px: 3,
            py: 2.5,
            borderTop: isDark
              ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
              : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
            flexDirection: 'row',
            justifyContent: 'space-between',
          }}
        >
          {/* Left: destructive */}
          <Box>
            {isAuthenticated && (
              <Button
                onClick={handleRemoveCredentials}
                disabled={isAnyActionInProgress}
                variant="text"
                color="error"
                startIcon={
                  deleting ? (
                    <CircularProgress size={14} color="error" />
                  ) : (
                    <Iconify icon={deleteIcon} width={16} />
                  )
                }
                sx={{
                  textTransform: 'none',
                  borderRadius: 1,
                  px: 2,
                  fontSize: '0.875rem',
                  '&:hover': { backgroundColor: alpha(theme.palette.error.main, 0.08) },
                }}
              >
                {deleting ? 'Removing...' : 'Remove Credentials'}
              </Button>
            )}
          </Box>

          {/* Right: primary actions */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Button
              onClick={onClose}
              disabled={isAnyActionInProgress}
              variant="text"
              sx={{
                textTransform: 'none',
                borderRadius: 1,
                px: 2,
                fontSize: '0.875rem',
                color: theme.palette.text.secondary,
              }}
            >
              {isAuthenticated ? 'Close' : 'Cancel'}
            </Button>

            <Box sx={{ width: '1px', height: 20, bgcolor: alpha(theme.palette.divider, 0.4), mx: 0.5 }} />

            {/* OAuth actions */}
            {isOAuth && (
              <>
                {isAuthenticated && (
                  <Button
                    onClick={handleReauthenticate}
                    disabled={isAnyActionInProgress}
                    variant="outlined"
                    startIcon={
                      reauthenticating ? (
                        <CircularProgress size={14} color="inherit" />
                      ) : (
                        <Iconify icon={refreshIcon} width={15} />
                      )
                    }
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      px: 2,
                      fontSize: '0.8125rem',
                      borderColor: alpha(theme.palette.warning.main, 0.5),
                      color: theme.palette.warning.main,
                      '&:hover': {
                        borderColor: theme.palette.warning.main,
                        backgroundColor: alpha(theme.palette.warning.main, 0.07),
                      },
                    }}
                  >
                    {reauthenticating ? 'Clearing...' : 'Re-authenticate'}
                  </Button>
                )}
                <Button
                  onClick={handleOAuthAuthenticate}
                  disabled={isAnyActionInProgress}
                  variant="contained"
                  startIcon={
                    authenticating ? (
                      <CircularProgress size={14} sx={{ color: 'inherit' }} />
                    ) : (
                      <Iconify icon={lockIcon} width={16} />
                    )
                  }
                  sx={{ textTransform: 'none', borderRadius: 1, px: 2.5, boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
                >
                  {authenticating ? 'Authenticating...' : isAuthenticated ? 'Reconnect' : 'Authenticate'}
                </Button>
              </>
            )}

            {/* Non-OAuth: save credentials */}
            {!isOAuth && authType !== 'NONE' && (
              <>
                {isAuthenticated && (
                  <Button
                    onClick={handleReauthenticate}
                    disabled={isAnyActionInProgress}
                    variant="outlined"
                    startIcon={
                      reauthenticating ? (
                        <CircularProgress size={14} color="inherit" />
                      ) : (
                        <Iconify icon={refreshIcon} width={15} />
                      )
                    }
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      px: 2,
                      fontSize: '0.8125rem',
                      borderColor: alpha(theme.palette.warning.main, 0.5),
                      color: theme.palette.warning.main,
                      '&:hover': {
                        borderColor: theme.palette.warning.main,
                        backgroundColor: alpha(theme.palette.warning.main, 0.07),
                      },
                    }}
                  >
                    {reauthenticating ? 'Clearing...' : 'Re-authenticate'}
                  </Button>
                )}
                <Button
                  onClick={handleSaveCredentials}
                  disabled={isAnyActionInProgress}
                  variant="contained"
                  startIcon={
                    saving ? (
                      <CircularProgress size={14} sx={{ color: 'inherit' }} />
                    ) : (
                      <Iconify icon={saveIcon} width={16} />
                    )
                  }
                  sx={{ textTransform: 'none', borderRadius: 1, px: 2.5, boxShadow: 'none', '&:hover': { boxShadow: 'none' } }}
                >
                  {saving ? 'Saving...' : isAuthenticated ? 'Update Credentials' : 'Save Credentials'}
                </Button>
              </>
            )}
          </Box>
        </DialogActions>

        {/* Local toast */}
        <Snackbar
          open={localToast.open}
          autoHideDuration={4000}
          onClose={() => setLocalToast((prev) => ({ ...prev, open: false }))}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          sx={{ zIndex: (t) => t.zIndex.snackbar }}
        >
          <Alert
            onClose={() => setLocalToast((prev) => ({ ...prev, open: false }))}
            severity={localToast.severity}
            variant="filled"
            sx={{ borderRadius: 1.5, fontWeight: 600, minWidth: 320 }}
          >
            {localToast.message}
          </Alert>
        </Snackbar>
      </Dialog>

      {/* ── Remove credentials confirmation dialog ── */}
      <Dialog
        open={removeConfirmOpen}
        onClose={() => setRemoveConfirmOpen(false)}
        maxWidth="xs"
        fullWidth
        PaperProps={{ sx: { borderRadius: 2 } }}
      >
        <DialogTitle sx={{ pb: 1 }}>Remove Agent Credentials?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will permanently remove the agent&apos;s credentials for{' '}
            <strong>{displayName}</strong>. The agent will no longer be able to use this
            toolset until credentials are configured again.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setRemoveConfirmOpen(false)}
            variant="text"
            sx={{ textTransform: 'none' }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleRemoveConfirmed}
            variant="contained"
            color="error"
            sx={{ textTransform: 'none', boxShadow: 'none' }}
          >
            Remove Credentials
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default AgentToolsetConfigDialog;
