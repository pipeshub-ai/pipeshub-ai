/**
 * OAuth App Dialog Component
 * 
 * Reusable dialog for creating, viewing, editing, and deleting OAuth app configurations
 * Matches the styling and functionality of the connector config dialog
 */

import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  CircularProgress,
  Alert,
  alpha,
  useTheme,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  IconButton,
  Chip,
  Paper,
  Collapse,
  Link,
  Tooltip,
  Fade,
  Slide,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import closeIcon from '@iconify-icons/mdi/close';
import infoIcon from '@iconify-icons/eva/info-outline';
import bookIcon from '@iconify-icons/mdi/book-outline';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import copyIcon from '@iconify-icons/mdi/content-copy';
import checkIcon from '@iconify-icons/mdi/check';
import openInNewIcon from '@iconify-icons/mdi/open-in-new';
import shieldIcon from '@iconify-icons/mdi/shield-outline';
import { ConnectorApiService } from '../../services/api';
import { FieldRenderer } from '../field-renderers';

export type OAuthAppDialogMode = 'create' | 'edit' | 'view';

interface OAuthAppDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  mode: OAuthAppDialogMode;
  connectorType?: string;
  oauthConfigId?: string;
  onModeChange?: (mode: OAuthAppDialogMode) => void;
}

const OAuthAppDialog: React.FC<OAuthAppDialogProps> = ({
  open,
  onClose,
  onSuccess,
  mode: initialMode,
  connectorType: initialConnectorType,
  oauthConfigId,
  onModeChange,
}) => {
  // Internal mode state - can be changed by Edit button
  const [mode, setMode] = useState<OAuthAppDialogMode>(initialMode);
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [showTopFade, setShowTopFade] = useState(false);
  const [showBottomFade, setShowBottomFade] = useState(false);

  // Core state
  const [connectorType, setConnectorType] = useState<string>(initialConnectorType || '');
  const [oauthInstanceName, setOAuthInstanceName] = useState('');
  const [config, setConfig] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Connector and field data
  const [availableConnectors, setAvailableConnectors] = useState<any[]>([]);
  const [selectedConnector, setSelectedConnector] = useState<any>(null);
  const [loadingConnectors, setLoadingConnectors] = useState(false);

  // UI state
  const [showDocs, setShowDocs] = useState(false);
  const [showRedirectUri, setShowRedirectUri] = useState(true);
  const [copied, setCopied] = useState(false);

  // Memoized values
  const redirectUri = useMemo(() => {
    if (!selectedConnector?.redirectUri) return '';
    return `${window.location.origin}/${selectedConnector.redirectUri}`;
  }, [selectedConnector]);

  const authFields = useMemo(
    () => selectedConnector?.authFields || [],
    [selectedConnector]
  );

  const documentationLinks = useMemo(
    () => selectedConnector?.documentationLinks || [],
    [selectedConnector]
  );

  const pipeshubDocumentationUrl = useMemo(
    () =>
      documentationLinks?.find((link: any) => link.type === 'pipeshub')?.url ||
      'https://docs.pipeshub.com/connectors/overview',
    [documentationLinks]
  );

  const otherDocumentationLinks = useMemo(
    () => documentationLinks?.filter((link: any) => link.type !== 'pipeshub') || [],
    [documentationLinks]
  );

  // Scroll fade indicators
  const topFadeGradient = useMemo(
    () =>
      isDark
        ? 'linear-gradient(to bottom, rgba(18, 18, 23, 0.98), transparent)'
        : `linear-gradient(to bottom, ${theme.palette.background.paper}, transparent)`,
    [isDark, theme.palette.background.paper]
  );

  const bottomFadeGradient = useMemo(
    () =>
      isDark
        ? 'linear-gradient(to top, rgba(18, 18, 23, 0.98), transparent)'
        : `linear-gradient(to top, ${theme.palette.background.paper}, transparent)`,
    [isDark, theme.palette.background.paper]
  );

  const checkScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    setShowTopFade(scrollTop > 10);
    setShowBottomFade(scrollTop < scrollHeight - clientHeight - 10);
  }, []);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    checkScroll();
    let ticking = false;
    const handleScroll = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          checkScroll();
          ticking = false;
        });
        ticking = true;
      }
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    const resizeObserver = new ResizeObserver(() => {
      setTimeout(checkScroll, 150);
    });
    resizeObserver.observe(container);

    // eslint-disable-next-line consistent-return
    return () => {
      container.removeEventListener('scroll', handleScroll);
      resizeObserver.disconnect();
    };
  }, [checkScroll]);

  // Load connectors and initialize
  useEffect(() => {
    if (!open) return;

    const targetType = initialConnectorType || connectorType;
    
    // If we have a specific connector type, fetch only that connector from registry
    // Otherwise, fetch all connectors (for create mode without pre-selected type)
    if (targetType) {
      setLoadingConnectors(true);
      // Fetch only the specific connector's registry info (more efficient)
      ConnectorApiService.getOAuthConfigRegistryByType(targetType)
        .then((connector) => {
          if (connector) {
            const normalizedConnector = {
              ...connector,
              connectorType: connector.type || connector.connectorType || targetType,
            };
            setSelectedConnector(normalizedConnector);
            setAvailableConnectors([normalizedConnector]);
            if (!connectorType) {
              setConnectorType(targetType);
            }
          }
        })
        .catch((error) => {
          console.error('Error fetching connector registry:', error);
          setErrors({ fetch: 'Failed to load connector registry' });
        })
        .finally(() => {
          setLoadingConnectors(false);
        });
    } else {
      // Create mode without pre-selected type - fetch all connectors
      setLoadingConnectors(true);
      ConnectorApiService.getOAuthConfigRegistry(1, 100)
        .then((result) => {
          const connectors = (result.connectors || []).map((c: any) => ({
            ...c,
            connectorType: c.type || c.connectorType,
          }));
          setAvailableConnectors(connectors);
        })
        .catch((error) => {
          console.error('Error fetching connectors:', error);
          setErrors({ fetch: 'Failed to load connectors' });
        })
        .finally(() => {
          setLoadingConnectors(false);
        });
    }
  }, [open, initialConnectorType, connectorType]);

  // Track if we've already loaded the data to prevent re-fetching when switching modes
  const dataLoadedRef = useRef(false);
  const loadedOAuthConfigIdRef = useRef<string | undefined>(undefined);

  // Load OAuth app data for edit/view modes
  useEffect(() => {
    // Only fetch if we haven't loaded this specific app yet, or if dialog just opened
    const shouldFetch = 
      (mode === 'edit' || mode === 'view') && 
      connectorType && 
      oauthConfigId && 
      open &&
      (!dataLoadedRef.current || loadedOAuthConfigIdRef.current !== oauthConfigId);
    
    if (shouldFetch) {
      setLoading(true);
      dataLoadedRef.current = false;
      
      // Fetch both the OAuth config (with stored values) and registry (for schema)
      // Use the optimized endpoint to fetch only the specific connector's registry info
      Promise.all([
        ConnectorApiService.getOAuthConfig(connectorType, oauthConfigId),
        ConnectorApiService.getOAuthConfigRegistryByType(connectorType)
      ])
        .then(([oauthConfig, connector]) => {
          // Set the stored config values
          setOAuthInstanceName(oauthConfig.oauthInstanceName || '');
          setConfig(oauthConfig.config || {});
          
          // Update selected connector from registry for schema/documentation
          if (connector) {
            const normalizedConnector = {
              ...connector,
              connectorType: connector.type || connector.connectorType || connectorType,
            };
            setSelectedConnector(normalizedConnector);
            setAvailableConnectors([normalizedConnector]);
          }
          
          dataLoadedRef.current = true;
          loadedOAuthConfigIdRef.current = oauthConfigId;
        })
        .catch((error) => {
          console.error('Error fetching OAuth config:', error);
          setErrors({ fetch: 'Failed to load OAuth app configuration' });
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [mode, connectorType, oauthConfigId, open]);

  // Update selected connector when connectorType changes (for create mode)
  useEffect(() => {
    // Only initialize defaults in create mode (not edit/view where we load from API)
    if (connectorType && availableConnectors.length > 0 && mode === 'create') {
      const connector = availableConnectors.find(
        (c) => (c.type || c.connectorType) === connectorType
      );
      if (connector) {
        setSelectedConnector(connector);
        // Initialize config with default values if not already set
        setConfig((prev) => {
          const newConfig = { ...prev };
          let hasChanges = false;
          (connector.authFields || []).forEach((field: any) => {
            if (field.defaultValue !== undefined && newConfig[field.name] === undefined) {
              newConfig[field.name] = field.defaultValue;
              hasChanges = true;
            }
          });
          return hasChanges ? newConfig : prev;
        });
      }
    }
  }, [connectorType, availableConnectors, mode]);

  // Reset form when dialog closes or mode changes
  useEffect(() => {
    if (!open) {
      setConnectorType(initialConnectorType || '');
      setOAuthInstanceName('');
      setConfig({});
      setErrors({});
      setSelectedConnector(null);
      setShowDocs(false);
      setShowRedirectUri(true);
      setMode(initialMode);
      // Reset data loaded flags when dialog closes
      dataLoadedRef.current = false;
      loadedOAuthConfigIdRef.current = undefined;
    } else {
    // When dialog opens, sync mode with prop
      setMode(initialMode);
    }
  }, [open, initialConnectorType, initialMode]);

  // Validation
  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (mode === 'create' && !connectorType) {
      newErrors.connectorType = 'Connector type is required';
    }

    if (!oauthInstanceName.trim()) {
      newErrors.oauthInstanceName = 'OAuth app name is required';
    } else if (oauthInstanceName.trim().length < 3) {
      newErrors.oauthInstanceName = 'OAuth app name must be at least 3 characters';
    }

    authFields.forEach((field: any) => {
      if (field.required && !config[field.name]) {
        newErrors[`config.${field.name}`] = `${field.displayName || field.name} is required`;
      }
    });

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handlers
  const handleSave = async () => {
    if (!validate()) return;

    setSaving(true);
    try {
      if (mode === 'create') {
        await ConnectorApiService.createOAuthConfig(connectorType, oauthInstanceName.trim(), config);
      } else if (mode === 'edit' && oauthConfigId) {
        await ConnectorApiService.updateOAuthConfig(connectorType, oauthConfigId, oauthInstanceName.trim(), config);
        // After successful edit, switch back to view mode
        setMode('view');
        if (onModeChange) {
          onModeChange('view');
        }
      }
      onSuccess();
    } catch (error: any) {
      const errorMessage = error?.response?.data?.detail || error?.message || `Failed to ${mode} OAuth app`;
      setErrors({ submit: errorMessage });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!oauthConfigId || !connectorType) return;
    if (!window.confirm(`Are you sure you want to delete "${oauthInstanceName}"? This action cannot be undone.`)) {
      return;
    }

    setDeleting(true);
    try {
      await ConnectorApiService.deleteOAuthConfig(connectorType, oauthConfigId);
      onSuccess();
    } catch (error: any) {
      const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to delete OAuth app';
      setErrors({ submit: errorMessage });
    } finally {
      setDeleting(false);
    }
  };

  const handleFieldChange = (fieldName: string, value: any) => {
    setConfig((prev) => ({ ...prev, [fieldName]: value }));
    if (errors[`config.${fieldName}`]) {
      setErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors[`config.${fieldName}`];
        return newErrors;
      });
    }
  };

  const handleCopyRedirectUri = () => {
    navigator.clipboard.writeText(redirectUri);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isViewMode = mode === 'view';
  const isEditMode = mode === 'edit';
  const isCreateMode = mode === 'create';

  // Create OAuth app name field definition (memoized to prevent recreation on every render)
  const oauthAppNameField = useMemo(
    () => ({
      name: 'oauthInstanceName',
      displayName: 'OAuth App Name',
      placeholder: 'e.g., Production OAuth App',
      description: 'Give this OAuth app a unique name',
      fieldType: 'TEXT' as const,
      required: true,
      defaultValue: '',
      validation: { minLength: 3, maxLength: 100 },
      isSecret: false,
    }),
    []
  );

  // Combine OAuth app name with auth fields
  const allFields = useMemo(
    () => [oauthAppNameField, ...authFields],
    [oauthAppNameField, authFields]
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2.5,
          boxShadow: isDark
            ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
            : '0 20px 60px rgba(0, 0, 0, 0.12)',
          overflow: 'hidden',
          height: '85vh',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          border: isDark ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
        },
      }}
      slotProps={{
        backdrop: {
          sx: {
            backgroundColor: isDark ? 'rgba(0, 0, 0, 0.35)' : 'rgba(0, 0, 0, 0.5)',
          },
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 2.5,
          backgroundColor: 'transparent',
          flexShrink: 0,
          borderBottom: isDark
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          {selectedConnector?.iconPath && (
            <Box
              sx={{
                p: 1.25,
                borderRadius: 1.5,
                bgcolor: isDark ? alpha(theme.palette.common.white, 0.08) : alpha(theme.palette.grey[100], 0.8),
                backgroundColor: isDark ? alpha(theme.palette.common.white, 0.9) : alpha(theme.palette.grey[100], 0.8),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: isDark ? `1px solid ${alpha(theme.palette.common.white, 0.1)}` : 'none',
              }}
            >
              <img
                src={selectedConnector.iconPath}
                alt={selectedConnector.name}
                width={32}
                height={32}
                style={{ objectFit: 'contain' }}
                onError={(e: React.SyntheticEvent<HTMLImageElement>) => {
                  const target = e.target as HTMLImageElement;
                  target.src = '/assets/icons/connectors/default.svg';
                }}
              />
            </Box>
          )}
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
              {isCreateMode && 'Create OAuth App'}
              {isEditMode && 'Edit OAuth App'}
              {isViewMode && 'OAuth App Details'}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              {selectedConnector?.name && (
                <Chip
                  label={selectedConnector.name}
                  size="small"
                  variant="outlined"
                  sx={{
                    fontSize: '0.6875rem',
                    height: 20,
                    fontWeight: 500,
                    borderColor: isDark ? alpha(theme.palette.divider, 0.3) : alpha(theme.palette.divider, 0.2),
                    bgcolor: isDark ? alpha(theme.palette.common.white, 0.05) : 'transparent',
                    color: isDark ? alpha(theme.palette.text.primary, 0.9) : theme.palette.text.secondary,
                    '& .MuiChip-label': { px: 1.25, py: 0 },
                  }}
                />
              )}
              {selectedConnector?.appGroup && (
                <Chip
                  label={selectedConnector.appGroup}
                  size="small"
                  variant="outlined"
                  sx={{
                    fontSize: '0.6875rem',
                    height: 20,
                    fontWeight: 500,
                    borderColor: isDark ? alpha(theme.palette.divider, 0.3) : alpha(theme.palette.divider, 0.2),
                    bgcolor: isDark ? alpha(theme.palette.common.white, 0.05) : 'transparent',
                    color: isDark ? alpha(theme.palette.text.primary, 0.9) : theme.palette.text.secondary,
                    '& .MuiChip-label': { px: 1.25, py: 0 },
                  }}
                />
              )}
            </Box>
          </Box>
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          sx={{
            color: isDark ? alpha(theme.palette.text.secondary, 0.8) : theme.palette.text.secondary,
            p: 1,
            '&:hover': {
              backgroundColor: isDark ? alpha(theme.palette.common.white, 0.1) : alpha(theme.palette.text.secondary, 0.08),
              color: theme.palette.text.primary,
            },
            transition: 'all 0.2s ease',
          }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent
        sx={{
          p: 0,
          overflow: 'hidden',
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          position: 'relative',
        }}
      >
        {errors.submit && (
          <Alert
            severity="error"
            sx={{
              mx: 2.5,
              mt: 2,
              mb: 0,
              borderRadius: 1.5,
              flexShrink: 0,
              bgcolor: isDark ? alpha(theme.palette.error.main, 0.15) : undefined,
              border: isDark ? `1px solid ${alpha(theme.palette.error.main, 0.3)}` : 'none',
            }}
            onClose={() => setErrors((prev) => ({ ...prev, submit: '' }))}
          >
            {errors.submit}
          </Alert>
        )}

        {showTopFade && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 24,
              background: topFadeGradient,
              pointerEvents: 'none',
              zIndex: 1,
            }}
          />
        )}

        {showBottomFade && (
          <Box
            sx={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: 24,
              background: bottomFadeGradient,
              pointerEvents: 'none',
              zIndex: 1,
            }}
          />
        )}

        <Box
          ref={scrollContainerRef}
          sx={{
            px: 1.5,
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            overflow: 'auto',
            '&::-webkit-scrollbar': { width: '6px' },
            '&::-webkit-scrollbar-track': { backgroundColor: 'transparent' },
            '&::-webkit-scrollbar-thumb': {
              backgroundColor: isDark ? alpha(theme.palette.text.secondary, 0.25) : alpha(theme.palette.text.secondary, 0.16),
              borderRadius: '3px',
              '&:hover': {
                backgroundColor: isDark ? alpha(theme.palette.text.secondary, 0.4) : alpha(theme.palette.text.secondary, 0.24),
              },
            },
          }}
        >
          <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
            {/* Documentation Alert */}
            <Alert
              variant="outlined"
              severity="info"
              sx={{
                borderRadius: 1.25,
                py: 1,
                px: 1.75,
                fontSize: '0.875rem',
                '& .MuiAlert-icon': { fontSize: '1.25rem', py: 0.5 },
                '& .MuiAlert-message': { py: 0.25 },
                alignItems: 'center',
              }}
            >
              Refer to{' '}
              <Link
                href={pipeshubDocumentationUrl}
                target="_blank"
                rel="noopener"
                sx={{
                  fontWeight: 600,
                  textDecoration: 'none',
                  '&:hover': { textDecoration: 'underline' },
                }}
              >
                our documentation
              </Link>{' '}
              for more information.
            </Alert>

            {/* Redirect URI */}
            {redirectUri && (
              <Paper
                variant="outlined"
                sx={{
                  borderRadius: 1.25,
                  overflow: 'hidden',
                  bgcolor: isDark ? alpha(theme.palette.primary.main, 0.08) : alpha(theme.palette.primary.main, 0.03),
                  borderColor: isDark ? alpha(theme.palette.primary.main, 0.25) : alpha(theme.palette.primary.main, 0.15),
                }}
              >
                <Box
                  onClick={() => setShowRedirectUri(!showRedirectUri)}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    p: 1.5,
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                    '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.05) },
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                    <Box
                      sx={{
                        p: 0.625,
                        borderRadius: 1,
                        bgcolor: alpha(theme.palette.primary.main, 0.12),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Iconify icon={infoIcon} width={16} color={theme.palette.primary.main} />
                    </Box>
                    <Typography variant="subtitle2" sx={{ fontSize: '0.875rem', fontWeight: 600, color: theme.palette.primary.main }}>
                      Redirect URI
                    </Typography>
                  </Box>
                  <Iconify
                    icon={chevronDownIcon}
                    width={20}
                    color={theme.palette.text.secondary}
                    sx={{
                      transform: showRedirectUri ? 'rotate(180deg)' : 'rotate(0deg)',
                      transition: 'transform 0.2s',
                    }}
                  />
                </Box>

                <Collapse in={showRedirectUri}>
                  <Box sx={{ px: 1.5, pb: 1.5 }}>
                    <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem', mb: 1.25, lineHeight: 1.5 }}>
                      {selectedConnector?.name
                        ? `Use this URL when configuring your ${selectedConnector.name} OAuth2 App.`
                        : 'Use this URL when configuring your OAuth2 App.'}
                    </Typography>
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        p: 1.25,
                        borderRadius: 1,
                        bgcolor: isDark ? alpha(theme.palette.grey[900], 0.4) : alpha(theme.palette.grey[100], 0.8),
                        border: `1.5px solid ${alpha(theme.palette.primary.main, isDark ? 0.25 : 0.15)}`,
                        transition: 'all 0.2s',
                        '&:hover': {
                          borderColor: alpha(theme.palette.primary.main, isDark ? 0.4 : 0.3),
                          bgcolor: isDark ? alpha(theme.palette.grey[900], 0.6) : alpha(theme.palette.grey[100], 1),
                        },
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          flex: 1,
                          fontFamily: '"SF Mono", "Roboto Mono", Monaco, Consolas, monospace',
                          fontSize: '0.8125rem',
                          wordBreak: 'break-all',
                          color: theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.dark,
                          fontWeight: 500,
                          userSelect: 'all',
                          lineHeight: 1.6,
                        }}
                      >
                        {redirectUri}
                      </Typography>
                      <Tooltip title={copied ? 'Copied!' : 'Copy to clipboard'} arrow>
                        <IconButton
                          size="small"
                          onClick={handleCopyRedirectUri}
                          sx={{
                            p: 0.75,
                            bgcolor: alpha(theme.palette.primary.main, 0.1),
                            transition: 'all 0.2s',
                            '&:hover': {
                              bgcolor: alpha(theme.palette.primary.main, 0.2),
                              transform: 'scale(1.05)',
                            },
                          }}
                        >
                          <Iconify icon={copied ? checkIcon : copyIcon} width={16} color={theme.palette.primary.main} />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  </Box>
                </Collapse>
              </Paper>
            )}

            {/* Documentation Links */}
            {otherDocumentationLinks.length > 0 && (
              <Paper
                variant="outlined"
                sx={{
                  borderRadius: 1.25,
                  overflow: 'hidden',
                  bgcolor: isDark ? alpha(theme.palette.info.main, 0.08) : alpha(theme.palette.info.main, 0.025),
                  borderColor: isDark ? alpha(theme.palette.info.main, 0.25) : alpha(theme.palette.info.main, 0.12),
                }}
              >
                <Box
                  onClick={() => setShowDocs(!showDocs)}
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    p: 1.5,
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                    '&:hover': { bgcolor: alpha(theme.palette.info.main, 0.05) },
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                    <Box
                      sx={{
                        p: 0.625,
                        borderRadius: 1,
                        bgcolor: alpha(theme.palette.info.main, 0.12),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Iconify icon={bookIcon} width={16} color={theme.palette.info.main} />
                    </Box>
                    <Typography variant="subtitle2" sx={{ fontSize: '0.875rem', fontWeight: 600, color: theme.palette.info.main }}>
                      Setup Documentation
                    </Typography>
                    <Chip
                      label={otherDocumentationLinks.length}
                      size="small"
                      sx={{
                        height: 18,
                        fontSize: '0.6875rem',
                        fontWeight: 600,
                        bgcolor: alpha(theme.palette.info.main, 0.2),
                        color: theme.palette.info.main,
                      }}
                    />
                  </Box>
                  <Iconify
                    icon={chevronDownIcon}
                    width={20}
                    color={theme.palette.text.secondary}
                    sx={{
                      transform: showDocs ? 'rotate(180deg)' : 'rotate(0deg)',
                      transition: 'transform 0.2s',
                    }}
                  />
                </Box>

                <Collapse in={showDocs}>
                  <Box sx={{ px: 1.5, pb: 1.5, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                    {otherDocumentationLinks.map((link: any, index: number) => (
                      <Box
                        key={index}
                        onClick={(e) => {
                          e.stopPropagation();
                          window.open(link.url, '_blank');
                        }}
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          p: 1,
                          borderRadius: 1,
                          border: `1px solid ${alpha(theme.palette.divider, isDark ? 0.12 : 0.1)}`,
                          bgcolor: isDark ? alpha(theme.palette.background.paper, 0.5) : theme.palette.background.paper,
                          cursor: 'pointer',
                          transition: 'all 0.2s',
                          '&:hover': {
                            borderColor: alpha(theme.palette.info.main, isDark ? 0.4 : 0.25),
                            bgcolor: isDark ? alpha(theme.palette.info.main, 0.12) : alpha(theme.palette.info.main, 0.03),
                            transform: 'translateX(4px)',
                            boxShadow: `0 2px 8px ${alpha(theme.palette.info.main, isDark ? 0.2 : 0.08)}`,
                          },
                        }}
                      >
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Box
                            sx={{
                              p: 0.5,
                              borderRadius: 0.75,
                              bgcolor: alpha(theme.palette.info.main, 0.1),
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                            }}
                          >
                            <Iconify icon={openInNewIcon} width={14} color={theme.palette.info.main} />
                          </Box>
                          <Typography variant="body2" sx={{ fontSize: '0.8125rem', fontWeight: 500, color: theme.palette.text.primary, flex: 1 }}>
                            {link.title}
                          </Typography>
                        </Box>
                      </Box>
                    ))}
                  </Box>
                </Collapse>
              </Paper>
            )}

            {/* Connector Type Selector (create mode only) */}
            {isCreateMode && !initialConnectorType && (
              <FormControl fullWidth error={!!errors.connectorType}>
                <InputLabel>Connector Type</InputLabel>
                <Select
                  value={connectorType}
                  onChange={(e) => {
                    setConnectorType(e.target.value);
                  }}
                  label="Connector Type"
                  disabled={loadingConnectors}
                >
                  {availableConnectors.map((connector) => (
                    <MenuItem key={connector.connectorType} value={connector.connectorType}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                        {connector.iconPath && (
                          <Box
                            component="img"
                            src={connector.iconPath}
                            alt={connector.name}
                            sx={{ width: 24, height: 24, objectFit: 'contain' }}
                            onError={(e) => {
                              const target = e.target as HTMLImageElement;
                              target.src = '/assets/icons/connectors/default.svg';
                            }}
                          />
                        )}
                        <Box>
                          <Typography variant="body2">{connector.name}</Typography>
                          {connector.appGroup && (
                            <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                              {connector.appGroup}
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    </MenuItem>
                  ))}
                </Select>
                {errors.connectorType && (
                  <Typography variant="caption" color="error" sx={{ mt: 0.5 }}>
                    {errors.connectorType}
                  </Typography>
                )}
              </FormControl>
            )}



            {/* Form Fields */}
            <Fade in={!loading && !loadingConnectors && !!selectedConnector} timeout={300}>
              <Box sx={{ opacity: loading || loadingConnectors ? 0 : 1, transition: 'opacity 0.3s ease' }}>
                {!loading && !loadingConnectors && selectedConnector && allFields.length > 0 && (
              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  borderRadius: 1.25,
                  bgcolor: isDark ? alpha(theme.palette.background.paper, 0.4) : theme.palette.background.paper,
                  borderColor: isDark ? alpha(theme.palette.divider, 0.12) : alpha(theme.palette.divider, 0.1),
                  boxShadow: isDark ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}` : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
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
                    <Iconify icon={shieldIcon} width={16} color={theme.palette.text.secondary} />
                  </Box>
                  <Box sx={{ flex: 1 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: '0.875rem', color: theme.palette.text.primary, lineHeight: 1.4 }}>
                      OAuth2 Configuration
                    </Typography>
                    <Typography variant="caption" sx={{ fontSize: '0.75rem', color: theme.palette.text.secondary, lineHeight: 1.3 }}>
                      Enter your {selectedConnector?.name || 'connector'} authentication details
                    </Typography>
                  </Box>
                </Box>
                <Grid container spacing={2}>
                  {allFields.map((field: any) => {
                    // Handle OAuth app name field specially
                    if (field.name === 'oauthInstanceName') {
                      return (
                        <Grid item xs={12} key={field.name}>
                          <FieldRenderer
                            field={field}
                            value={oauthInstanceName}
                            onChange={(value) => {
                              setOAuthInstanceName(value);
                              if (errors.oauthInstanceName) {
                                setErrors((prev) => {
                                  const newErrors = { ...prev };
                                  delete newErrors.oauthInstanceName;
                                  return newErrors;
                                });
                              }
                            }}
                            error={errors.oauthInstanceName}
                            disabled={isViewMode}
                          />
                        </Grid>
                      );
                    }
                    // Handle auth fields
                    return (
                      <Grid item xs={12} key={field.name}>
                        <FieldRenderer
                          field={field}
                          value={config[field.name] ?? field.defaultValue ?? ''}
                          onChange={(value) => handleFieldChange(field.name, value)}
                          error={errors[`config.${field.name}`]}
                          disabled={isViewMode}
                        />
                      </Grid>
                    );
                  })}
                </Grid>
              </Paper>
                )}

                {!loading && !loadingConnectors && selectedConnector && allFields.length === 0 && (
                  <Alert severity="info" sx={{ borderRadius: 1.25 }}>
                    No configuration fields required for this connector type.
                  </Alert>
                )}
              </Box>
            </Fade>
          </Box>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          px: 2.5,
          py: 2,
          borderTop: isDark ? `1px solid ${alpha(theme.palette.divider, 0.12)}` : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          flexShrink: 0,
          gap: 1.5,
        }}
      >
        {isEditMode && (
          <Button
            onClick={handleDelete}
            disabled={deleting || saving}
            color="error"
            variant="outlined"
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              px: 2.5,
              py: 0.625,
              borderRadius: 1,
              fontSize: '0.8125rem',
              mr: 'auto',
            }}
            startIcon={deleting ? <CircularProgress size={14} /> : <Iconify icon="mdi:delete" width={14} height={14} />}
          >
            {deleting ? 'Deleting...' : 'Delete'}
          </Button>
        )}

        <Fade in={isViewMode && !!oauthConfigId} timeout={200}>
          <Box>
            {isViewMode && oauthConfigId && (
              <Button
                onClick={() => {
                  // Smoothly transition to edit mode without re-fetching data
                  setMode('edit');
                  if (onModeChange) {
                    onModeChange('edit');
                  }
                }}
                variant="outlined"
                sx={{
                  textTransform: 'none',
                  fontWeight: 500,
                  px: 2.5,
                  py: 0.625,
                  borderRadius: 1,
                  fontSize: '0.8125rem',
                  mr: 'auto',
                  borderColor: isDark ? alpha(theme.palette.primary.main, 0.3) : alpha(theme.palette.primary.main, 0.2),
                  color: theme.palette.primary.main,
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    borderColor: theme.palette.primary.main,
                    backgroundColor: alpha(theme.palette.primary.main, 0.08),
                    transform: 'translateY(-1px)',
                  },
                }}
                startIcon={<Iconify icon="mdi:pencil" width={16} height={16} />}
              >
                Edit
              </Button>
            )}
          </Box>
        </Fade>

        <Button
          onClick={onClose}
          disabled={saving || deleting}
          variant="outlined"
          sx={{
            textTransform: 'none',
            fontWeight: 500,
            px: 2.5,
            py: 0.625,
            borderRadius: 1,
            fontSize: '0.8125rem',
            borderColor: isDark ? alpha(theme.palette.divider, 0.3) : alpha(theme.palette.divider, 0.2),
            color: isDark ? alpha(theme.palette.text.secondary, 0.9) : theme.palette.text.secondary,
            '&:hover': {
              borderColor: isDark ? alpha(theme.palette.text.secondary, 0.5) : alpha(theme.palette.text.secondary, 0.4),
              backgroundColor: isDark ? alpha(theme.palette.common.white, 0.08) : alpha(theme.palette.text.secondary, 0.04),
            },
            transition: 'all 0.2s ease',
          }}
        >
          {isViewMode ? 'Close' : 'Cancel'}
        </Button>

        {!isViewMode && (
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={saving || deleting || !connectorType || !oauthInstanceName.trim()}
            startIcon={saving ? <CircularProgress size={16} /> : <Iconify icon="mdi:check" width={18} height={18} />}
            sx={{
              textTransform: 'none',
              fontWeight: 600,
              px: 3,
              py: 0.625,
              borderRadius: 1,
              fontSize: '0.8125rem',
              boxShadow: isDark ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}` : 'none',
              '&:hover': {
                boxShadow: isDark ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}` : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
              },
              '&:active': { boxShadow: 'none' },
              transition: 'all 0.2s ease',
            }}
          >
            {saving ? (isCreateMode ? 'Creating...' : 'Saving...') : isCreateMode ? 'Create OAuth App' : 'Save Changes'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default OAuthAppDialog;
