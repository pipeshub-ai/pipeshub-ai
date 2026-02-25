import { z } from 'zod';
import { useState, useEffect } from 'react';
import keyLinkIcon from '@iconify-icons/mdi/key-link';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import contentCopyIcon from '@iconify-icons/mdi/content-copy';
import { Link, useNavigate, useLocation } from 'react-router-dom';

import {
  Box,
  alpha,
  Stack,
  Alert,
  Paper,
  Button,
  useTheme,
  Checkbox,
  Container,
  TextField,
  FormGroup,
  Typography,
  IconButton,
  FormControlLabel,
  CircularProgress,
} from '@mui/material';

import { getOAuth2Paths } from 'src/routes/paths';

import { Iconify } from 'src/components/iconify';

import { OAuth2ScopeSelector } from './oauth2-scope-selector';
import {
  OAuth2Api,
  type ScopeCategory,
  type OAuth2AppWithSecret,
  type CreateOAuth2AppRequest,
} from './services/oauth2-api';

const GRANT_TYPES = [
  { value: 'authorization_code', label: 'Authorization Code' },
  { value: 'refresh_token', label: 'Refresh Token' },
  { value: 'client_credentials', label: 'Client Credentials' },
] as const;

const optionalUrlSchema = z.union([z.literal(''), z.string().url('Must be a valid URL')]);

const createAppSchema = z.object({
  name: z.string().min(1, 'Application name is required.'),
  redirectUris: z
    .array(z.string().url('Invalid redirect URI'))
    .min(1, 'At least one redirect URI is required.'),
  allowedScopes: z.array(z.string()).min(1, 'At least one scope must be selected.'),
  homepageUrl: optionalUrlSchema,
  privacyPolicyUrl: optionalUrlSchema,
  termsOfServiceUrl: optionalUrlSchema,
});

