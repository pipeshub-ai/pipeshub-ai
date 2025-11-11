import { useEffect, useMemo, useState } from 'react';
import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Paper,
  Stack,
  Typography,
  TextField,
  Button,
  Switch,
  FormControlLabel,
  Container,
  CircularProgress,
  Alert,
  Snackbar,
  Link,
  Tooltip,
  InputAdornment,
  Chip,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import axios from 'src/utils/axios';

type PlatformSettingsData = {
  fileUploadMaxSizeBytes: number;
  featureFlags: Record<string, boolean>;
};

const DEFAULTS: PlatformSettingsData = {
  fileUploadMaxSizeBytes: 30 * 1024 * 1024,
  featureFlags: {},
};

export default function PlatformSettings() {
  const theme = useTheme();
  const [settings, setSettings] = useState<PlatformSettingsData>(DEFAULTS);
  const [originalSettings, setOriginalSettings] = useState<PlatformSettingsData>(DEFAULTS);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fileSizeInput, setFileSizeInput] = useState<string>('');
  const [fileSizeError, setFileSizeError] = useState<string | null>(null);
  const isDark = theme.palette.mode === 'dark';
  const [availableFlags, setAvailableFlags] = useState<
    Array<{ key: string; label: string; description?: string; defaultEnabled?: boolean }>
  >([]);
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error',
  });

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await axios.get('/api/v1/configurationManager/platform/settings');
        const flagsRes = await axios.get(
          '/api/v1/configurationManager/platform/feature-flags/available'
        );
        if (mounted) {
          const fileSize =
            Number(res.data?.fileUploadMaxSizeBytes) || DEFAULTS.fileUploadMaxSizeBytes;
          const loaded = {
            fileUploadMaxSizeBytes: fileSize,
            featureFlags: res.data?.featureFlags || {},
          } as PlatformSettingsData;
          setSettings(loaded);
          setOriginalSettings(loaded);
          setAvailableFlags(flagsRes.data?.flags || []);
          setFileSizeInput(String(Math.round(fileSize / (1024 * 1024))));
          setError(null);
        }
      } catch (e: any) {
        if (mounted)
          setError(e?.response?.data?.message || e?.message || 'Failed to load settings');
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const maxMb = useMemo(
    () => Math.round(settings.fileUploadMaxSizeBytes / (1024 * 1024)),
    [settings.fileUploadMaxSizeBytes]
  );

  const handleFileSizeChange = (value: string) => {
    setFileSizeInput(value);
    const mb = Number(value);
    if (!Number.isFinite(mb) || mb < 1) {
      setFileSizeError('Enter a positive number');
      return;
    }
    setFileSizeError(null);
    setSettings((prev) => ({ ...prev, fileUploadMaxSizeBytes: mb * 1024 * 1024 }));
  };

  const handleFileSizeBlur = () => {
    const mb = Number(fileSizeInput);
    if (!Number.isFinite(mb) || mb < 1) {
      // revert to current effective value
      setFileSizeInput(String(maxMb));
      setFileSizeError(null);
      return;
    }
    setFileSizeError(null);
    setSettings((prev) => ({ ...prev, fileUploadMaxSizeBytes: mb * 1024 * 1024 }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await axios.post('/api/v1/configurationManager/platform/settings', settings);
      showSuccessSnackbar('Settings saved successfully');
      setOriginalSettings(settings);
    } catch (e: any) {
      setError(e?.response?.data?.message || e?.message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const showSuccessSnackbar = (message: string) => {
    setSnackbar({
      open: true,
      message,
      severity: 'success',
    });
  };

  const handleCloseSnackbar = () => {
    setSnackbar((prev) => ({ ...prev, open: false }));
  };

  const featureFlagCount = availableFlags.length;
  const activeFlags = availableFlags.filter((f) => !!settings.featureFlags[f.key]).length;

  const hasChanges = useMemo(() => {
    if (settings.fileUploadMaxSizeBytes !== originalSettings.fileUploadMaxSizeBytes) return true;
    const currentKeys = Object.keys(settings.featureFlags);
    const originalKeys = Object.keys(originalSettings.featureFlags);
    if (currentKeys.length !== originalKeys.length) return true;
    return currentKeys.some((k) => !!settings.featureFlags[k] !== !!originalSettings.featureFlags[k]);
  }, [settings, originalSettings]);

  // Basic client-side validation for file size; backend still enforces constraints

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          position: 'relative',
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: theme.palette.divider,
          backgroundColor: isDark
            ? alpha(theme.palette.background.paper, 0.6)
            : theme.palette.background.paper,
        }}
      >
        {/* Loading overlay */}
        {loading && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.background.paper, 0.7),
              backdropFilter: 'blur(4px)',
              zIndex: 10,
            }}
          >
            <CircularProgress size={28} />
          </Box>
        )}

        {/* Header section */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            justifyContent: 'space-between',
            alignItems: { xs: 'flex-start', sm: 'center' },
            mb: 3,
            gap: 2,
          }}
        >
          <Box>
            <Typography
              variant="h5"
              component="h1"
              sx={{
                fontWeight: 600,
                mb: 0.5,
                fontSize: '1.25rem',
                color: theme.palette.text.primary,
              }}
            >
              Platform Settings
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: 500,
                lineHeight: 1.5,
              }}
            >
              Configure platform-wide options and feature toggles
            </Typography>
          </Box>

          {/* Save button with tooltip when disabled */}
          <Tooltip
            title={!hasChanges && !saving && !loading ? 'Make a change to enable Save' : ''}
            placement="left"
            arrow
            disableHoverListener={hasChanges || saving || loading}
          >
            <span>
              <Button
                onClick={handleSave}
                disabled={saving || loading || !hasChanges}
                startIcon={
                  saving ? (
                    <CircularProgress size={18} sx={{ color: 'inherit' }} />
                  ) : (
                    <Iconify icon="mdi:content-save" width={18} height={18} />
                  )
                }
                variant="outlined"
                color="primary"
                sx={{
                  borderRadius: 1,
                  borderColor: theme.palette.divider,
                  color: theme.palette.text.primary,
                  '&:hover': {
                    borderColor: theme.palette.text.primary,
                  },
                }}
              >
                {saving ? 'Saving...' : 'Save Changes'}
              </Button>
            </span>
          </Tooltip>
        </Box>

        {/* Error message */}
        {error && (
          <Alert
            severity="error"
            onClose={() => setError(null)}
            sx={{
              mb: 3,
              borderRadius: 1,
              border: 'none',
              '& .MuiAlert-icon': {
                color: theme.palette.error.main,
              },
            }}
          >
            <Typography variant="body2">{error}</Typography>
          </Alert>
        )}

        <Stack spacing={3}>
          {/* File Upload Limit Section */}
          <Box>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                mb: 2,
              }}
            >
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                  color: theme.palette.primary.main,
                }}
              >
                <Iconify icon="mdi:file-upload" width={20} height={20} />
              </Box>
              <Box>
                <Typography
                  variant="subtitle1"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.9375rem',
                    mb: 0.25,
                  }}
                >
                  File Upload Limit
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                  Maximum file size for uploads
                </Typography>
              </Box>
            </Box>

            <Box
              sx={{
                p: 2.5,
                borderRadius: 1,
                bgcolor: isDark
                  ? alpha(theme.palette.background.default, 0.3)
                  : alpha(theme.palette.grey[50], 0.8),
                border: `1px solid ${theme.palette.divider}`,
              }}
            >
              <TextField
                type="text"
                label="Maximum File Size"
                value={fileSizeInput}
                onChange={(e) => handleFileSizeChange(e.target.value)}
                onBlur={handleFileSizeBlur}
                placeholder="Enter size in MB"
                error={!!fileSizeError}
                helperText={fileSizeError || ''}
                InputProps={{
                  endAdornment: (
                    <InputAdornment position="end">
                      <Chip
                        label="MB"
                        size="small"
                        sx={{
                          height: 24,
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          bgcolor: isDark
                            ? alpha(theme.palette.grey[100], 0.8)
                            : alpha(theme.palette.grey[900], 0.1),
                          color: isDark ? theme.palette.grey[800] : theme.palette.grey[800],
                        }}
                      />
                    </InputAdornment>
                  ),
                }}
                sx={{
                  maxWidth: 400,
                  '& .MuiOutlinedInput-root': {
                    bgcolor: theme.palette.background.paper,
                  },
                }}
                fullWidth
              />

              <Box
                sx={{
                  mt: 2,
                  p: 1.5,
                  borderRadius: 1,
                  bgcolor: alpha(theme.palette.info.main, 0.04),
                  border: `1px solid ${alpha(theme.palette.info.main, 0.1)}`,
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 1,
                }}
              >
                <Iconify
                  icon="mdi:information"
                  width={16}
                  height={16}
                  sx={{ color: theme.palette.info.main, mt: 0.25, flexShrink: 0 }}
                />
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                  Changes apply immediately to all file uploads including Knowledge Base and other
                  backend-enforced uploads
                </Typography>
              </Box>
            </Box>
          </Box>

          {/* Feature Flags Section */}
          <Box>
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                mb: 2,
              }}
            >
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  bgcolor: alpha(theme.palette.success.main, 0.1),
                  color: theme.palette.success.main,
                }}
              >
                <Iconify icon="mdi:flag" width={20} height={20} />
              </Box>
              <Box sx={{ flex: 1 }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography
                    variant="subtitle1"
                    sx={{
                      fontWeight: 600,
                      fontSize: '0.9375rem',
                    }}
                  >
                    Feature Flags
                  </Typography>
                  {featureFlagCount > 0 && (
                    <Chip
                      label={`${activeFlags}/${featureFlagCount} active`}
                      size="small"
                      sx={{
                        height: 20,
                        fontSize: '0.6875rem',
                        fontWeight: 600,
                        bgcolor: isDark
                          ? alpha(theme.palette.grey[100], 0.8)
                          : alpha(theme.palette.success.main, 0.1),
                        color: isDark ? theme.palette.grey[800] : theme.palette.success.main,
                      }}
                    />
                  )}
                </Stack>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                  Toggle platform features on or off
                </Typography>
              </Box>
            </Box>

            <Box
              sx={{
                p: 2.5,
                borderRadius: 1,
                bgcolor: isDark
                  ? alpha(theme.palette.background.default, 0.3)
                  : alpha(theme.palette.grey[50], 0.8),
                border: `1px solid ${theme.palette.divider}`,
                minHeight: 100,
              }}
            >
              {featureFlagCount === 0 ? (
                <Box
                  sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    py: 4,
                  }}
                >
                  <Iconify
                    icon="mdi:flag-off"
                    width={40}
                    height={40}
                    sx={{ color: theme.palette.text.disabled, mb: 1.5 }}
                  />
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem' }}>
                    No feature flags configured
                  </Typography>
                </Box>
              ) : (
                <Stack spacing={1.5}>
                  {availableFlags.map((f) => {
                    const key = f.key;
                    const value = !!settings.featureFlags[key];
                    return (
                      <Box
                        key={key}
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          p: 2,
                          borderRadius: 1,
                          bgcolor: theme.palette.background.paper,
                          border: `1px solid ${theme.palette.divider}`,
                          transition: 'all 0.2s',
                          '&:hover': {
                            borderColor: alpha(theme.palette.primary.main, 0.3),
                            bgcolor: alpha(theme.palette.primary.main, 0.02),
                          },
                        }}
                      >
                        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ flex: 1 }}>
                          <Box
                            sx={{
                              width: 8,
                              height: 8,
                              borderRadius: '50%',
                              bgcolor: value
                                ? theme.palette.success.main
                                : theme.palette.text.disabled,
                              flexShrink: 0,
                            }}
                          />
                          <Box sx={{ flex: 1 }}>
                            <Typography
                              variant="body2"
                              sx={{
                                fontWeight: 500,
                                fontSize: '0.875rem',
                                fontFamily: 'monospace',
                                color: theme.palette.text.primary,
                              }}
                            >
                              {f.label || key}
                            </Typography>
                            {f.description && (
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{ display: 'block', mt: 0.5, fontSize: '0.8125rem' }}
                              >
                                {f.description}
                              </Typography>
                            )}
                          </Box>
                        </Stack>
                        <FormControlLabel
                          control={
                            <Switch
                              checked={!!value}
                              onChange={(_, checked) =>
                                setSettings((p) => ({
                                  ...p,
                                  featureFlags: { ...p.featureFlags, [key]: checked },
                                }))
                              }
                              size="small"
                            />
                          }
                          label=""
                          sx={{ m: 0 }}
                        />
                      </Box>
                    );
                  })}
                </Stack>
              )}
            </Box>
          </Box>
        </Stack>

        {/* Info box */}
        <Box
          sx={{
            mt: 3,
            p: 2.5,
            borderRadius: 1,
            bgcolor: isDark
              ? alpha(theme.palette.info.main, 0.08)
              : alpha(theme.palette.info.main, 0.04),
            border: `1px solid ${alpha(theme.palette.info.main, isDark ? 0.2 : 0.1)}`,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 1.5,
          }}
        >
          <Box sx={{ color: theme.palette.info.main, mt: 0.5 }}>
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M12 16V12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M12 8H12.01"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Box>
          <Box>
            <Typography
              variant="subtitle2"
              color="text.primary"
              sx={{
                mb: 0.5,
                fontWeight: 600,
                fontSize: '0.875rem',
              }}
            >
              Platform Configuration
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                fontSize: '0.8125rem',
                lineHeight: 1.5,
              }}
            >
              Changes to platform settings affect all users and take effect immediately. Feature
              flags can be toggled to enable or disable specific functionality across the platform.
            </Typography>
          </Box>
        </Box>
      </Paper>

      {/* Snackbar for success and error messages */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{
            width: '100%',
            boxShadow: isDark
              ? '0px 3px 8px rgba(0, 0, 0, 0.3)'
              : '0px 3px 8px rgba(0, 0, 0, 0.12)',
            '& .MuiAlert-icon': {
              opacity: 0.8,
            },
            fontSize: '0.8125rem',
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
