import cogIcon from '@iconify-icons/mdi/cog';
import gridIcon from '@iconify-icons/mdi/grid';
import lockIcon from '@iconify-icons/mdi/lock';
import refreshIcon from '@iconify-icons/mdi/refresh';
import contentCopyIcon from '@iconify-icons/mdi/content-copy';
import React, { useState, useEffect, useCallback } from 'react';
import deleteForeverIcon from '@iconify-icons/mdi/delete-forever';
import { useParams, useNavigate, useLocation } from 'react-router-dom';

import {
  Box,
  List,
  Chip,
  alpha,
  Stack,
  Alert,
  Paper,
  Button,
  Dialog,
  Divider,
  useTheme,
  Snackbar,
  Checkbox,
  TextField,
  FormGroup,
  Typography,
  IconButton,
  DialogTitle,
  ListItemIcon,
  ListItemText,
  DialogContent,
  DialogActions,
  ListItemButton,
  CircularProgress,
  FormControlLabel,
  DialogContentText,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

import {
  OAuth2Api,
  type OAuth2App,
  type ScopeCategory,
  type UpdateOAuth2AppRequest,
} from './services/oauth2-api';

type DetailSection = 'general' | 'permissions' | 'advanced';

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
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error';
  }>({ open: false, message: '', severity: 'success' });
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
        setAllowedGrantTypes(
          data.allowedGrantTypes?.length
            ? data.allowedGrantTypes
            : ['authorization_code', 'refresh_token']
        );
        setAllowedScopes(data.allowedScopes || []);
        setHomepageUrl(data.homepageUrl || '');
        setPrivacyPolicyUrl(data.privacyPolicyUrl || '');
        setTermsOfServiceUrl(data.termsOfServiceUrl || '');
      })
      .catch(() => {
        setSnackbar({ open: true, message: 'Failed to load application.', severity: 'error' });
        setApp(null);
      })
      .finally(() => setLoading(false));
  }, [appId]);

  useEffect(() => {
    loadApp();
  }, [loadApp]);

  useEffect(() => {
    OAuth2Api.listScopes()
      .then((res) => setScopesByCategory(res.scopes || {}))
      .catch(() => setScopesByCategory({}));
  }, []);

  // Clear regenerated secret when user navigates away (section change or leave page)
  const prevSectionRef = React.useRef(section);
  useEffect(() => {
    if (prevSectionRef.current !== section) {
      prevSectionRef.current = section;
      setNewSecret(null);
    }
  }, [section]);
  useEffect(() => () => setNewSecret(null), [location.pathname]);

  const handleSave = async () => {
    if (!appId || !app) return;
    const uris = redirectUris.map((u) => u.trim()).filter(Boolean);
    if (uris.length === 0) {
      setSnackbar({
        open: true,
        message: 'At least one redirect URI is required',
        severity: 'error',
      });
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
      setSnackbar({
        open: true,
        message: 'Application updated successfully.',
        severity: 'success',
      });
    } catch (err: any) {
      setSnackbar({
        open: true,
        message: err?.response?.data?.message || err?.message || 'Update failed.',
        severity: 'error',
      });
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
      setSnackbar({
        open: true,
        message:
          'A new client secret has been generated. Store it securely; it will not be displayed again.',
        severity: 'success',
      });
    } catch (err: any) {
      setSnackbar({
        open: true,
        message: err?.response?.data?.message || 'Failed to regenerate client secret.',
        severity: 'error',
      });
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
      setSnackbar({
        open: true,
        message: err?.response?.data?.message || 'Failed to suspend',
        severity: 'error',
      });
    }
  };

  const handleActivate = async () => {
    if (!appId) return;
    try {
      const result = await OAuth2Api.activateApp(appId);
      setApp(result.app);
      setSnackbar({ open: true, message: 'App activated', severity: 'success' });
    } catch (err: any) {
      setSnackbar({
        open: true,
        message: err?.response?.data?.message || 'Failed to activate',
        severity: 'error',
      });
    }
  };

  const handleRevokeAllTokens = async () => {
    if (!appId) return;
    try {
      await OAuth2Api.revokeAllTokens(appId);
      setSnackbar({ open: true, message: 'All tokens revoked', severity: 'success' });
    } catch (err: any) {
      setSnackbar({
        open: true,
        message: err?.response?.data?.message || 'Failed to revoke tokens',
        severity: 'error',
      });
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
      setSnackbar({
        open: true,
        message: err?.response?.data?.message || 'Failed to delete',
        severity: 'error',
      });
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
    setSnackbar({ open: true, message: 'Copied to clipboard.', severity: 'success' });
  };

  if (loading && !app) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 320 }}>
        <Stack alignItems="center" spacing={2.5}>
          <CircularProgress size={32} />
          <Typography sx={{ fontSize: '0.875rem', color: 'text.secondary' }}>
            Loading application…
          </Typography>
        </Stack>
      </Box>
    );
  }

  if (!app) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error" sx={{ '& .MuiAlert-message': { fontSize: '0.875rem' } }}>
          Application not found.
        </Alert>
        <Button
          startIcon={<Iconify icon="mdi:arrow-left" />}
          onClick={() => navigate('..')}
          sx={{ mt: 2.5, fontSize: '0.875rem' }}
        >
          Return to OAuth 2.0
        </Button>
      </Box>
    );
  }

  const sidebarBg = isDark
    ? alpha(theme.palette.background.default, 0.4)
    : alpha(theme.palette.grey[50], 0.6);
  const selectedBg = alpha(theme.palette.primary.main, isDark ? 0.2 : 0.1);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header with back button */}
      <Box sx={{ px: 3, py: 2, borderBottom: `1px solid ${theme.palette.divider}` }}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <Button
            startIcon={<Iconify icon="mdi:arrow-left" width={20} height={20} />}
            onClick={() => navigate(oauth2ListPath)}
            sx={{ textTransform: 'none', minWidth: 'auto', px: 1, fontSize: '0.875rem' }}
          >
            Back
          </Button>
          <Typography sx={{ fontWeight: 600, fontSize: '1.25rem' }}>{app.name}</Typography>
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
          <Typography
            sx={{
              px: 2,
              fontWeight: 600,
              fontSize: '0.6875rem',
              color: theme.palette.text.secondary,
              letterSpacing: '0.08em',
            }}
          >
            GENERAL
          </Typography>
          <List disablePadding sx={{ mt: 1 }}>
            <ListItemButton
              selected={section === 'general'}
              onClick={() => setSection('general')}
              sx={{
                py: 1.25,
                '&.Mui-selected': {
                  backgroundColor: selectedBg,
                  borderRight: `3px solid ${theme.palette.primary.main}`,
                },
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
                py: 1.25,
                '&.Mui-selected': {
                  backgroundColor: selectedBg,
                  borderRight: `3px solid ${theme.palette.primary.main}`,
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Iconify icon={lockIcon} width={18} height={18} />
              </ListItemIcon>
              <ListItemText
                primary="Permissions & scopes"
                primaryTypographyProps={{ fontSize: '0.875rem' }}
              />
            </ListItemButton>
            <ListItemButton
              selected={section === 'advanced'}
              onClick={() => setSection('advanced')}
              sx={{
                py: 1.25,
                '&.Mui-selected': {
                  backgroundColor: selectedBg,
                  borderRight: `3px solid ${theme.palette.primary.main}`,
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Iconify icon={cogIcon} width={18} height={18} />
              </ListItemIcon>
              <ListItemText primary="Advanced" primaryTypographyProps={{ fontSize: '0.875rem' }} />
            </ListItemButton>
          </List>
        </Box>

        {/* Main content */}
        <Box sx={{ flex: 1, overflow: 'auto', p: 3 }}>
          {newSecret && (
            <Paper
              variant="outlined"
              sx={{
                mb: 3,
                p: 2,
                bgcolor: alpha(theme.palette.warning.main, 0.08),
                borderColor: theme.palette.warning.main,
              }}
            >
              <Typography sx={{ fontSize: '0.875rem', fontWeight: 500, mb: 1.5 }}>
                Copy and store this client secret securely. It will not be displayed again.
              </Typography>
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <Box
                  component="code"
                  sx={{
                    flex: 1,
                    py: 1.25,
                    px: 2,
                    fontFamily: 'monospace',
                    fontSize: '0.8125rem',
                    bgcolor: theme.palette.background.paper,
                    borderRadius: 1,
                    border: `1px solid ${theme.palette.divider}`,
                    overflow: 'auto',
                  }}
                >
                  {newSecret}
                </Box>
                <IconButton size="small" onClick={() => copyToClipboard(newSecret)} title="Copy">
                  <Iconify icon={contentCopyIcon} width={18} height={18} />
                </IconButton>
                <IconButton size="small" onClick={() => setNewSecret(null)} title="Dismiss">
                  <Iconify icon="mdi:close" width={18} height={18} />
                </IconButton>
              </Stack>
            </Paper>
          )}

          {section === 'general' && (
            <>
              {/* About / Client ID */}
              <Box sx={{ mb: 3 }}>
                <Typography sx={{ fontWeight: 600, fontSize: '1rem', mb: 1 }}>About</Typography>
                <Typography
                  sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary, mb: 1.5 }}
                >
                  Use this Client ID in your OAuth flows.
                </Typography>
                <Paper
                  variant="outlined"
                  sx={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 1,
                    py: 1,
                    px: 2,
                    mt: 0.5,
                    maxWidth: '100%',
                    bgcolor: alpha(theme.palette.primary.main, isDark ? 0.12 : 0.06),
                    borderColor: alpha(theme.palette.primary.main, 0.3),
                  }}
                >
                  <Box
                    component="code"
                    sx={{
                      fontFamily: 'monospace',
                      fontSize: '0.8125rem',
                      overflow: 'auto',
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {app.clientId}
                  </Box>
                  <IconButton
                    size="small"
                    onClick={() => copyToClipboard(app.clientId)}
                    title="Copy Client ID"
                  >
                    <Iconify icon={contentCopyIcon} width={16} height={16} />
                  </IconButton>
                </Paper>
              </Box>

              <Divider sx={{ my: 3 }} />

              {/* Client secrets */}
              <Box sx={{ mb: 3 }}>
                <Typography sx={{ fontWeight: 600, fontSize: '1rem', mb: 1 }}>
                  Client secrets
                </Typography>
                <Typography
                  sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary, mb: 1.5 }}
                >
                  A client secret is required to authenticate this application with the API.
                </Typography>
                <Button
                  variant="outlined"
                  startIcon={
                    regenerating ? (
                      <CircularProgress size={16} />
                    ) : (
                      <Iconify icon={refreshIcon} width={18} height={18} />
                    )
                  }
                  onClick={handleRegenerateSecret}
                  disabled={regenerating}
                  sx={{ textTransform: 'none' }}
                >
                  Generate new client secret
                </Button>
              </Box>

              <Divider sx={{ my: 3 }} />

              {/* Basic information */}
              <Box>
                <Typography sx={{ fontWeight: 600, fontSize: '1rem', mb: 1.5 }}>
                  Basic information
                </Typography>
                <Stack spacing={2} sx={{ maxWidth: 560 }}>
                  <TextField
                    label="OAuth App name"
                    size="small"
                    required
                    fullWidth
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    helperText="The display name of this OAuth application."
                    sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
                  />
                  <TextField
                    label="Description"
                    fullWidth
                    multiline
                    rows={3}
                    size="small"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Displayed to users when they authorize this application."
                    sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
                  />
                  <TextField
                    label="Homepage URL"
                    fullWidth
                    size="small"
                    value={homepageUrl}
                    onChange={(e) => setHomepageUrl(e.target.value)}
                    placeholder="https://yourapp.com"
                    sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
                  />
                  <Box>
                    <Typography sx={{ fontSize: '0.875rem', fontWeight: 500, mb: 1 }}>
                      Grant types
                    </Typography>
                    <Typography
                      sx={{
                        fontSize: '0.75rem',
                        color: theme.palette.text.secondary,
                        display: 'block',
                        mb: 1.5,
                      }}
                    >
                      OAuth 2.0 grant types permitted for this application.
                    </Typography>
                    <FormGroup row>
                      <FormControlLabel
                        control={
                          <Checkbox
                            checked={allowedGrantTypes.includes('authorization_code')}
                            onChange={(e) =>
                              setAllowedGrantTypes((prev) =>
                                e.target.checked
                                  ? [...prev, 'authorization_code']
                                  : prev.filter((x) => x !== 'authorization_code')
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
                                e.target.checked
                                  ? [...prev, 'refresh_token']
                                  : prev.filter((x) => x !== 'refresh_token')
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
                                e.target.checked
                                  ? [...prev, 'client_credentials']
                                  : prev.filter((x) => x !== 'client_credentials')
                              )
                            }
                          />
                        }
                        label="Client Credentials"
                      />
                    </FormGroup>
                  </Box>
                  <Box>
                    <Typography sx={{ fontSize: '0.875rem', fontWeight: 500, mb: 1 }}>
                      Redirect URIs *
                    </Typography>
                    {redirectUris.map((uri, index) => (
                      <Stack direction="row" spacing={1.5} key={index} sx={{ mb: 1.5 }}>
                        <TextField
                          fullWidth
                          size="small"
                          value={uri}
                          onChange={(e) => setRedirectUriAt(index, e.target.value)}
                          placeholder="https://yourapp.com/callback"
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
                        startIcon={<Iconify icon="mdi:plus" width={16} />}
                        onClick={addRedirectUri}
                        sx={{ textTransform: 'none' }}
                      >
                        Add redirect URI
                      </Button>
                    )}
                  </Box>
                  <TextField
                    label="Privacy policy URL"
                    fullWidth
                    size="small"
                    value={privacyPolicyUrl}
                    onChange={(e) => setPrivacyPolicyUrl(e.target.value)}
                    sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
                  />
                  <TextField
                    label="Terms of service URL"
                    fullWidth
                    size="small"
                    value={termsOfServiceUrl}
                    onChange={(e) => setTermsOfServiceUrl(e.target.value)}
                    sx={{ '& .MuiInputBase-input': { fontSize: '0.875rem' } }}
                  />
                  <Button
                    variant="contained"
                    onClick={handleSave}
                    disabled={saving}
                    sx={{
                      textTransform: 'none',
                      alignSelf: 'flex-start',
                      fontSize: '0.875rem',
                      py: 1,
                      px: 2,
                    }}
                  >
                    {saving ? <CircularProgress size={20} /> : 'Save changes'}
                  </Button>
                </Stack>
              </Box>
            </>
          )}

          {section === 'permissions' && (
            <Box>
              <Typography sx={{ fontWeight: 600, fontSize: '1rem', mb: 1.5 }}>
                Permissions & scopes
              </Typography>
              <Typography sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary, mb: 2 }}>
                Select the scopes (permissions) that this application may request during user
                authorization.
              </Typography>
              {scopesByCategory && Object.keys(scopesByCategory).length > 0 ? (
                <Stack spacing={2.5} sx={{ maxWidth: 640 }}>
                  {(() => {
                    const entries = Object.entries(scopesByCategory) as [
                      string,
                      Array<{ name: string; description: string }>,
                    ][];
                    const allScopeNames = entries.flatMap(([, scopes]) =>
                      scopes.map((s) => s.name)
                    );
                    const allSelected =
                      allScopeNames.length > 0 &&
                      allScopeNames.every((scopeName) => allowedScopes.includes(scopeName));
                    const someSelected = allScopeNames.some((scopeName) =>
                      allowedScopes.includes(scopeName)
                    );
                    return (
                      <>
                        <FormGroup>
                          <FormControlLabel
                            control={
                              <Checkbox
                                checked={allSelected}
                                indeterminate={someSelected && !allSelected}
                                onChange={(e) => {
                                  if (e.target.checked) {
                                    setAllowedScopes([...allScopeNames]);
                                  } else {
                                    setAllowedScopes([]);
                                  }
                                }}
                              />
                            }
                            label={
                              <Typography sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                                Select all scopes
                              </Typography>
                            }
                          />
                        </FormGroup>
                        <Divider sx={{ my: 1.5 }} />
                        {entries.map(([category, scopeList]) => {
                          const namesInCategory = scopeList.map((s) => s.name);
                          const selectedInCategory = namesInCategory.filter((n) =>
                            allowedScopes.includes(n)
                          );
                          const categoryAllChecked =
                            namesInCategory.length > 0 &&
                            selectedInCategory.length === namesInCategory.length;
                          const categorySomeChecked = selectedInCategory.length > 0;
                          return (
                            <Box key={category} sx={{ mb: 1.5 }}>
                              <FormGroup sx={{ mb: 0.75 }}>
                                <FormControlLabel
                                  control={
                                    <Checkbox
                                      checked={categoryAllChecked}
                                      indeterminate={categorySomeChecked && !categoryAllChecked}
                                      onChange={(e) => {
                                        if (e.target.checked) {
                                          setAllowedScopes((prev) => [
                                            ...prev.filter((x) => !namesInCategory.includes(x)),
                                            ...namesInCategory,
                                          ]);
                                        } else {
                                          setAllowedScopes((prev) =>
                                            prev.filter((x) => !namesInCategory.includes(x))
                                          );
                                        }
                                      }}
                                    />
                                  }
                                  label={
                                    <Typography
                                      sx={{
                                        fontWeight: 600,
                                        fontSize: '0.875rem',
                                        color: theme.palette.text.primary,
                                      }}
                                    >
                                      {category}
                                    </Typography>
                                  }
                                />
                              </FormGroup>
                              <FormGroup sx={{ pl: 3.5, mt: 0 }}>
                                {scopeList.map((s) => (
                                  <FormControlLabel
                                    key={s.name}
                                    control={
                                      <Checkbox
                                        checked={allowedScopes.includes(s.name)}
                                        onChange={(e) => {
                                          if (e.target.checked) {
                                            setAllowedScopes((prev) => [...prev, s.name]);
                                          } else {
                                            setAllowedScopes((prev) =>
                                              prev.filter((x) => x !== s.name)
                                            );
                                          }
                                        }}
                                      />
                                    }
                                    label={
                                      <Box>
                                        <Typography
                                          component="span"
                                          sx={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}
                                        >
                                          {s.name}
                                        </Typography>
                                        {s.description && (
                                          <Typography
                                            component="span"
                                            sx={{
                                              display: 'block',
                                              fontSize: '0.75rem',
                                              color: theme.palette.text.secondary,
                                            }}
                                          >
                                            {s.description}
                                          </Typography>
                                        )}
                                      </Box>
                                    }
                                  />
                                ))}
                              </FormGroup>
                            </Box>
                          );
                        })}
                        <Button
                          variant="contained"
                          onClick={handleSave}
                          disabled={saving}
                          sx={{
                            textTransform: 'none',
                            alignSelf: 'flex-start',
                            fontSize: '0.875rem',
                            py: 1,
                            px: 2,
                          }}
                        >
                          {saving ? <CircularProgress size={20} /> : 'Save scopes'}
                        </Button>
                      </>
                    );
                  })()}
                </Stack>
              ) : (
                <Typography sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary }}>
                  No scopes are available.
                </Typography>
              )}
            </Box>
          )}

          {section === 'advanced' && (
            <Box>
              <Typography sx={{ fontWeight: 600, fontSize: '1rem', mb: 1.5 }}>Advanced</Typography>
              <Stack spacing={2.5}>
                <Box>
                  <Typography sx={{ fontSize: '0.875rem', fontWeight: 500, mb: 1.5 }}>
                    Status
                  </Typography>
                  <Chip
                    size="small"
                    label={app.status}
                    color={app.status === 'active' ? 'success' : 'default'}
                    sx={{ textTransform: 'capitalize', mr: 1 }}
                  />
                  {app.status === 'active' ? (
                    <Button
                      variant="outlined"
                      color="warning"
                      size="small"
                      onClick={handleSuspend}
                      sx={{ textTransform: 'none' }}
                    >
                      Suspend application
                    </Button>
                  ) : (
                    <Button
                      variant="outlined"
                      color="success"
                      size="small"
                      onClick={handleActivate}
                      sx={{ textTransform: 'none' }}
                    >
                      Activate application
                    </Button>
                  )}
                </Box>

                <Divider />

                <Box>
                  <Typography
                    sx={{
                      fontWeight: 600,
                      fontSize: '1rem',
                      mb: 1,
                      color: theme.palette.error.main,
                    }}
                  >
                    Danger Zone
                  </Typography>
                  <Typography
                    sx={{ fontSize: '0.875rem', color: theme.palette.text.secondary, mb: 1.5 }}
                  >
                    Revoke all tokens or permanently delete this OAuth application. These actions
                    cannot be undone.
                  </Typography>
                  <Stack direction="row" spacing={2} flexWrap="wrap" gap={1}>
                    <Button
                      variant="outlined"
                      color="warning"
                      onClick={handleRevokeAllTokens}
                      sx={{ textTransform: 'none' }}
                    >
                      Revoke all tokens
                    </Button>
                    <Button
                      variant="outlined"
                      color="error"
                      startIcon={<Iconify icon={deleteForeverIcon} width={18} height={18} />}
                      onClick={() => setDeleteDialogOpen(true)}
                      sx={{ textTransform: 'none' }}
                    >
                      Delete application
                    </Button>
                  </Stack>
                </Box>
              </Stack>
            </Box>
          )}
        </Box>
      </Box>

      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Delete OAuth application?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This will delete &quot;{app.name}&quot; and revoke all tokens. This action cannot be
            undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)} sx={{ textTransform: 'none' }}>
            Cancel
          </Button>
          <Button
            onClick={handleDelete}
            color="error"
            variant="contained"
            sx={{ textTransform: 'none' }}
          >
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
        <Alert
          severity={snackbar.severity}
          onClose={() => setSnackbar((s) => ({ ...s, open: false }))}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