export function OAuth2NewAppView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const isDark = theme.palette.mode === 'dark';

  const oauth2Paths = getOAuth2Paths(location.pathname);
  const oauth2ListPath = oauth2Paths.root;

  const [step, setStep] = useState<'form' | 'success'>('form');
  const [createdApp, setCreatedApp] = useState<OAuth2AppWithSecret | null>(null);
  const [scopesByCategory, setScopesByCategory] = useState<ScopeCategory | null>(null);
  const [loadingScopes, setLoadingScopes] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<'id' | 'secret' | null>(null);

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [redirectUris, setRedirectUris] = useState<string[]>(['']);
  const [allowedGrantTypes, setAllowedGrantTypes] = useState<string[]>([
    'authorization_code',
    'refresh_token',
  ]);
  const [allowedScopes, setAllowedScopes] = useState<string[]>([]);
  const [homepageUrl, setHomepageUrl] = useState('');
  const [privacyPolicyUrl, setPrivacyPolicyUrl] = useState('');
  const [termsOfServiceUrl, setTermsOfServiceUrl] = useState('');

  useEffect(() => {
    OAuth2Api.listScopes()
      .then((res) => setScopesByCategory(res.scopes || {}))
      .catch(() => setScopesByCategory({}))
      .finally(() => setLoadingScopes(false));
  }, []);

  const addRedirectUri = () => {
    if (redirectUris.length < 10) setRedirectUris((prev) => [...prev, '']);
  };
  const setRedirectUriAt = (index: number, value: string) => {
    setRedirectUris((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  };
  const removeRedirectUri = (index: number) => {
    setRedirectUris((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async () => {
    setError(null);
    const uris = redirectUris.map((u) => u.trim()).filter(Boolean);

    const validation = createAppSchema.safeParse({
      name: name.trim(),
      redirectUris: uris,
      allowedScopes,
      homepageUrl: homepageUrl.trim(),
      privacyPolicyUrl: privacyPolicyUrl.trim(),
      termsOfServiceUrl: termsOfServiceUrl.trim(),
    });

    if (!validation.success) {
      setError(validation.error.errors[0]?.message || 'Validation failed.');
      return;
    }

    setSubmitting(true);
    try {
      const body: CreateOAuth2AppRequest = {
        name: name.trim(),
        description: description.trim() || undefined,
        redirectUris: uris,
        allowedGrantTypes: allowedGrantTypes.length > 0 ? allowedGrantTypes : undefined,
        allowedScopes,
        homepageUrl: homepageUrl.trim() || undefined,
        privacyPolicyUrl: privacyPolicyUrl.trim() || undefined,
        termsOfServiceUrl: termsOfServiceUrl.trim() || undefined,
        isConfidential: true,
      };
      const result = await OAuth2Api.createApp(body);
      if (!result?.app) {
        setError('Unexpected response from server. Please try again.');
        return;
      }
      setCreatedApp(result.app);
      setStep('success');
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || 'Failed to create application.');
    } finally {
      setSubmitting(false);
    }
  };

  const copyToClipboard = (text: string, which: 'id' | 'secret') => {
    navigator.clipboard.writeText(text);
    setCopied(which);
    setTimeout(() => setCopied(null), 2000);
  };

  if (step === 'success' && createdApp) {
    return (
      <Container maxWidth="lg" sx={{ py: 3, px: 3, flex: 1, overflow: 'auto' }}>
        <Paper
          elevation={0}
          sx={{ p: 3, borderRadius: 2, border: `1px solid ${theme.palette.divider}` }}
        >
          <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
            <Box
              sx={{
                width: 40,
                height: 40,
                borderRadius: 1.5,
                backgroundColor: alpha(theme.palette.success.main, 0.12),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon="mdi:check-circle"
                width={24}
                height={24}
                sx={{ color: theme.palette.success.main }}
              />
            </Box>
            <Box>
              <Typography sx={{ fontWeight: 600, fontSize: '1.25rem' }}>
                OAuth application created
              </Typography>
              <Typography sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary }}>
                Store the client secret securely. It will not be displayed again.
              </Typography>
            </Box>
          </Stack>

          <Alert severity="warning" sx={{ mb: 3, '& .MuiAlert-message': { fontSize: '0.875rem' } }}>
            Copy and store it securely. You won&apos;t be able to view it again
          </Alert>

          <Stack spacing={2.5}>
            <Box>
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: theme.palette.text.secondary,
                  display: 'block',
                  mb: 0.5,
                }}
              >
                Client ID
              </Typography>
              <Paper
                variant="outlined"
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  py: 1.25,
                  px: 2,
                  bgcolor: alpha(
                    theme.palette.primary.main,
                    theme.palette.mode === 'dark' ? 0.12 : 0.06
                  ),
                  borderColor: alpha(theme.palette.primary.main, 0.3),
                }}
              >
                <Box
                  component="code"
                  sx={{
                    flex: 1,
                    fontFamily: 'monospace',
                    fontSize: '0.8125rem',
                    overflow: 'auto',
                    minWidth: 0,
                  }}
                >
                  {createdApp.clientId}
                </Box>
                <IconButton
                  size="small"
                  onClick={() => copyToClipboard(createdApp.clientId, 'id')}
                  color={copied === 'id' ? 'success' : 'default'}
                  title="Copy"
                >
                  <Iconify
                    icon={copied === 'id' ? 'mdi:check' : contentCopyIcon}
                    width={20}
                    height={20}
                  />
                </IconButton>
              </Paper>
            </Box>
            <Box>
              <Typography
                sx={{
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: theme.palette.text.secondary,
                  display: 'block',
                  mb: 0.5,
                }}
              >
                Client Secret
              </Typography>
              <Paper
                variant="outlined"
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  py: 1.25,
                  px: 2,
                  bgcolor: alpha(theme.palette.warning.main, isDark ? 0.12 : 0.08),
                  borderColor: alpha(theme.palette.warning.main, isDark ? 0.5 : 1),
                }}
              >
                <Box
                  component="code"
                  sx={{
                    flex: 1,
                    fontFamily: 'monospace',
                    fontSize: '0.8125rem',
                    overflow: 'auto',
                    minWidth: 0,
                  }}
                >
                  {createdApp.clientSecret}
                </Box>
                <IconButton
                  size="small"
                  onClick={() => copyToClipboard(createdApp.clientSecret, 'secret')}
                  color={copied === 'secret' ? 'success' : 'default'}
                  title="Copy"
                >
                  <Iconify
                    icon={copied === 'secret' ? 'mdi:check' : contentCopyIcon}
                    width={20}
                    height={20}
                  />
                </IconButton>
              </Paper>
            </Box>
          </Stack>

          <Stack direction="row" spacing={2} sx={{ mt: 3 }}>
            <Button
              variant="contained"
              onClick={() => navigate(oauth2ListPath)}
              sx={{ textTransform: 'none', fontWeight: 600, fontSize: '0.875rem', py: 1, px: 2 }}
            >
              Return to OAuth 2.0 applications
            </Button>
            <Button
              variant="outlined"
              component={Link}
              to={oauth2Paths.app(createdApp.id)}
              sx={{ textTransform: 'none', fontSize: '0.875rem', py: 1, px: 2 }}
            >
              Open application settings
            </Button>
          </Stack>
        </Paper>
      </Container>
    );
  }

  return (
    <Container maxWidth="lg" sx={{ py: 3, px: 3, flex: 1, overflow: 'auto' }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
        <Button
          startIcon={<Iconify icon={arrowLeftIcon} width={20} height={20} />}
          onClick={() => navigate(oauth2ListPath)}
          sx={{ textTransform: 'none', minWidth: 'auto', p: 0, fontSize: '0.875rem' }}
        >
          Back
        </Button>
        <Box
          sx={{
            width: 40,
            height: 40,
            borderRadius: 1.5,
            backgroundColor: alpha(theme.palette.primary.main, 0.1),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Iconify
            icon={keyLinkIcon}
            width={20}
            height={20}
            sx={{ color: theme.palette.primary.main }}
          />
        </Box>
        <Box>
          <Typography sx={{ fontWeight: 600, fontSize: '1.5rem' }}>
            New OAuth 2.0 application
          </Typography>
          <Typography sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary, mt: 0.25 }}>
            Register a new application for Pipeshub OAuth 2.0.
          </Typography>
        </Box>
      </Stack>

      <Paper
        elevation={0}
        sx={{ p: 3, borderRadius: 2, border: `1px solid ${theme.palette.divider}` }}
      >
        <Stack spacing={3}>
          <TextField
            label="App name"
            required
            fullWidth
            size="small"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Integration"
            helperText="A display name for this application."
            sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
          />
          <TextField
            label="Description"
            fullWidth
            multiline
            rows={2}
            size="small"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional application description."
            sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
          />

          <Box>
            <Typography sx={{ fontSize: '0.875rem', fontWeight: 600, mb: 1 }}>
              Redirect URIs{' '}
              <Typography component="span" color="error">
                *
              </Typography>
            </Typography>
            <Typography
              sx={{
                fontSize: '0.75rem',
                color: theme.palette.text.secondary,
                display: 'block',
                mb: 1.5,
              }}
            >
              At least one callback URL is required. HTTPS is required in production environments.
            </Typography>
            {redirectUris.map((uri, index) => (
              <Stack direction="row" spacing={1.5} key={index} sx={{ mb: 1.5 }}>
                <TextField
                  fullWidth
                  size="small"
                  placeholder="https://yourapp.com/callback"
                  value={uri}
                  onChange={(e) => setRedirectUriAt(index, e.target.value)}
                  sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
                />
                <IconButton
                  onClick={() => removeRedirectUri(index)}
                  disabled={redirectUris.length <= 1}
                  size="small"
                >
                  <Iconify icon="mdi:minus-circle-outline" width={20} height={20} />
                </IconButton>
              </Stack>
            ))}
            {redirectUris.length < 10 && (
              <Button
                size="small"
                startIcon={<Iconify icon="mdi:plus" width={16} height={16} />}
                onClick={addRedirectUri}
                sx={{ textTransform: 'none' }}
              >
                Add redirect URI
              </Button>
            )}
          </Box>

          <Box>
            <Typography sx={{ fontSize: '0.875rem', fontWeight: 600, mb: 1.5 }}>
              Grant types
            </Typography>
            <FormGroup row sx={{ gap: 0.5 }}>
              {GRANT_TYPES.map((g) => (
                <FormControlLabel
                  key={g.value}
                  control={
                    <Checkbox
                      checked={allowedGrantTypes.includes(g.value)}
                      onChange={(e) =>
                        setAllowedGrantTypes((prev) =>
                          e.target.checked ? [...prev, g.value] : prev.filter((x) => x !== g.value)
                        )
                      }
                    />
                  }
                  label={g.label}
                />
              ))}
            </FormGroup>
          </Box>

          <Box>
            <Typography sx={{ fontSize: '0.875rem', fontWeight: 600, mb: 1 }}>
              Scopes{' '}
              <Typography component="span" color="error">
                *
              </Typography>
            </Typography>
            <Typography
              sx={{
                fontSize: '0.75rem',
                color: theme.palette.text.secondary,
                display: 'block',
                mb: 1.5,
              }}
            >
              Select the permissions this application will request. Select from the categories
              below.
            </Typography>
            {loadingScopes ? (
              <CircularProgress size={24} />
            ) : scopesByCategory && Object.keys(scopesByCategory).length > 0 ? (
              <OAuth2ScopeSelector
                scopesByCategory={scopesByCategory}
                allowedScopes={allowedScopes}
                onChange={setAllowedScopes}
              />
            ) : (
              <Typography sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary }}>
                No scopes are available. Please contact support.
              </Typography>
            )}
          </Box>

          <TextField
            label="Homepage URL"
            fullWidth
            size="small"
            value={homepageUrl}
            onChange={(e) => setHomepageUrl(e.target.value)}
            placeholder="https://yourapp.com"
            sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
          />
          <TextField
            label="Privacy policy URL"
            fullWidth
            size="small"
            value={privacyPolicyUrl}
            onChange={(e) => setPrivacyPolicyUrl(e.target.value)}
            placeholder="https://yourapp.com/privacy"
            sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
          />
          <TextField
            label="Terms of service URL"
            fullWidth
            size="small"
            value={termsOfServiceUrl}
            onChange={(e) => setTermsOfServiceUrl(e.target.value)}
            placeholder="https://yourapp.com/terms"
            sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
          />

          {error && (
            <Alert
              severity="error"
              onClose={() => setError(null)}
              sx={{ '& .MuiAlert-message': { fontSize: '0.875rem' } }}
            >
              {error}
            </Alert>
          )}

          <Stack direction="row" spacing={2}>
            <Button
              variant="contained"
              onClick={handleSubmit}
              disabled={submitting}
              sx={{ textTransform: 'none', fontWeight: 600, fontSize: '0.875rem', py: 1, px: 2 }}
            >
              {submitting ? <CircularProgress size={20} /> : 'Create OAuth application'}
            </Button>
            <Button
              variant="outlined"
              onClick={() => navigate(oauth2ListPath)}
              sx={{ textTransform: 'none', fontSize: '0.875rem', py: 1, px: 2 }}
            >
              Cancel
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Container>
  );
}
