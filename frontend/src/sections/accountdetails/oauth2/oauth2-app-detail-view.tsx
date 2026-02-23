import React, { useCallback, useEffect, useState } from 'react';
import {
  Box,
  alpha,
  useTheme,
  Button,
  Stack,
  Typography,
  TextField,
  CircularProgress,
  Alert,
  Snackbar,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  OutlinedInput,
  Chip,
  FormControlLabel,
  Checkbox,
  FormGroup,
} from '@mui/material';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { Iconify } from 'src/components/iconify';
import gridIcon from '@iconify-icons/mdi/grid';
import lockIcon from '@iconify-icons/mdi/lock';
import cogIcon from '@iconify-icons/mdi/cog';
import alertOctagonIcon from '@iconify-icons/mdi/alert-octagon';
import keyLinkIcon from '@iconify-icons/mdi/key-link';
import contentCopyIcon from '@iconify-icons/mdi/content-copy';
import refreshIcon from '@iconify-icons/mdi/refresh';
import deleteForeverIcon from '@iconify-icons/mdi/delete-forever';
import { OAuth2Api, type OAuth2App, type UpdateOAuth2AppRequest, type ScopeCategory } from './services/oauth2-api';

type DetailSection = 'general' | 'permissions' | 'advanced' | 'danger';

