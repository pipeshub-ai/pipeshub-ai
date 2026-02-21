import React, { useCallback, useEffect, useState } from 'react';
import {
  Container,
  Box,
  alpha,
  useTheme,
  Button,
  Stack,
  Typography,
  TextField,
  FormControlLabel,
  Checkbox,
  FormGroup,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  OutlinedInput,
  Chip,
  Alert,
  IconButton,
  Paper,
  CircularProgress,
} from '@mui/material';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import arrowLeftIcon from '@iconify-icons/mdi/arrow-left';
import contentCopyIcon from '@iconify-icons/mdi/content-copy';
import keyLinkIcon from '@iconify-icons/mdi/key-link';
import { OAuth2Api, type CreateOAuth2AppRequest, type OAuth2AppWithSecret, type ScopeCategory } from './services/oauth2-api';

const GRANT_TYPES = [
  { value: 'authorization_code', label: 'Authorization Code' },
  { value: 'refresh_token', label: 'Refresh Token' },
  { value: 'client_credentials', label: 'Client Credentials' },
] as const;

export function OAuth2NewAppView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const isDark = theme.palette.mode === 'dark';

  const getAppSettingsPath = (appId: string) => {
    const pathname = location.pathname.replace(/\/$/, '');
    const base = pathname.endsWith('/new') ? pathname.slice(0, -4) : pathname.replace(/\/new\/?$/, '');
    return `${base}/${appId}`;
  };

  const oauth2ListPath = (() => {
    const pathname = location.pathname.replace(/\/$/, '');
    return pathname.endsWith('/new') ? pathname.slice(0, -4) : pathname.replace(/\/new\/?$/, '');
  })();

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
  const [allowedGrantTypes, setAllowedGrantTypes] = useState<string[]>(['authorization_code', 'refresh_token']);
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
    if (!name.trim()) {
      setError('App name is required.');
      return;
    }
    if (uris.length === 0) {
      setError('At least one redirect URI is required.');
      return;
    }
    const invalidUri = uris.find((u) => {
      try {
        const parsed = new URL(u);
        return !parsed.href;
      } catch {
        return true;
      }
    });
    if (invalidUri) {
      setError(`Invalid redirect URI: ${invalidUri}`);
      return;
    }
    if (allowedScopes.length === 0) {
      setError('Select at least one scope.');
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
      setCreatedApp(result.app);
      setStep('success');
    } catch (err: any) {
      setError(err?.response?.data?.message || err?.message || 'Failed to create app');
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
      <Container maxWidth="lg" sx={{ py: 3, px: 3 }}>
        <Paper elevation={0} sx={{ p: 3, borderRadius: 2, border: `1px solid ${theme.palette.divider}` }}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 3 }}>
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
              <Iconify icon="mdi:check-circle" width={24} height={24} sx={{ color: theme.palette.success.main }} />
            </Box>
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                OAuth app created
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Save your client secret now. You won’t be able to see it again.
              </Typography>
            </Box>
          </Stack>

          <Alert severity="warning" sx={{ mb: 3 }}>
            Copy the client secret and store it securely. It cannot be shown again. You can regenerate a new secret from the app settings later.
          </Alert>

          <Stack spacing={2}>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                Client ID
              </Typography>
              <Stack direction="row" alignItems="center" spacing={1}>
                <TextField
                  fullWidth
                  size="small"
                  value={createdApp.clientId}
                  sx={{ mt: 0.5, '& .MuiInputBase-input': { fontFamily: 'monospace' } }}
                />
                <IconButton
                  onClick={() => copyToClipboard(createdApp.clientId, 'id')}
                  color={copied === 'id' ? 'success' : 'default'}
                  title="Copy"
                >
                  <Iconify icon={copied === 'id' ? 'mdi:check' : contentCopyIcon} width={20} height={20} />
                </IconButton>
              </Stack>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                Client Secret
              </Typography>
              <Stack direction="row" alignItems="center" spacing={1}>
                <TextField
                  fullWidth
                  size="small"
                  type="password"
                  value={createdApp.clientSecret}
                  sx={{ mt: 0.5, '& .MuiInputBase-input': { fontFamily: 'monospace' } }}
                />
                <IconButton
                  onClick={() => copyToClipboard(createdApp.clientSecret, 'secret')}
                  color={copied === 'secret' ? 'success' : 'default'}
                  title="Copy"
                >
                  <Iconify icon={copied === 'secret' ? 'mdi:check' : contentCopyIcon} width={20} height={20} />
                </IconButton>
              </Stack>
            </Box>
          </Stack>

          <Stack direction="row" spacing={2} sx={{ mt: 4 }}>
            <Button
              variant="contained"
              onClick={() => navigate(oauth2ListPath)}
              sx={{ textTransform: 'none', fontWeight: 600 }}
            >
              Back to OAuth 2.0 apps
            </Button>
            <Button
              variant="outlined"
              component={Link}
              to={getAppSettingsPath(createdApp.id ?? (createdApp as any)._id)}
              sx={{ textTransform: 'none' }}
            >
              Open app settings
            </Button>
          </Stack>
        </Paper>
      </Container>
    );
  }

  return (
    <Container maxWidth="lg" sx={{ py: 3, px: 3 }}>
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
        <Button
          startIcon={<Iconify icon={arrowLeftIcon} width={20} height={20} />}
          onClick={() => navigate(oauth2ListPath)}
          sx={{ textTransform: 'none', minWidth: 'auto', p: 0 }}
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
          <Iconify icon={keyLinkIcon} width={20} height={20} sx={{ color: theme.palette.primary.main }} />
        </Box>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            New OAuth 2.0 App
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Register a new application to use Pipeshub OAuth 2.0
          </Typography>
        </Box>
      </Stack>

      <Paper elevation={0} sx={{ p: 3, borderRadius: 2, border: `1px solid ${theme.palette.divider}` }}>
        <Stack spacing={3}>
          <TextField
            label="App name"
            required
            fullWidth
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Integration"
            helperText="A name to identify your app"
          />
          <TextField
            label="Description"
            fullWidth
            multiline
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description"
          />

          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
              Redirect URIs <Typography component="span" color="error">*</Typography>
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              At least one callback URL. Must be HTTPS in production.
            </Typography>
            {redirectUris.map((uri, index) => (
              <Stack direction="row" spacing={1} key={index} sx={{ mb: 1 }}>
                <TextField
                  fullWidth
                  size="small"
                  placeholder="https://yourapp.com/callback"
                  value={uri}
                  onChange={(e) => setRedirectUriAt(index, e.target.value)}
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
              <Button size="small" startIcon={<Iconify icon="mdi:plus" width={16} height={16} />} onClick={addRedirectUri}>
                Add URI
              </Button>
            )}
          </Box>

          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
              Grant types
            </Typography>
            <FormGroup row>
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
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
              Scopes <Typography component="span" color="error">*</Typography>
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              Select the permissions your app will request. Choose from each category below.
            </Typography>
            {loadingScopes ? (
              <CircularProgress size={24} />
            ) : scopesByCategory && Object.keys(scopesByCategory).length > 0 ? (
              <Stack spacing={2.5}>
                {Object.entries(scopesByCategory).map(([category, scopes]) => {
                  const scopeList = scopes as Array<{ name: string; description: string }>;
                  const selectedInCategory = scopeList.filter((s) => allowedScopes.includes(s.name)).map((s) => s.name);
                  return (
                    <Box key={category}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 600, color: theme.palette.text.secondary, display: 'block', mb: 1 }}>
                        {category}
                      </Typography>
                      <FormControl fullWidth size="medium">
                        <InputLabel>Scopes</InputLabel>
                        <Select
                          multiple
                          value={selectedInCategory}
                          onChange={(e) => {
                            const next = e.target.value as string[];
                            const toRemove = selectedInCategory.filter((s) => !next.includes(s));
                            const toAdd = next.filter((s) => !selectedInCategory.includes(s));
                            setAllowedScopes((prev) => [...prev.filter((x) => !toRemove.includes(x)), ...toAdd]);
                          }}
                          input={<OutlinedInput label="Scopes" />}
                          renderValue={(selected) => (
                            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                              {(selected as string[]).map((value) => (
                                <Chip key={value} size="small" label={value} sx={{ fontFamily: 'monospace' }} />
                              ))}
                            </Box>
                          )}
                        >
                          {scopeList.map((s) => (
                            <MenuItem key={s.name} value={s.name}>
                              <Box>
                                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{s.name}</Typography>
                                <Typography variant="caption" color="text.secondary" display="block">{s.description}</Typography>
                              </Box>
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                    </Box>
                  );
                })}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">
                No scopes available. Contact support.
              </Typography>
            )}
          </Box>

          <TextField
            label="Homepage URL"
            fullWidth
            value={homepageUrl}
            onChange={(e) => setHomepageUrl(e.target.value)}
            placeholder="https://yourapp.com"
          />
          <TextField
            label="Privacy policy URL"
            fullWidth
            value={privacyPolicyUrl}
            onChange={(e) => setPrivacyPolicyUrl(e.target.value)}
            placeholder="https://yourapp.com/privacy"
          />
          <TextField
            label="Terms of service URL"
            fullWidth
            value={termsOfServiceUrl}
            onChange={(e) => setTermsOfServiceUrl(e.target.value)}
            placeholder="https://yourapp.com/terms"
          />

          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          <Stack direction="row" spacing={2}>
            <Button variant="contained" onClick={handleSubmit} disabled={submitting} sx={{ textTransform: 'none', fontWeight: 600 }}>
              {submitting ? <CircularProgress size={20} /> : 'Create OAuth app'}
            </Button>
            <Button variant="outlined" onClick={() => navigate(oauth2ListPath)} sx={{ textTransform: 'none' }}>
              Cancel
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Container>
  );
}
