import { z as zod } from 'zod';
import { useForm } from 'react-hook-form';
import { useState, useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';

import {
  Box,
  Alert,
  Button,
  Dialog,
  TextField,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
} from '@mui/material';

import { 
  getOAuthConfig, 
  type OAuthConfig, 
  updateOAuthConfig 
} from '../utils/auth-configuration-service';

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
    .optional()
    .or(zod.literal('')),
  tokenEndpoint: zod
    .string()
    .url({ message: 'Please enter a valid token endpoint URL!' })
    .optional()
    .or(zod.literal('')),
  userInfoEndpoint: zod
    .string()
    .url({ message: 'Please enter a valid user info endpoint URL!' })
    .optional()
    .or(zod.literal('')),
  scope: zod
    .string()
    .optional(),
  redirectUri: zod
    .string()
    .url({ message: 'Please enter a valid redirect URI!' })
    .optional()
    .or(zod.literal('')),
});

type OAuthConfigFormData = zod.infer<typeof OAuthConfigSchema>;

interface OAuthAuthFormProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export default function OAuthAuthForm({ open, onClose, onSuccess }: OAuthAuthFormProps) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

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
    },
  });

  // Load existing configuration
  useEffect(() => {
    if (open) {
      setLoading(true);
      setError(null);
      setSuccess(false);

      getOAuthConfig()
        .then((config: OAuthConfig) => {
          if (config) {
            setValue('providerName', config.providerName || '');
            setValue('clientId', config.clientId || '');
            setValue('clientSecret', config.clientSecret || '');
            setValue('authorizationUrl', config.authorizationUrl || '');
            setValue('tokenEndpoint', config.tokenEndpoint || '');
            setValue('userInfoEndpoint', config.userInfoEndpoint || '');
            setValue('scope', config.scope || 'openid email profile');
            setValue('redirectUri', config.redirectUri || '');
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
      onClose();
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Typography variant="h6" component="div">
          OAuth Provider Configuration
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
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

            <Box sx={{ display: 'grid', gap: 3 }}>
              <TextField
                {...register('providerName')}
                label="Provider Name"
                placeholder="e.g., Custom OAuth Provider"
                error={!!errors.providerName}
                helperText={errors.providerName?.message || 'A friendly name for your OAuth provider'}
                fullWidth
                required
              />

              <TextField
                {...register('clientId')}
                label="Client ID"
                placeholder="Your OAuth application client ID"
                error={!!errors.clientId}
                helperText={errors.clientId?.message || 'The client ID from your OAuth provider'}
                fullWidth
                required
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
              />

              <TextField
                {...register('tokenEndpoint')}
                label="Token Endpoint"
                placeholder="https://provider.com/oauth/token"
                error={!!errors.tokenEndpoint}
                helperText={errors.tokenEndpoint?.message || 'Optional: URL to exchange authorization code for tokens'}
                fullWidth
              />

              <TextField
                {...register('userInfoEndpoint')}
                label="User Info Endpoint"
                placeholder="https://provider.com/oauth/userinfo"
                error={!!errors.userInfoEndpoint}
                helperText={errors.userInfoEndpoint?.message || 'Optional: URL to fetch user information with access token'}
                fullWidth
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
                placeholder="https://yourapp.com/auth/oauth/callback"
                error={!!errors.redirectUri}
                helperText={errors.redirectUri?.message || 'Optional: Custom redirect URI for OAuth callback'}
                fullWidth
              />
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