export function OAuth2AppDetailView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { appId } = useParams<{ appId: string }>();
  const isDark = theme.palette.mode === 'dark';

  const pathSegments = location.pathname.split('/').filter(Boolean);
  const oauth2ListPath = `/${pathSegments.slice(0, -1).join('/')}`;

  const [app, setApp] = useState<OAuth2App | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [scopesByCategory, setScopesByCategory] = useState<ScopeCategory | null>(null);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({ open: false, message: '', severity: 'success' });
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [section, setSection] = useState<DetailSection>('general');

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [redirectUris, setRedirectUris] = useState<string[]>([]);
  const [allowedGrantTypes, setAllowedGrantTypes] = useState<string[]>([]);
  const [allowedScopes, setAllowedScopes] = useState<string[]>([]);
  const [homepageUrl, setHomepageUrl] = useState('');
  const [privacyPolicyUrl, setPrivacyPolicyUrl] = useState('');
  const [termsOfServiceUrl, setTermsOfServiceUrl] = useState('');

  const loadApp = useCallback(() => {
    if (!appId) return;
    setLoading(true);
    OAuth2Api.getApp(appId)
      .then((data) => {
        setApp(data);
        setName(data.name);
        setDescription(data.description || '');
        setRedirectUris(data.redirectUris?.length ? data.redirectUris : ['']);
        setAllowedGrantTypes(data.allowedGrantTypes?.length ? data.allowedGrantTypes : ['authorization_code', 'refresh_token']);
        setAllowedScopes(data.allowedScopes || []);
        setHomepageUrl(data.homepageUrl || '');
        setPrivacyPolicyUrl(data.privacyPolicyUrl || '');
        setTermsOfServiceUrl(data.termsOfServiceUrl || '');
      })
      .catch(() => {
        setSnackbar({ open: true, message: 'Failed to load app', severity: 'error' });
        setApp(null);
      })
      .finally(() => setLoading(false));
  }, [appId]);

  useEffect(() => {
    loadApp();
  }, [loadApp]);

  useEffect(() => {
    OAuth2Api.listScopes().then((res) => setScopesByCategory(res.scopes || {})).catch(() => setScopesByCategory({}));
  }, []);

  const handleSave = async () => {
    if (!appId || !app) return;
    const uris = redirectUris.map((u) => u.trim()).filter(Boolean);
    if (uris.length === 0) {
      setSnackbar({ open: true, message: 'At least one redirect URI is required', severity: 'error' });
      return;
    }
    if (allowedScopes.length === 0) {
      setSnackbar({ open: true, message: 'At least one scope is required', severity: 'error' });
      return;
    }
    setSaving(true);
    try {
      const body: UpdateOAuth2AppRequest = {
        name: name.trim(),
        description: description.trim() || undefined,
        redirectUris: uris,
        allowedGrantTypes: allowedGrantTypes.length > 0 ? allowedGrantTypes : undefined,
        allowedScopes,
        homepageUrl: homepageUrl.trim() || null,
        privacyPolicyUrl: privacyPolicyUrl.trim() || null,
        termsOfServiceUrl: termsOfServiceUrl.trim() || null,
      };
      const result = await OAuth2Api.updateApp(appId, body);
      setApp(result.app);
      setSnackbar({ open: true, message: 'App updated', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.response?.data?.message || err?.message || 'Update failed', severity: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const handleRegenerateSecret = async () => {
    if (!appId) return;
    setRegenerating(true);
    try {
      const result = await OAuth2Api.regenerateSecret(appId);
      setNewSecret(result.clientSecret);
      setSnackbar({ open: true, message: 'New client secret generated. Save it now; it won’t be shown again.', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.response?.data?.message || 'Failed to regenerate secret', severity: 'error' });
    } finally {
      setRegenerating(false);
    }
  };

  const handleSuspend = async () => {
    if (!appId) return;
    try {
      const result = await OAuth2Api.suspendApp(appId);
      setApp(result.app);
      setSnackbar({ open: true, message: 'App suspended', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.response?.data?.message || 'Failed to suspend', severity: 'error' });
    }
  };

  const handleActivate = async () => {
    if (!appId) return;
    try {
      const result = await OAuth2Api.activateApp(appId);
      setApp(result.app);
      setSnackbar({ open: true, message: 'App activated', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.response?.data?.message || 'Failed to activate', severity: 'error' });
    }
  };

  const handleRevokeAllTokens = async () => {
    if (!appId) return;
    try {
      await OAuth2Api.revokeAllTokens(appId);
      setSnackbar({ open: true, message: 'All tokens revoked', severity: 'success' });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.response?.data?.message || 'Failed to revoke tokens', severity: 'error' });
    }
  };

  const handleDelete = async () => {
    if (!appId) return;
    try {
      await OAuth2Api.deleteApp(appId);
      setSnackbar({ open: true, message: 'App deleted', severity: 'success' });
      setDeleteDialogOpen(false);
      navigate(oauth2ListPath, { replace: true });
    } catch (err: any) {
      setSnackbar({ open: true, message: err?.response?.data?.message || 'Failed to delete', severity: 'error' });
    }
  };

  const setRedirectUriAt = (index: number, value: string) => {
    setRedirectUris((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  };
  const addRedirectUri = () => {
    if (redirectUris.length < 10) setRedirectUris((prev) => [...prev, '']);
  };
  const removeRedirectUri = (index: number) => {
    setRedirectUris((prev) => prev.filter((_, i) => i !== index));
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setSnackbar({ open: true, message: 'Copied to clipboard', severity: 'success' });
  };

  if (loading && !app) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 320 }}>
        <Stack alignItems="center" spacing={2}>
          <CircularProgress />
          <Typography color="text.secondary">Loading app…</Typography>
        </Stack>
      </Box>
    );
  }

  if (!app) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">App not found.</Alert>
        <Button startIcon={<Iconify icon="mdi:arrow-left" />} onClick={() => navigate('..')} sx={{ mt: 2 }}>
          Back to OAuth 2.0
        </Button>
      </Box>
    );
  }

  const sidebarBg = isDark ? alpha(theme.palette.background.default, 0.4) : alpha(theme.palette.grey[50], 0.6);
  const selectedBg = alpha(theme.palette.primary.main, isDark ? 0.2 : 0.1);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header with back button */}
      <Box sx={{ px: 3, py: 2, borderBottom: `1px solid ${theme.palette.divider}` }}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <Button
            startIcon={<Iconify icon="mdi:arrow-left" width={20} height={20} />}
            onClick={() => navigate(oauth2ListPath)}
            sx={{ textTransform: 'none', minWidth: 'auto', px: 1 }}
          >
            Back
          </Button>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {app.name}
          </Typography>
        </Stack>
      </Box>

      <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left sidebar - GitHub App style */}
        <Box
          sx={{
            width: 240,
            flexShrink: 0,
            borderRight: `1px solid ${theme.palette.divider}`,
            backgroundColor: sidebarBg,
            py: 2,
          }}
        >
          <Typography variant="caption" sx={{ px: 2, fontWeight: 600, color: theme.palette.text.secondary, letterSpacing: '0.05em' }}>
            GENERAL
          </Typography>
          <List disablePadding sx={{ mt: 0.5 }}>
            <ListItemButton
              selected={section === 'general'}
              onClick={() => setSection('general')}
              sx={{
                py: 1,
                '&.Mui-selected': { backgroundColor: selectedBg, borderRight: `3px solid ${theme.palette.primary.main}` },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Iconify icon={gridIcon} width={18} height={18} />
              </ListItemIcon>
              <ListItemText primary="General" primaryTypographyProps={{ fontSize: '0.875rem' }} />
            </ListItemButton>
            <ListItemButton
              selected={section === 'permissions'}
              onClick={() => setSection('permissions')}
              sx={{
                py: 1,
                '&.Mui-selected': { backgroundColor: selectedBg, borderRight: `3px solid ${theme.palette.primary.main}` },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Iconify icon={lockIcon} width={18} height={18} />
              </ListItemIcon>
              <ListItemText primary="Permissions & scopes" primaryTypographyProps={{ fontSize: '0.875rem' }} />
            </ListItemButton>
            <ListItemButton
              selected={section === 'advanced'}
              onClick={() => setSection('advanced')}
              sx={{
                py: 1,
                '&.Mui-selected': { backgroundColor: selectedBg, borderRight: `3px solid ${theme.palette.primary.main}` },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Iconify icon={cogIcon} width={18} height={18} />
              </ListItemIcon>
              <ListItemText primary="Advanced" primaryTypographyProps={{ fontSize: '0.875rem' }} />
            </ListItemButton>
          </List>
          <Divider sx={{ my: 2 }} />
          <Typography variant="caption" sx={{ px: 2, fontWeight: 600, color: theme.palette.text.secondary, letterSpacing: '0.05em' }}>
            DANGER ZONE
          </Typography>
          <List disablePadding sx={{ mt: 0.5 }}>
            <ListItemButton
              selected={section === 'danger'}
              onClick={() => setSection('danger')}
              sx={{
                py: 1,
                '&.Mui-selected': { backgroundColor: alpha(theme.palette.error.main, 0.08), borderRight: `3px solid ${theme.palette.error.main}` },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Iconify icon={alertOctagonIcon} width={18} height={18} sx={{ color: theme.palette.error.main }} />
              </ListItemIcon>
              <ListItemText primary="Danger zone" primaryTypographyProps={{ fontSize: '0.875rem' }} />
            </ListItemButton>
          </List>
        </Box>

        {/* Main content */}
        <Box sx={{ flex: 1, overflow: 'auto', p: 3 }}>
          {newSecret && (
            <Alert severity="warning" sx={{ mb: 3 }} onClose={() => setNewSecret(null)}>
              <Typography variant="subtitle2">New client secret (copy now):</Typography>
              <Typography component="code" sx={{ fontFamily: 'monospace', fontSize: '0.875rem' }}>
                {newSecret}
              </Typography>
            </Alert>
          )}

          {section === 'general' && (
            <>
              {/* About */}
              <Box sx={{ mb: 4 }}>
                <Typography variant="h6" sx={{ fontWeight: 600, mb: 2 }}>
                  About
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  App ID: <Typography component="span" sx={{ fontFamily: 'monospace' }}>{app.id}</Typography>
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  Use Client ID (not App ID) for OAuth flows. Client ID:{' '}
                  <Typography component="span" sx={{ fontFamily: 'monospace' }}>{app.clientId}</Typography>
                  <IconButton size="small" onClick={() => copyToClipboard(app.clientId)} sx={{ ml: 0.5 }}>
                    <Iconify icon={contentCopyIcon} width={16} height={16} />
                  </IconButton>
                </Typography>
                <Button variant="contained" color="error" size="medium" onClick={handleRevokeAllTokens} sx={{ mt: 2, textTransform: 'none' }}>
                  Revoke all user tokens
                </Button>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 2, maxWidth: 560 }}>
                  OAuth 2.0 apps use client credentials to authenticate to the API. Revoking all tokens will sign out every user who has authorized this app.
                </Typography>
              </Box>

              <Divider sx={{ my: 3 }} />

              {/* Client secrets */}
              <Box sx={{ mb: 4 }}>
                <Typography variant="h6" sx={{ fontWeight: 600, mb: 1 }}>
                  Client secrets
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  You need a client secret to authenticate as the application to the API.
                </Typography>
                <Button
                  variant="outlined"
                  startIcon={regenerating ? <CircularProgress size={16} /> : <Iconify icon={refreshIcon} width={18} height={18} />}
                  onClick={handleRegenerateSecret}
                  disabled={regenerating}
                  sx={{ textTransform: 'none' }}
                >
                  Generate a new client secret
                </Button>
              </Box>

              <Divider sx={{ my: 3 }} />

              {/* Basic information */}
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 600, mb: 2 }}>
                  Basic information
                </Typography>
                <Stack spacing={2} sx={{ maxWidth: 560 }}>
                  <TextField
                    label="OAuth App name"
                    required
                    fullWidth
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    helperText="The name of your OAuth app."
                  />
                  <TextField
                    label="Description"
                    fullWidth
                    multiline
                    rows={3}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="This is displayed to users of your OAuth app."
                  />
                  <TextField label="Homepage URL" fullWidth value={homepageUrl} onChange={(e) => setHomepageUrl(e.target.value)} placeholder="https://yourapp.com" />
                  <Box>
                    <Typography variant="subtitle2" sx={{ mb: 1 }}>Grant types</Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                      OAuth 2.0 grant types this app is allowed to use.
                    </Typography>
                    <FormGroup row>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={allowedGrantTypes.includes('authorization_code')}
                            onChange={(e) =>
                              setAllowedGrantTypes((prev) =>
                                e.target.checked ? [...prev, 'authorization_code'] : prev.filter((x) => x !== 'authorization_code')
                              )
                            }
                          />
                        }
                        label="Authorization Code"
                      />
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={allowedGrantTypes.includes('refresh_token')}
                            onChange={(e) =>
                              setAllowedGrantTypes((prev) =>
                                e.target.checked ? [...prev, 'refresh_token'] : prev.filter((x) => x !== 'refresh_token')
                              )
                            }
                          />
                        }
                        label="Refresh Token"
                      />
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={allowedGrantTypes.includes('client_credentials')}
                            onChange={(e) =>
                              setAllowedGrantTypes((prev) =>
                                e.target.checked ? [...prev, 'client_credentials'] : prev.filter((x) => x !== 'client_credentials')
                              )
                            }
                          />
                        }
                        label="Client Credentials"
                      />
                    </FormGroup>
                  </Box>
                  <Box>
                    <Typography variant="subtitle2" sx={{ mb: 1 }}>Redirect URIs *</Typography>
                    {redirectUris.map((uri, index) => (
                      <Stack direction="row" spacing={1} key={index} sx={{ mb: 1 }}>
                        <TextField fullWidth size="small" value={uri} onChange={(e) => setRedirectUriAt(index, e.target.value)} placeholder="https://yourapp.com/callback" />
                        <IconButton onClick={() => removeRedirectUri(index)} disabled={redirectUris.length <= 1} size="small">
                          <Iconify icon="mdi:minus-circle-outline" width={20} height={20} />
                        </IconButton>
                      </Stack>
                    ))}
                    {redirectUris.length < 10 && (
                      <Button size="small" startIcon={<Iconify icon="mdi:plus" width={16} />} onClick={addRedirectUri} sx={{ textTransform: 'none' }}>
                        Add URI
                      </Button>
                    )}
                  </Box>
                  <TextField label="Privacy policy URL" fullWidth value={privacyPolicyUrl} onChange={(e) => setPrivacyPolicyUrl(e.target.value)} />
                  <TextField label="Terms of service URL" fullWidth value={termsOfServiceUrl} onChange={(e) => setTermsOfServiceUrl(e.target.value)} />
                  <Button variant="contained" onClick={handleSave} disabled={saving} sx={{ textTransform: 'none', alignSelf: 'flex-start' }}>
                    {saving ? <CircularProgress size={20} /> : 'Save changes'}
                  </Button>
                </Stack>
              </Box>
            </>
          )}

          {section === 'permissions' && (
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 600, mb: 1 }}>
                Permissions & scopes
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                Select the scopes (permissions) that this app can request when users authorize it.
              </Typography>
              {scopesByCategory && Object.keys(scopesByCategory).length > 0 ? (
                <Stack spacing={3} sx={{ maxWidth: 640 }}>
                  {Object.entries(scopesByCategory).map(([category, scopes]) => {
                    const scopeList = scopes as Array<{ name: string; description: string }>;
                    const selectedInCategory = scopeList.filter((s) => allowedScopes.includes(s.name)).map((s) => s.name);
                    return (
                      <Box key={category}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1.5, color: theme.palette.text.primary }}>
                          {category}
                        </Typography>
                        <FormControl fullWidth size="small">
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
                                  <Typography variant="caption" color="text.secondary">{s.description}</Typography>
                                </Box>
                              </MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                      </Box>
                    );
                  })}
                  <Button variant="contained" onClick={handleSave} disabled={saving} sx={{ textTransform: 'none', alignSelf: 'flex-start' }}>
                    {saving ? <CircularProgress size={20} /> : 'Save scopes'}
                  </Button>
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">No scopes available.</Typography>
              )}
            </Box>
          )}

          {section === 'advanced' && (
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 600, mb: 2 }}>
                Advanced
              </Typography>
              <Stack spacing={2}>
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>Status</Typography>
                  <Chip size="small" label={app.status} color={app.status === 'active' ? 'success' : 'default'} sx={{ textTransform: 'capitalize', mr: 1 }} />
                  {app.status === 'active' ? (
                    <Button variant="outlined" color="warning" size="small" onClick={handleSuspend} sx={{ textTransform: 'none' }}>
                      Suspend app
                    </Button>
                  ) : (
                    <Button variant="outlined" color="success" size="small" onClick={handleActivate} sx={{ textTransform: 'none' }}>
                      Activate app
                    </Button>
                  )}
                </Box>
              </Stack>
            </Box>
          )}

          {section === 'danger' && (
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 600, mb: 2, color: theme.palette.error.main }}>
                Danger zone
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Revoke all tokens or permanently delete this OAuth app. These actions cannot be undone.
              </Typography>
              <Stack direction="row" spacing={2} flexWrap="wrap">
                <Button variant="outlined" color="warning" onClick={handleRevokeAllTokens} sx={{ textTransform: 'none' }}>
                  Revoke all tokens
                </Button>
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<Iconify icon={deleteForeverIcon} width={18} height={18} />}
                  onClick={() => setDeleteDialogOpen(true)}
                  sx={{ textTransform: 'none' }}
                >
                  Delete OAuth app
                </Button>
              </Stack>
            </Box>
          )}
        </Box>
      </Box>

      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete OAuth app?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will delete &quot;{app.name}&quot; and revoke all tokens. This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)} sx={{ textTransform: 'none' }}>Cancel</Button>
          <Button onClick={handleDelete} color="error" variant="contained" sx={{ textTransform: 'none' }}>
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
        onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert severity={snackbar.severity} onClose={() => setSnackbar((s) => ({ ...s, open: false }))}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
