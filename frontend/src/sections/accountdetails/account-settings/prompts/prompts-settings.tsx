import { useEffect, useMemo, useState } from 'react';
import { alpha, useTheme } from '@mui/material/styles';
import {
  Box,
  Paper,
  Stack,
  Typography,
  TextField,
  Button,
  Container,
  CircularProgress,
  Alert,
  Snackbar,
  Skeleton,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import messageTextIcon from '@iconify-icons/mdi/message-text';
import informationIcon from '@iconify-icons/mdi/information';
import saveIcon from '@iconify-icons/mdi/content-save';
import restoreIcon from '@iconify-icons/mdi/restore';
import axios from 'src/utils/axios';

// Default hardcoded system prompt
const DEFAULT_SYSTEM_PROMPT =
  'You are an assistant. Answer queries in a professional, enterprise-appropriate format.';

type PromptsSettingsData = {
  customSystemPrompt: string;
};

export default function PromptsSettings() {
  const theme = useTheme();
  const [settings, setSettings] = useState<PromptsSettingsData>({
    customSystemPrompt: DEFAULT_SYSTEM_PROMPT,
  });
  const [originalSettings, setOriginalSettings] = useState<PromptsSettingsData>({
    customSystemPrompt: DEFAULT_SYSTEM_PROMPT,
  });
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isDark = theme.palette.mode === 'dark';
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'info' | 'warning',
  });

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await axios.get('/api/v1/configurationManager/prompts/system');
        if (mounted) {
          const customSystemPrompt = res.data?.customSystemPrompt || DEFAULT_SYSTEM_PROMPT;
          const loaded = {
            customSystemPrompt,
          } as PromptsSettingsData;
          setSettings(loaded);
          setOriginalSettings(loaded);
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

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await axios.put('/api/v1/configurationManager/prompts/system', {
        customSystemPrompt: settings.customSystemPrompt,
      });
      showSuccessSnackbar('Custom system prompt saved successfully');
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

  const handleUseDefault = () => {
    setSettings((prev) => ({ ...prev, customSystemPrompt: DEFAULT_SYSTEM_PROMPT }));
  };

  const hasChanges = useMemo(
    () => settings.customSystemPrompt !== originalSettings.customSystemPrompt,
    [settings, originalSettings]
  );

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          p: { xs: 2, md: 3 },
          borderRadius: 1,
          border: '1px solid',
          borderColor: theme.palette.divider,
          backgroundColor: isDark
            ? alpha(theme.palette.background.paper, 0.6)
            : theme.palette.background.paper,
        }}
      >
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
              Custom System Prompt
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                maxWidth: 500,
                lineHeight: 1.5,
              }}
            >
              Configure the custom system prompt for AI responses
            </Typography>
          </Box>
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

        {loading ? (
          /* Loading Skeletons */
          <Stack spacing={3}>
            {/* Header Skeleton */}
            <Box>
              <Skeleton variant="text" width="40%" height={32} sx={{ mb: 1 }} />
              <Skeleton variant="text" width="60%" height={24} />
            </Box>

            {/* System Prompt Section Skeleton */}
            <Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
                <Skeleton variant="circular" width={40} height={40} />
                <Box sx={{ flex: 1 }}>
                  <Skeleton variant="text" width="25%" height={24} sx={{ mb: 0.5 }} />
                  <Skeleton variant="text" width="50%" height={20} />
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
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1.5 }}>
                  <Skeleton variant="text" width="30%" height={20} />
                  <Skeleton variant="rectangular" width={140} height={32} sx={{ borderRadius: 1 }} />
                </Box>
                <Skeleton variant="rectangular" width="100%" height={144} sx={{ borderRadius: 1 }} />
                <Skeleton variant="text" width="90%" height={16} sx={{ mt: 2 }} />
                <Skeleton variant="text" width="75%" height={16} />
              </Box>
            </Box>
          </Stack>
        ) : (
          <Stack spacing={3}>
          {/* Custom System Prompt Section */}
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
                <Iconify icon={messageTextIcon} width={20} height={20} />
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
                  System Prompt
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                  Define the behavior and personality of the AI assistant
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
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  mb: 1.5,
                }}
              >
                <Typography
                  variant="body2"
                  sx={{ fontWeight: 500, color: theme.palette.text.secondary }}
                >
                  Custom System Prompt
                </Typography>
                <Button
                  onClick={handleUseDefault}
                  variant="outlined"
                  color="primary"
                  size="small"
                  startIcon={<Iconify icon={restoreIcon} width={16} height={16} />}
                  sx={{
                    borderRadius: 1,
                    borderColor: theme.palette.primary.main,
                    textTransform: 'none',
                    fontSize: '0.8125rem',
                    '&:hover': {
                      borderColor: theme.palette.primary.dark,
                      backgroundColor: alpha(theme.palette.primary.main, 0.08),
                    },
                  }}
                >
                  Use Default Prompt
                </Button>
              </Box>
              <TextField
                multiline
                rows={6}
                fullWidth
                value={settings.customSystemPrompt}
                onChange={(e) =>
                  setSettings((prev) => ({ ...prev, customSystemPrompt: e.target.value }))
                }
                placeholder="Enter your custom system prompt here..."
                sx={{
                  '& .MuiOutlinedInput-root': {
                    bgcolor: theme.palette.background.paper,
                  },
                }}
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
                  icon={informationIcon}
                  width={16}
                  height={16}
                  sx={{ color: theme.palette.info.main, mt: 0.25, flexShrink: 0 }}
                />
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                  This prompt will be used across all AI chat interactions to guide the
                  assistant&apos;s responses. Changes take effect immediately for new conversations.
                </Typography>
              </Box>
            </Box>
          </Box>
        </Stack>
        )}

        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 3 }}>
          {/* Save button */}
          <Button
            onClick={handleSave}
            disabled={saving || loading || !hasChanges}
            startIcon={
              saving ? (
                <CircularProgress size={18} sx={{ color: 'inherit' }} />
              ) : (
                <Iconify icon={saveIcon} width={18} height={18} />
              )
            }
            variant="contained"
            color="primary"
            sx={{
              borderRadius: 1,
              borderColor: theme.palette.divider,
              color: theme.palette.common.white,
              '&:hover': {
                backgroundColor: theme.palette.primary.main,
              },
            }}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </Button>
        </Box>

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
              Prompt Configuration
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                fontSize: '0.8125rem',
                lineHeight: 1.5,
              }}
            >
              The custom system prompt helps define the AI&apos;s behavior, tone, and approach to
              answering questions. Make sure your prompt is clear and aligns with your
              organization&apos;s needs.
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
