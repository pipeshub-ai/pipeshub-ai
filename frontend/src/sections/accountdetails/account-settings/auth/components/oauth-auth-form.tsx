import { z as zod } from 'zod';
import { useForm } from 'react-hook-form';
import { useState, useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';

import {
  Box,
  Alert,
  Button,
  Dialog,
  Switch,
  TextField,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  FormControlLabel,
} from '@mui/material';

import { alpha, useTheme } from '@mui/material/styles';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import { 
  getOAuthConfig, 
  type OAuthConfig, 
  updateOAuthConfig 
} from '../utils/auth-configuration-service';

const getRedirectUris = async () => {
  // Get the current window URL without hash and search parameters
  const currentRedirectUri = `${window.location.origin}/auth/oauth/callback`;

  // Get the frontend URL from the backend
  try {
    const response = await axios.get(`/api/v1/configurationManager/frontendPublicUrl`);
    const frontendBaseUrl = response.data.url;
    // Ensure the URL ends with a slash if needed
    const frontendUrl = frontendBaseUrl.endsWith('/')
      ? `${frontendBaseUrl}auth/oauth/callback`
      : `${frontendBaseUrl}/auth/oauth/callback`;

    return {
      currentRedirectUri,
      recommendedRedirectUri: frontendUrl,
      urisMismatch: currentRedirectUri !== frontendUrl,
    };
  } catch (error) {
    console.error('Error fetching frontend URL:', error);
    return {
      currentRedirectUri,
      recommendedRedirectUri: currentRedirectUri,
      urisMismatch: false,
    };
  }
};

// Validation schema for OAuth configuration
const OAuthConfigSchema = zod.object({
  providerName: zod
    .string()
    .min(1, { message: 'Provider name is required!' }),
  clientId: zod
    .string()
    .min(1, { message: 'Client ID is required!' }),
  clientSecret: zod
    .string()
    .optional(),
  authorizationUrl: zod
    .string()
    .url({ message: 'Please enter a valid authorization URL!' })
    .min(1, { message: 'Authorization URL is required!' }),
  tokenEndpoint: zod
    .string()
    .url({ message: 'Please enter a valid token endpoint URL!' })
    .min(1, { message: 'Token endpoint is required!' }),
  userInfoEndpoint: zod
    .string()
    .url({ message: 'Please enter a valid user info endpoint URL!' })
    .min(1, { message: 'User info endpoint is required!' }),
  scope: zod
    .string()
    .optional(),
  redirectUri: zod
    .string()
    .url({ message: 'Please enter a valid redirect URI!' })
    .optional()
    .or(zod.literal('')),
  enableJit: zod
    .boolean()
    .optional()
    .default(false),
});

type OAuthConfigFormData = zod.infer<typeof OAuthConfigSchema>;

interface OAuthAuthFormProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export default function OAuthAuthForm({ open, onClose, onSuccess }: OAuthAuthFormProps) {
  const theme = useTheme();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [enableJit, setEnableJit] = useState(false);
  const [redirectUris, setRedirectUris] = useState<{
    currentRedirectUri: string;
    recommendedRedirectUri: string;
    urisMismatch: boolean;
  } | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    setValue,
    formState: { errors, isValid },
  } = useForm<OAuthConfigFormData>({
    resolver: zodResolver(OAuthConfigSchema),
    mode: 'onChange',
    defaultValues: {
      providerName: '',
      clientId: '',
      clientSecret: '',
      authorizationUrl: '',
      tokenEndpoint: '',
      userInfoEndpoint: '',
      scope: 'openid email profile',
      redirectUri: '',
      enableJit: true,
    },
  });

  // Load existing configuration
  useEffect(() => {
    if (open) {
      setLoading(true);
      setError(null);
      setSuccess(false);

      // Parallelize API calls for better performance
      Promise.all([
        getRedirectUris(),
        getOAuthConfig(),
      ])
        .then(([uris, config]) => {
          setRedirectUris(uris);

          // Set default redirectUri from uris immediately (not from state)
          const recommendedUri =
            uris?.recommendedRedirectUri || `${window.location.origin}/auth/oauth/callback`;

          if (config) {
            setValue('providerName', config.providerName || '');
            setValue('clientId', config.clientId || '');
            setValue('clientSecret', config.clientSecret || '');
            setValue('authorizationUrl', config.authorizationUrl || '');
            setValue('tokenEndpoint', config.tokenEndpoint || '');
            setValue('userInfoEndpoint', config.userInfoEndpoint || '');
            setValue('scope', config.scope || 'openid email profile');
            setValue('redirectUri', config.redirectUri || recommendedUri);
            setValue('enableJit', config.enableJit || false);
            setEnableJit(config.enableJit || false);
          } else {
            setValue('redirectUri', recommendedUri);
          }
        })
        .catch((err) => {
          console.error('Error loading OAuth configuration:', err);
          // Don't show error for initial load failure (config might not exist yet)
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [open, setValue]);

  const onSubmit = async (data: OAuthConfigFormData) => {
    setSaving(true);
    setError(null);

    try {
      const configData: OAuthConfig = {
        providerName: data.providerName,
        clientId: data.clientId,
        clientSecret: data.clientSecret || undefined,
        authorizationUrl: data.authorizationUrl || undefined,
        tokenEndpoint: data.tokenEndpoint || undefined,
        userInfoEndpoint: data.userInfoEndpoint || undefined,
        scope: data.scope || 'openid email profile',
        redirectUri: data.redirectUri || undefined,
        enableJit,
      };

      await updateOAuthConfig(configData);
      setSuccess(true);
      
      if (onSuccess) {
        onSuccess();
      }

      setTimeout(() => {
        handleClose();
      }, 1500);
    } catch (err) {
      console.error('Error saving OAuth configuration:', err);
      setError('Failed to save OAuth configuration. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const handleClose = () => {
    if (!saving) {
      reset();
      setError(null);
      setSuccess(false);
      setEnableJit(false);
      onClose();
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Iconify
            icon="mdi:shield-key-outline"
            width={28}
            height={28}
            color={theme.palette.primary.main}
          />
          <Typography variant="h6" component="div">
            OAuth Provider Configuration
          </Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1, ml: 5 }}>
          Configure your OAuth 2.0 provider settings for user authentication
        </Typography>
      </DialogTitle>

      <DialogContent>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        ) : (
          <Box component="form" sx={{ mt: 2 }}>
            {error && (
              <Alert severity="error" sx={{ mb: 3 }}>
                {error}
              </Alert>
            )}

            {success && (
              <Alert severity="success" sx={{ mb: 3 }}>
                OAuth configuration saved successfully!
              </Alert>
            )}

            {redirectUris?.urisMismatch && (
              <Alert
                severity="warning"
                sx={{
                  mb: 3,
                  borderRadius: 1,
                }}
              >
                <Typography variant="body2" sx={{ mb: 1 }}>
                  Redirect URI mismatch detected! Using the recommended URI from backend
                  configuration.
                </Typography>
                <Typography variant="caption" component="div">
                  Current redirect Uri: {redirectUris.currentRedirectUri}
                </Typography>
                <Typography variant="caption" component="div">
                  Recommended redirect URI: {redirectUris.recommendedRedirectUri}
                </Typography>
              </Alert>
            )}

            <Box
              sx={{
                mb: 3,
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.info.main, 0.04),
                border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
                display: 'flex',
                alignItems: 'flex-start',
                gap: 1,
              }}
            >
              <Iconify
                icon="eva:info-outline"
                width={20}
                height={20}
                color={theme.palette.info.main}
                style={{ marginTop: 2 }}
              />
              <Box>
                <Typography variant="body2" color="text.secondary">
                  Redirect URI (add to your OAuth provider settings):
                  <Box
                    component="code"
                    sx={{
                      display: 'block',
                      p: 1.5,
                      mt: 1,
                      bgcolor: alpha(theme.palette.background.default, 0.7),
                      borderRadius: 1,
                      fontSize: '0.8rem',
                      fontFamily: 'monospace',
                      wordBreak: 'break-all',
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    {redirectUris?.recommendedRedirectUri || `${window.location.origin}/auth/oauth/callback`}
                  </Box>
                </Typography>
              </Box>
            </Box>

            <Box sx={{ display: 'grid', gap: 3 }}>
              <TextField
                {...register('providerName')}
                label="Provider Name"
                placeholder="e.g., Custom OAuth Provider"
                error={!!errors.providerName}
                helperText={errors.providerName?.message || 'A friendly name for your OAuth provider'}
                fullWidth
                required
                InputLabelProps={{ required: true }}
              />

              <TextField
                {...register('clientId')}
                label="Client ID"
                placeholder="Your OAuth application client ID"
                error={!!errors.clientId}
                helperText={errors.clientId?.message || 'The client ID from your OAuth provider'}
                fullWidth
                required
                InputLabelProps={{ required: true }}
              />

              <TextField
                {...register('clientSecret')}
                label="Client Secret"
                type="password"
                placeholder="Your OAuth application client secret"
                error={!!errors.clientSecret}
                helperText={errors.clientSecret?.message || 'Optional: Client secret if required by your provider'}
                fullWidth
              />

              <TextField
                {...register('authorizationUrl')}
                label="Authorization URL"
                placeholder="https://provider.com/oauth/authorize"
                error={!!errors.authorizationUrl}
                helperText={errors.authorizationUrl?.message || 'The OAuth authorization endpoint URL'}
                fullWidth
                required
                InputLabelProps={{ required: true }}
              />

              <TextField
                {...register('tokenEndpoint')}
                label="Token Endpoint"
                placeholder="https://provider.com/oauth/token"
                error={!!errors.tokenEndpoint}
                helperText={errors.tokenEndpoint?.message || 'URL to exchange authorization code for tokens'}
                fullWidth
                required
                InputLabelProps={{ required: true }}
              />

              <TextField
                {...register('userInfoEndpoint')}
                label="User Info Endpoint"
                placeholder="https://provider.com/oauth/userinfo"
                error={!!errors.userInfoEndpoint}
                helperText={errors.userInfoEndpoint?.message || 'URL to fetch user information with access token'}
                fullWidth
                required
                InputLabelProps={{ required: true }}
              />

              <TextField
                {...register('scope')}
                label="Scope"
                placeholder="openid email profile"
                error={!!errors.scope}
                helperText={errors.scope?.message || 'OAuth scopes to request (space-separated)'}
                fullWidth
              />

              <TextField
                {...register('redirectUri')}
                label="Redirect URI"
                placeholder={redirectUris?.recommendedRedirectUri || 'https://yourapp.com/auth/oauth/callback'}
                error={!!errors.redirectUri}
                helperText={errors.redirectUri?.message || 'This value is automatically configured and cannot be changed'}
                fullWidth
                disabled
                InputProps={{
                  readOnly: true,
                  sx: {
                    bgcolor: alpha(theme.palette.action.disabledBackground, 0.4),
                  },
                }}
              />

              <Box
                sx={{
                  p: 2,
                  borderRadius: 1,
                  bgcolor: alpha(theme.palette.primary.main, 0.04),
                  border: `1px solid ${alpha(theme.palette.primary.main, 0.15)}`,
                }}
              >
                <FormControlLabel
                  control={
                    <Switch
                      checked={enableJit}
                      onChange={(e) => {
                        setEnableJit(e.target.checked);
                        setValue('enableJit', e.target.checked);
                      }}
                      color="primary"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="subtitle2">
                        Enable Just-In-Time (JIT) Provisioning
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Automatically create user accounts when they sign in with OAuth for the first
                        time
                      </Typography>
                    </Box>
                  }
                  sx={{ alignItems: 'flex-start', ml: 0 }}
                />
              </Box>
            </Box>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 3 }}>
        <Button onClick={handleClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          onClick={handleSubmit(onSubmit)}
          variant="contained"
          disabled={!isValid || saving || loading}
          startIcon={saving ? <CircularProgress size={16} /> : null}
        >
          {saving ? 'Saving...' : 'Save Configuration'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}