import React, { useMemo } from 'react';
import {
  Paper,
  Box,
  Typography,
  Alert,
  Link,
  Grid,
  CircularProgress,
  alpha,
  useTheme,
  Collapse,
  IconButton,
  Tooltip,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormHelperText,
  Fade,
  Chip,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import infoIcon from '@iconify-icons/eva/info-outline';
import bookIcon from '@iconify-icons/mdi/book-outline';
import settingsIcon from '@iconify-icons/mdi/settings';
import keyIcon from '@iconify-icons/mdi/key';
import personIcon from '@iconify-icons/mdi/person';
import shieldIcon from '@iconify-icons/mdi/shield-outline';
import codeIcon from '@iconify-icons/mdi/code';
import descriptionIcon from '@iconify-icons/mdi/file-document-outline';
import openInNewIcon from '@iconify-icons/mdi/open-in-new';
import copyIcon from '@iconify-icons/mdi/content-copy';
import checkIcon from '@iconify-icons/mdi/check';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
import shieldCheckIcon from '@iconify-icons/mdi/shield-check';
import addCircleIcon from '@iconify-icons/mdi/plus-circle';
import { useAdmin } from 'src/context/AdminContext';
import { FieldRenderer } from '../field-renderers';
import { shouldShowElement } from '../../utils/conditional-display';
import BusinessOAuthSection from './business-oauth-section';
import SharePointOAuthSection from './sharepoint-oauth-section';
import { Connector, ConnectorConfig } from '../../types/types';
import { ConnectorApiService } from '../../services/api';

interface AuthSectionProps {
  connector: Connector;
  connectorConfig: ConnectorConfig | null;
  formData: Record<string, any>;
  formErrors: Record<string, string>;
  conditionalDisplay: Record<string, boolean>;
  accountTypeLoading: boolean;
  isBusiness: boolean;
  adminEmail: string;
  adminEmailError: string | null;
  selectedFile: File | null;
  fileName: string | null;
  fileError: string | null;
  jsonData: Record<string, any> | null;
  onAdminEmailChange: (email: string) => void;
  onFileUpload: () => void;
  onFileChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
  certificateFile: File | null;
  certificateFileName: string | null;
  certificateError: string | null;
  certificateData: Record<string, any> | null;
  privateKeyFile: File | null;
  privateKeyFileName: string | null;
  privateKeyError: string | null;
  privateKeyData: string | null;
  onCertificateUpload: () => void;
  onCertificateChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onPrivateKeyUpload: () => void;
  onPrivateKeyChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  certificateInputRef: React.RefObject<HTMLInputElement>;
  privateKeyInputRef: React.RefObject<HTMLInputElement>;
  onFieldChange: (section: string, fieldName: string, value: any) => void;
  saveAttempted?: boolean;
  // Create-mode connector instance naming
  isCreateMode: boolean;
  instanceName: string;
  instanceNameError: string | null;
  onInstanceNameChange: (value: string) => void;

  // Auth type selection (create mode only)
  selectedAuthType: string | null;
  handleAuthTypeChange: (authType: string) => void;
}

const AuthSection: React.FC<AuthSectionProps> = ({
  connector,
  connectorConfig,
  formData,
  formErrors,
  conditionalDisplay,
  accountTypeLoading,
  isBusiness,
  adminEmail,
  adminEmailError,
  selectedFile,
  fileName,
  fileError,
  jsonData,
  onAdminEmailChange,
  onFileUpload,
  onFileChange,
  fileInputRef,
  certificateFile,
  certificateFileName,
  certificateError,
  certificateData,
  privateKeyFile,
  privateKeyFileName,
  privateKeyError,
  privateKeyData,
  onCertificateUpload,
  onCertificateChange,
  onPrivateKeyUpload,
  onPrivateKeyChange,
  certificateInputRef,
  privateKeyInputRef,
  onFieldChange,
  isCreateMode,
  instanceName,
  instanceNameError,
  onInstanceNameChange,
  selectedAuthType,
  handleAuthTypeChange,
  saveAttempted = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { isAdmin } = useAdmin();
  const [copied, setCopied] = React.useState(false);
  const [showDocs, setShowDocs] = React.useState(false);
  const [showRedirectUri, setShowRedirectUri] = React.useState(true);
  const [oauthApps, setOAuthApps] = React.useState<any[]>([]);
  const [loadingOAuthApps, setLoadingOAuthApps] = React.useState(false);
  const [selectedOAuthConfigId, setSelectedOAuthConfigId] = React.useState<string | null>(null);
  const [selectedOAuthApp, setSelectedOAuthApp] = React.useState<any>(null);
  const [newOAuthAppName, setNewOAuthAppName] = React.useState<string>('');
  const [loadingOAuthConfig, setLoadingOAuthConfig] = React.useState(false);

  // Memoize auth schemas to prevent dependency issues
  const authSchemas = useMemo(() => {
    if (!connectorConfig) return {};
    return (connectorConfig.config.auth as any).schemas || {};
  }, [connectorConfig]);

  // Get current auth type and its schema - memoized to prevent unnecessary recalculations
  const currentAuthType = useMemo(() => {
    if (!connectorConfig) return '';
    const auth = connectorConfig.config.auth;
    return isCreateMode
      ? selectedAuthType || (auth as any).supportedAuthTypes?.[0] || ''
      : auth.type || '';
  }, [isCreateMode, selectedAuthType, connectorConfig]);

  // Check if OAuth type is selected
  const isOAuthSelected = useMemo(
    () => currentAuthType === 'OAUTH',
    [currentAuthType]
  );

  // Initialize selected OAuth app from formData (both create and edit modes)
  React.useEffect(() => {
    if (isOAuthSelected && formData.oauthConfigId && !selectedOAuthConfigId) {
      setSelectedOAuthConfigId(formData.oauthConfigId);
    }
  }, [isOAuthSelected, formData.oauthConfigId, selectedOAuthConfigId]);

  // Fetch OAuth apps when OAuth is selected (all users can see the list, both create and edit modes)
  // Performance optimization: Backend automatically sends full config for admins (no second API call needed)
  React.useEffect(() => {
    if (isOAuthSelected && connector.type) {
      setLoadingOAuthApps(true);
      // Backend automatically determines what data to return based on authentication headers:
      // - Admins: Full config with credentials (performance optimization)
      // - Non-admins: Only essential fields (credentials excluded for security)
      ConnectorApiService.listOAuthConfigs(connector.type, 1, 100)
        .then((result) => {
          const apps = result.oauthConfigs || [];
          setOAuthApps(apps);

          // If no OAuth configs exist and we're in create mode, auto-populate instance name
          // and ensure we're in "create new OAuth app" mode
          if (
            apps.length === 0 &&
            isCreateMode &&
            instanceName &&
            !newOAuthAppName &&
            !selectedOAuthConfigId
          ) {
            setNewOAuthAppName(instanceName);
            onFieldChange('auth', 'oauthInstanceName', instanceName);
          }
        })
        .catch((error) => {
          console.error('Error fetching OAuth apps:', error);
        })
        .finally(() => {
          setLoadingOAuthApps(false);
        });
    } else {
      setOAuthApps([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOAuthSelected, connector.type]); // Only depend on OAuth selection and connector type - NOT instanceName or other fields

  // Sync oauthInstanceName with instanceName when instanceName changes and no OAuth configs exist
  // This is separate from the API fetch to avoid unnecessary API calls
  React.useEffect(() => {
    if (
      isOAuthSelected &&
      isCreateMode &&
      oauthApps.length === 0 &&
      instanceName &&
      !selectedOAuthConfigId
    ) {
      // Only update if user hasn't manually changed it or if it's empty
      if (!newOAuthAppName || newOAuthAppName === instanceName) {
        setNewOAuthAppName(instanceName);
        onFieldChange('auth', 'oauthInstanceName', instanceName);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    instanceName,
    isOAuthSelected,
    isCreateMode,
    oauthApps.length,
    selectedOAuthConfigId,
    // Exclude newOAuthAppName and onFieldChange to avoid unnecessary updates
  ]);

  // Get schema for current auth type - ensure we have a valid auth type
  const currentAuthSchema = useMemo(() => {
    if (currentAuthType && authSchemas[currentAuthType]) {
      return authSchemas[currentAuthType];
    }
    if (currentAuthType && Object.keys(authSchemas).length > 0) {
      // If auth type doesn't match, try to find any schema (fallback)
      const firstSchemaKey = Object.keys(authSchemas)[0];
      return authSchemas[firstSchemaKey];
    }
    return { fields: [] };
  }, [currentAuthType, authSchemas]);

  // Get OAuth field names from current auth schema (dynamic, no hardcoding)
  const oauthFieldNames = useMemo(() => {
    if (!isOAuthSelected || !currentAuthSchema.fields) {
      return [];
    }
    // Extract field names from the current auth schema
    return (currentAuthSchema.fields || []).map((field: any) => field.name);
  }, [isOAuthSelected, currentAuthSchema]);

  // Load OAuth config when selected and populate form fields (for admins)
  // Performance optimization: Use cached data from oauthApps list (already includes full config for admins)
  React.useEffect(() => {
    if (selectedOAuthConfigId && connector.type) {
      setLoadingOAuthConfig(true);
      
      // Find the selected OAuth config in the already-fetched list
      const app = oauthApps.find((a) => a._id === selectedOAuthConfigId);
      
      if (app) {
        setSelectedOAuthApp(app);

        // For admins: populate form fields with OAuth config values (if full config is available)
        // The list API now includes full config details when include_full_config=true
        if (isAdmin && app.config && typeof app.config === 'object' && oauthFieldNames.length > 0) {
          oauthFieldNames.forEach((fieldName: string) => {
            const value = app.config[fieldName];
            // Populate field if value exists (including empty strings)
            if (value !== null && value !== undefined) {
              onFieldChange('auth', fieldName, value);
            }
          });
        }
        
        setLoadingOAuthConfig(false);
      } else if (isAdmin) {
        // Fallback: Only make API call if config not found in cached list (shouldn't happen normally)
        // This maintains backward compatibility while providing performance improvement
        console.warn(`OAuth config ${selectedOAuthConfigId} not found in cached list, fetching...`);
        ConnectorApiService.getOAuthConfig(connector.type, selectedOAuthConfigId)
          .then((oauthConfig) => {
            if (oauthConfig) {
              setSelectedOAuthApp(oauthConfig);

              if (
                oauthConfig.config &&
                typeof oauthConfig.config === 'object' &&
                oauthFieldNames.length > 0
              ) {
                oauthFieldNames.forEach((fieldName: string) => {
                  const value = oauthConfig.config[fieldName];
                  if (value !== null && value !== undefined) {
                    onFieldChange('auth', fieldName, value);
                  }
                });
              }
            }
          })
          .catch((error) => {
            console.error('Error fetching OAuth config:', error);
          })
          .finally(() => {
            setLoadingOAuthConfig(false);
          });
      } else {
        // Non-admin user - config not found in list
        setLoadingOAuthConfig(false);
      }
      
      // Store only OAuth app ID reference in auth config (not credential fields)
      onFieldChange('auth', 'oauthConfigId', selectedOAuthConfigId);
    } else if (!selectedOAuthConfigId) {
      setSelectedOAuthApp(null);
      setLoadingOAuthConfig(false);
      // Clear OAuth app ID reference
      onFieldChange('auth', 'oauthConfigId', null);
      // Clear OAuth credential fields when no app is selected (for admins)
      if (isAdmin && oauthFieldNames.length > 0) {
        oauthFieldNames.forEach((fieldName: string) => {
          onFieldChange('auth', fieldName, '');
        });
      }
    }
  }, [isAdmin, selectedOAuthConfigId, connector.type, oauthApps, onFieldChange, oauthFieldNames]);

  // Handle OAuth app selection (all users)
  const handleOAuthAppChange = (oauthConfigId: string | null) => {
    setSelectedOAuthConfigId(oauthConfigId);
    if (!oauthConfigId) {
      // Clear OAuth config ID reference (user will enter credentials manually, which will create new OAuth config)
      onFieldChange('auth', 'oauthConfigId', null);
      // Clear OAuth credential fields when no app is selected (user can enter manually)
      if (isAdmin && oauthFieldNames.length > 0) {
        oauthFieldNames.forEach((fieldName: string) => {
          onFieldChange('auth', fieldName, '');
        });
      }
      // Clear instance name state and form data when switching to manual entry
      setNewOAuthAppName('');
      onFieldChange('auth', 'oauthInstanceName', '');
    } else {
      // Clear instance name state and form data when selecting existing app
      setNewOAuthAppName('');
      onFieldChange('auth', 'oauthInstanceName', '');
    }
  };

  if (!connectorConfig) return null;
  const { auth } = connectorConfig.config;
  let { documentationLinks } = connectorConfig.config;

  const customGoogleBusinessOAuth = (connectorParam: Connector, accountType: string): boolean =>
    accountType === 'business' &&
    connectorParam.appGroup === 'Google Workspace' &&
    connectorParam.authType === 'OAUTH' &&
    connectorParam.scope === 'team';

  const isSharePointCertificateAuth = (connectorParam: Connector): boolean =>
    connectorParam.type === 'SharePoint Online' &&
    (connectorParam.authType === 'OAUTH_CERTIFICATE' ||
      connectorParam.authType === 'OAUTH_ADMIN_CONSENT');

  const pipeshubDocumentationUrl =
    documentationLinks?.find((link) => link.type === 'pipeshub')?.url ||
    `https://docs.pipeshub.com/connectors/overview`;

  documentationLinks = documentationLinks?.filter((link) => link.type !== 'pipeshub');

  // Get redirect URI from current auth type's schema
  // Only for OAuth-based auth types (OAUTH only, not OAUTH_ADMIN_CONSENT)
  const isOAuthType = currentAuthType === 'OAUTH';
  const redirectUriValue = isOAuthType ? (currentAuthSchema as any).redirectUri || '' : '';
  const redirectUri = redirectUriValue ? `${window.location.origin}/${redirectUriValue}` : '';
  const displayRedirectUri =
    isOAuthType && (currentAuthSchema as any).displayRedirectUri !== undefined
      ? (currentAuthSchema as any).displayRedirectUri
      : false;

  // Only show redirect URI for OAuth types and if it exists in the schema
  const shouldShowRedirectUri =
    isOAuthType &&
    redirectUriValue !== '' &&
    (displayRedirectUri ||
      (auth.conditionalDisplay &&
        Object.keys(auth.conditionalDisplay).length > 0 &&
        shouldShowElement(auth.conditionalDisplay, 'redirectUri', formData)));

  const handleCopy = () => {
    navigator.clipboard.writeText(redirectUri);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {/* Compact Documentation Alert */}
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

      {/* Collapsible Redirect URI */}
      {shouldShowRedirectUri && (
        <Paper
          variant="outlined"
          sx={{
            borderRadius: 1.25,
            overflow: 'hidden',
            bgcolor: isDark
              ? alpha(theme.palette.primary.main, 0.08)
              : alpha(theme.palette.primary.main, 0.03),
            borderColor: isDark
              ? alpha(theme.palette.primary.main, 0.25)
              : alpha(theme.palette.primary.main, 0.15),
            boxShadow: isDark
              ? `0 1px 3px ${alpha(theme.palette.primary.main, 0.15)}`
              : `0 1px 3px ${alpha(theme.palette.primary.main, 0.05)}`,
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
              '&:hover': {
                bgcolor: alpha(theme.palette.primary.main, 0.05),
              },
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
              <Typography
                variant="subtitle2"
                sx={{
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  color: theme.palette.primary.main,
                }}
              >
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
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ fontSize: '0.8125rem', mb: 1.25, lineHeight: 1.5 }}
              >
                {connector.name === 'OneDrive'
                  ? 'Use this URL when configuring your Azure AD App registration.'
                  : `Use this URL when configuring your ${connector.name} OAuth2 App.`}
              </Typography>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  p: 1.25,
                  borderRadius: 1,
                  bgcolor: isDark
                    ? alpha(theme.palette.grey[900], 0.4)
                    : alpha(theme.palette.grey[100], 0.8),
                  border: `1.5px solid ${alpha(theme.palette.primary.main, isDark ? 0.25 : 0.15)}`,
                  transition: 'all 0.2s',
                  '&:hover': {
                    borderColor: alpha(theme.palette.primary.main, isDark ? 0.4 : 0.3),
                    bgcolor: isDark
                      ? alpha(theme.palette.grey[900], 0.6)
                      : alpha(theme.palette.grey[100], 1),
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
                    color:
                      theme.palette.mode === 'dark'
                        ? theme.palette.primary.light
                        : theme.palette.primary.dark,
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
                    onClick={handleCopy}
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
                    <Iconify
                      icon={copied ? checkIcon : copyIcon}
                      width={16}
                      color={theme.palette.primary.main}
                    />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>
          </Collapse>
        </Paper>
      )}

      {/* Collapsible Documentation Links */}
      {documentationLinks && documentationLinks.length > 0 && (
        <Paper
          variant="outlined"
          sx={{
            borderRadius: 1.25,
            overflow: 'hidden',
            bgcolor: isDark
              ? alpha(theme.palette.info.main, 0.08)
              : alpha(theme.palette.info.main, 0.025),
            borderColor: isDark
              ? alpha(theme.palette.info.main, 0.25)
              : alpha(theme.palette.info.main, 0.12),
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
              '&:hover': { bgcolor: alpha(theme.palette.info.main, 0.04) },
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
              <Typography
                variant="subtitle2"
                sx={{
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  color: theme.palette.info.main,
                }}
              >
                Setup Documentation
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  px: 1,
                  py: 0.375,
                  borderRadius: 0.75,
                  bgcolor: alpha(theme.palette.info.main, 0.12),
                  color: theme.palette.info.main,
                  fontSize: '0.75rem',
                  fontWeight: 600,
                }}
              >
                {documentationLinks.length}
              </Typography>
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
              {documentationLinks.map((link, index) => (
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
                    bgcolor: isDark
                      ? alpha(theme.palette.background.paper, 0.5)
                      : theme.palette.background.paper,
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                    '&:hover': {
                      borderColor: alpha(theme.palette.info.main, isDark ? 0.4 : 0.25),
                      bgcolor: isDark
                        ? alpha(theme.palette.info.main, 0.12)
                        : alpha(theme.palette.info.main, 0.03),
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
                        bgcolor: alpha(theme.palette.info.main, 0.08),
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Iconify
                        icon={
                          link.type === 'setup'
                            ? settingsIcon
                            : link.type === 'api'
                              ? codeIcon
                              : descriptionIcon
                        }
                        width={14}
                        color={theme.palette.info.main}
                      />
                    </Box>
                    <Typography
                      variant="body2"
                      sx={{
                        fontSize: '0.8125rem',
                        fontWeight: 500,
                        color: theme.palette.text.primary,
                      }}
                    >
                      {link.title}
                    </Typography>
                  </Box>
                  <Iconify
                    icon={openInNewIcon}
                    width={14}
                    color={theme.palette.text.secondary}
                    sx={{ opacity: 0.6 }}
                  />
                </Box>
              ))}
            </Box>
          </Collapse>
        </Paper>
      )}

      {/* Account Type Loading */}
      {accountTypeLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={20} />
        </Box>
      )}

      {/* Business OAuth Section (Google Workspace) */}
      {!accountTypeLoading &&
        customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') && (
          <BusinessOAuthSection
            adminEmail={adminEmail}
            adminEmailError={adminEmailError}
            selectedFile={selectedFile}
            fileName={fileName}
            fileError={fileError}
            jsonData={jsonData}
            onAdminEmailChange={onAdminEmailChange}
            onFileUpload={onFileUpload}
            onFileChange={onFileChange}
            fileInputRef={fileInputRef}
            isCreateMode={isCreateMode}
            instanceName={instanceName}
            instanceNameError={instanceNameError}
            onInstanceNameChange={onInstanceNameChange}
            connectorName={connector.name}
          />
        )}

      {/* SharePoint Certificate OAuth Section */}
      {!accountTypeLoading && isSharePointCertificateAuth(connector) && (
        <SharePointOAuthSection
          clientId={formData.clientId || ''}
          tenantId={formData.tenantId || ''}
          sharepointDomain={formData.sharepointDomain || ''}
          hasAdminConsent={formData.hasAdminConsent || false}
          clientIdError={formErrors.clientId || null}
          tenantIdError={formErrors.tenantId || null}
          sharepointDomainError={formErrors.sharepointDomain || null}
          hasAdminConsentError={formErrors.hasAdminConsent || null}
          certificateFile={certificateFile}
          certificateFileName={certificateFileName}
          certificateError={formErrors.certificate || certificateError || null}
          certificateData={certificateData}
          privateKeyFile={privateKeyFile}
          privateKeyFileName={privateKeyFileName}
          privateKeyError={formErrors.privateKey || privateKeyError || null}
          privateKeyData={privateKeyData}
          onClientIdChange={(value) => onFieldChange('auth', 'clientId', value)}
          onTenantIdChange={(value) => onFieldChange('auth', 'tenantId', value)}
          onSharePointDomainChange={(value) => onFieldChange('auth', 'sharepointDomain', value)}
          onAdminConsentChange={(value) => onFieldChange('auth', 'hasAdminConsent', value)}
          onCertificateUpload={onCertificateUpload}
          onCertificateChange={onCertificateChange}
          onPrivateKeyUpload={onPrivateKeyUpload}
          onPrivateKeyChange={onPrivateKeyChange}
          certificateInputRef={certificateInputRef}
          privateKeyInputRef={privateKeyInputRef}
          isCreateMode={isCreateMode}
          instanceName={instanceName}
          instanceNameError={instanceNameError}
          onInstanceNameChange={onInstanceNameChange}
          connectorName={connector.name}
          showValidationSummary={saveAttempted}
        />
      )}

      {/* Form Fields - More Compact */}
      {!accountTypeLoading &&
        !customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') &&
        !isSharePointCertificateAuth(connector) && (
          <Paper
            variant="outlined"
            sx={{
              p: 2,
              borderRadius: 1.25,
              bgcolor: isDark
                ? alpha(theme.palette.background.paper, 0.4)
                : theme.palette.background.paper,
              borderColor: isDark
                ? alpha(theme.palette.divider, 0.12)
                : alpha(theme.palette.divider, 0.1),
              boxShadow: isDark
                ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
                : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
            }}
          >
            <Box
              sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, flex: 1 }}>
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
                  <Iconify
                    icon={
                      currentAuthType === 'OAUTH'
                        ? shieldIcon
                        : currentAuthType === 'API_TOKEN'
                          ? keyIcon
                          : currentAuthType === 'USERNAME_PASSWORD'
                            ? personIcon
                            : settingsIcon
                    }
                    width={16}
                    color={theme.palette.text.secondary}
                  />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontWeight: 600,
                      fontSize: '0.875rem',
                      color: theme.palette.text.primary,
                      lineHeight: 1.4,
                    }}
                  >
                    {currentAuthType === 'OAUTH'
                      ? 'OAuth2 Credentials'
                      : currentAuthType === 'API_TOKEN'
                        ? 'API Credentials'
                        : currentAuthType === 'USERNAME_PASSWORD'
                          ? 'Login Credentials'
                          : 'Authentication'}
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      fontSize: '0.75rem',
                      color: theme.palette.text.secondary,
                      lineHeight: 1.3,
                    }}
                  >
                    Enter your {connector.name} authentication details
                  </Typography>
                </Box>
              </Box>

              {/* Auth Type Selector (Create Mode Only) - Top Right */}
              {isCreateMode &&
                (auth as any).supportedAuthTypes &&
                (auth as any).supportedAuthTypes.length > 1 && (
                  <Box
                    sx={{
                      ml: 2,
                      minWidth: 220,
                      maxWidth: 280,
                      transition: 'all 0.3s ease-in-out',
                    }}
                  >
                    <FormControl
                      fullWidth
                      size="small"
                      sx={{
                        '& .MuiInputLabel-root': {
                          fontWeight: 600,
                          color: isDark
                            ? alpha(theme.palette.text.primary, 0.95)
                            : alpha(theme.palette.text.primary, 0.85),
                          fontSize: '0.875rem',
                          mb: 0.5,
                        },
                        '& .MuiOutlinedInput-root': {
                          transition: 'all 0.2s ease-in-out',
                          bgcolor: isDark
                            ? alpha(theme.palette.background.paper, 0.6)
                            : theme.palette.background.paper,
                          fontSize: '0.875rem',
                          fontWeight: 500,
                          '&:hover': {
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderColor: isDark
                                ? alpha(theme.palette.primary.main, 0.6)
                                : alpha(theme.palette.primary.main, 0.5),
                              borderWidth: 2,
                            },
                            bgcolor: isDark
                              ? alpha(theme.palette.background.paper, 0.8)
                              : alpha(theme.palette.background.paper, 0.9),
                          },
                          '&.Mui-focused': {
                            '& .MuiOutlinedInput-notchedOutline': {
                              borderWidth: 2,
                              borderColor: theme.palette.primary.main,
                            },
                            bgcolor: isDark
                              ? alpha(theme.palette.background.paper, 0.9)
                              : theme.palette.background.paper,
                            boxShadow: isDark
                              ? `0 0 0 3px ${alpha(theme.palette.primary.main, 0.2)}`
                              : `0 0 0 3px ${alpha(theme.palette.primary.main, 0.1)}`,
                          },
                        },
                        '& .MuiOutlinedInput-notchedOutline': {
                          borderColor: isDark
                            ? alpha(theme.palette.primary.main, 0.4)
                            : alpha(theme.palette.primary.main, 0.3),
                          borderWidth: 1.5,
                          transition: 'all 0.2s ease-in-out',
                        },
                      }}
                    >
                      <InputLabel id="auth-type-select-label" shrink>
                        Authentication Type
                      </InputLabel>
                      <Select
                        labelId="auth-type-select-label"
                        id="auth-type-select"
                        value={selectedAuthType || (auth as any).supportedAuthTypes[0] || ''}
                        label="Authentication Type"
                        onChange={(e) => handleAuthTypeChange(e.target.value)}
                        MenuProps={{
                          PaperProps: {
                            sx: {
                              mt: 1,
                              borderRadius: 2,
                              boxShadow: isDark
                                ? '0 8px 24px rgba(0, 0, 0, 0.4)'
                                : '0 8px 24px rgba(0, 0, 0, 0.12)',
                              '& .MuiMenuItem-root': {
                                borderRadius: 1,
                                mx: 0.5,
                                my: 0.25,
                                px: 2,
                                py: 1.25,
                                transition: 'all 0.15s ease-in-out',
                                '&:hover': {
                                  bgcolor: isDark
                                    ? alpha(theme.palette.primary.main, 0.15)
                                    : alpha(theme.palette.primary.main, 0.08),
                                },
                                '&.Mui-selected': {
                                  bgcolor: isDark
                                    ? alpha(theme.palette.primary.main, 0.25)
                                    : alpha(theme.palette.primary.main, 0.12),
                                  fontWeight: 600,
                                  '&:hover': {
                                    bgcolor: isDark
                                      ? alpha(theme.palette.primary.main, 0.3)
                                      : alpha(theme.palette.primary.main, 0.16),
                                  },
                                },
                              },
                            },
                          },
                          transitionDuration: 200,
                        }}
                      >
                        {(auth as any).supportedAuthTypes.map((authType: string) => (
                          <MenuItem key={authType} value={authType}>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                              <Iconify
                                icon={
                                  authType === 'OAUTH'
                                    ? keyIcon
                                    : authType === 'API_TOKEN'
                                      ? codeIcon
                                      : authType === 'USERNAME_PASSWORD'
                                        ? personIcon
                                        : shieldIcon
                                }
                                width={20}
                                sx={{
                                  color: theme.palette.text.secondary,
                                  opacity: 0.8,
                                }}
                              />
                              <Typography
                                variant="body2"
                                sx={{ fontSize: '0.875rem', fontWeight: 500 }}
                              >
                                {authType
                                  .split('_')
                                  .map(
                                    (word) =>
                                      word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
                                  )
                                  .join(' ')}
                              </Typography>
                            </Box>
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </Box>
                )}
            </Box>

            {/* Instance Name Field - Show in create mode only */}
            {isCreateMode && (
              <Box sx={{ mb: 2.5 }}>
                <Grid container spacing={2}>
                  <Grid item xs={12}>
                    <FieldRenderer
                      field={{
                        name: 'instanceName',
                        displayName: 'Connector name',
                        fieldType: 'TEXT',
                        required: true,
                        placeholder: `e.g., ${connector.name[0].toUpperCase() + connector.name.slice(1).toLowerCase()} - Production`,
                        description: 'Give this connector a unique, descriptive name',
                        defaultValue: '',
                        validation: {},
                        isSecret: false,
                      }}
                      value={instanceName}
                      onChange={(value) => onInstanceNameChange(value)}
                      error={instanceNameError || undefined}
                    />
                  </Grid>
                </Grid>
              </Box>
            )}

            {/* OAuth App Selector - Show when OAuth is selected and OAuth configs exist (or in edit mode with existing config) or when loading */}
            {isOAuthSelected && (loadingOAuthApps || oauthApps.length > 0 || (!isCreateMode && selectedOAuthConfigId)) && (
              <Box sx={{ mb: 2.5 }}>
                <Grid container spacing={2}>
                  <Grid item xs={12}>
                    {loadingOAuthApps ? (
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, py: 2 }}>
                        <CircularProgress size={20} />
                        <Typography variant="body2" color="text.secondary">
                          Loading OAuth configurations...
                        </Typography>
                      </Box>
                    ) : (
                      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                        <Box
                          sx={{
                            display: 'flex',
                            gap: 1.5,
                            alignItems: 'flex-start',
                            flexWrap: { xs: 'wrap', sm: 'nowrap' },
                          }}
                        >
                          <FormControl fullWidth sx={{ flex: 1, minWidth: { xs: '100%', sm: 300 } }}>
                            <InputLabel>OAuth App {!isAdmin && '(Required)'}</InputLabel>
                            <Select
                              value={selectedOAuthConfigId || ''}
                              onChange={(e) => {
                                const value = e.target.value;
                                handleOAuthAppChange(value || null);
                              }}
                              label={`OAuth App ${!isAdmin ? '(Required)' : ''}`}
                              disabled={loadingOAuthConfig}
                            required={!isAdmin}
                            renderValue={(value) => {
                              if (!value && isAdmin && newOAuthAppName) {
                                // Show preview of new OAuth app being created
                                return (
                                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    <Iconify
                                      icon="mdi:plus-circle"
                                      width={18}
                                      sx={{ color: theme.palette.primary.main }}
                                    />
                                    <Typography
                                      sx={{ fontWeight: 500, color: theme.palette.primary.main }}
                                    >
                                      New: {newOAuthAppName}
                                    </Typography>
                                    <Chip
                                      label="Creating"
                                      size="small"
                                      sx={{
                                        height: 20,
                                        fontSize: '0.6875rem',
                                        bgcolor: alpha(theme.palette.warning.main, 0.1),
                                        color: theme.palette.warning.main,
                                        fontWeight: 500,
                                      }}
                                    />
                                  </Box>
                                );
                              }
                              if (!value && isAdmin) {
                                return 'None - Enter credentials manually';
                              }
                              // Find selected app name
                              const selectedApp = oauthApps.find((app: any) => app._id === value);
                              if (selectedApp) {
                                return (
                                  selectedApp.oauthInstanceName ||
                                  'Unnamed OAuth App'
                                );
                              }
                              return '';
                            }}
                            sx={{
                              '& .MuiSelect-select': {
                                display: 'flex',
                                alignItems: 'center',
                                gap: 1,
                                py: 1.25,
                              },
                            }}
                          >
                            {!isAdmin && (
                              <MenuItem value="" disabled>
                                <em>Select an OAuth app...</em>
                              </MenuItem>
                            )}
                            {oauthApps.map((app) => {
                              const appName =
                                app.oauthInstanceName ||
                                app.oauth_instance_name ||
                                'Unnamed OAuth App';
                              return (
                                <MenuItem key={app._id} value={app._id}>
                                  <Box
                                    sx={{
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: 1,
                                      width: '100%',
                                      py: 0.5,
                                    }}
                                  >
                                    <Iconify
                                      icon={shieldCheckIcon}
                                      width={18}
                                      sx={{
                                        color:
                                          selectedOAuthConfigId === app._id
                                            ? theme.palette.success.main
                                            : theme.palette.text.secondary,
                                        flexShrink: 0,
                                      }}
                                    />
                                    <Typography
                                      sx={{
                                        flex: 1,
                                        fontWeight: selectedOAuthConfigId === app._id ? 600 : 400,
                                        color:
                                          selectedOAuthConfigId === app._id
                                            ? theme.palette.primary.main
                                            : 'inherit',
                                      }}
                                    >
                                      {appName}
                                    </Typography>
                                    {app.appGroup && (
                                      <Chip
                                        label={app.appGroup}
                                        size="small"
                                        sx={{
                                          height: 20,
                                          fontSize: '0.6875rem',
                                          bgcolor: alpha(theme.palette.primary.main, 0.1),
                                          color: theme.palette.primary.main,
                                          fontWeight: 500,
                                        }}
                                      />
                                    )}
                                    {!isAdmin && (
                                      <Chip
                                        label="Configured"
                                        size="small"
                                        sx={{
                                          height: 20,
                                          fontSize: '0.6875rem',
                                          bgcolor: alpha(theme.palette.success.main, 0.1),
                                          color: theme.palette.success.main,
                                        }}
                                      />
                                    )}
                                  </Box>
                                </MenuItem>
                              );
                            })}
                            {isAdmin && (
                              <MenuItem value="">
                                <Box
                                  sx={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 1,
                                    width: '100%',
                                    py: 0.5,
                                  }}
                                >
                                  <Iconify
                                    icon={addCircleIcon}
                                    width={18}
                                    sx={{ color: theme.palette.text.secondary }}
                                  />
                                  <Typography>
                                    Create New OAuth App
                                  </Typography>
                                </Box>
                              </MenuItem>
                            )}
                          </Select>
                          <Typography
                            variant="caption"
                            sx={{
                              mt: 0.5,
                              color: theme.palette.text.secondary,
                              fontSize: '0.75rem',
                              display: 'block',
                            }}
                          >
                            {loadingOAuthApps
                              ? 'Loading OAuth apps...'
                              : isAdmin
                                ? selectedOAuthConfigId
                                  ? 'OAuth app selected. Credentials are automatically populated below and will be saved when you create/update the connector.'
                                  : newOAuthAppName
                                    ? `Creating new OAuth app "${newOAuthAppName}". This will be available for use by other connectors and team members. Enter credentials below.`
                                    : oauthApps.length === 0
                                      ? 'No OAuth apps available. Enter a name and credentials below to create a new OAuth app that will be available to your team.'
                                      : 'Select an existing OAuth app or enter a name and credentials manually to create a new one that will be available to your team.'
                                : selectedOAuthConfigId
                                  ? 'OAuth app selected. Credentials are securely stored and managed by administrators.'
                                  : oauthApps.length === 0
                                    ? 'No OAuth apps available. Please contact an administrator to create one.'
                                    : 'Select an OAuth app configured by your administrator. Credentials are not visible for security.'}
                          </Typography>
                        </FormControl>
                      </Box>

                      {/* OAuth App Instance Name Input - Show when no OAuth app is selected (manual entry) or when no OAuth configs exist */}
                      {isAdmin &&
                        (!selectedOAuthConfigId || (isCreateMode && oauthApps.length === 0)) && (
                          <Box sx={{ mt: 1.5 }}>
                            <FieldRenderer
                              field={{
                                name: 'oauthInstanceName',
                                displayName: 'OAuth App Instance Name',
                                fieldType: 'TEXT',
                                required: true,
                                placeholder:
                                  instanceName || `e.g., ${connector.name} - Production OAuth`,
                                description:
                                  oauthApps.length === 0
                                    ? 'This will be the name for your new OAuth app configuration. It will default to your connector instance name if not specified.'
                                    : 'Enter a unique name for this OAuth app instance within this connector type',
                                defaultValue: '',
                                validation: {
                                  minLength: 1,
                                  maxLength: 200,
                                },
                                isSecret: false,
                              }}
                              value={newOAuthAppName || instanceName || ''}
                              onChange={(value) => {
                                setNewOAuthAppName(value);
                                // Store in auth config for backend processing
                                onFieldChange('auth', 'oauthInstanceName', value);
                              }}
                              error={
                                (formErrors.oauthInstanceName || undefined) as string | undefined
                              }
                            />
                          </Box>
                        )}

                      {/* Info Alert for Creating New OAuth App */}
                      {isAdmin &&
                        (!selectedOAuthConfigId || (isCreateMode && oauthApps.length === 0)) &&
                        (newOAuthAppName || instanceName) && (
                          <Alert
                            severity="info"
                            icon={<Iconify icon="mdi:information-outline" width={20} />}
                            sx={{
                              mt: 1.5,
                              borderRadius: 1.25,
                              bgcolor: isDark
                                ? alpha(theme.palette.info.main, 0.1)
                                : alpha(theme.palette.info.main, 0.05),
                              border: `1px solid ${alpha(theme.palette.info.main, isDark ? 0.3 : 0.2)}`,
                              '& .MuiAlert-icon': {
                                color: theme.palette.info.main,
                              },
                            }}
                          >
                            <Typography
                              variant="body2"
                              sx={{ fontSize: '0.8125rem', fontWeight: 500, mb: 0.5 }}
                            >
                              {oauthApps.length === 0
                                ? 'Creating New OAuth App Configuration'
                                : `Creating New OAuth App: ${newOAuthAppName || instanceName}`}
                            </Typography>
                            <Typography
                              variant="caption"
                              sx={{ fontSize: '0.75rem', color: theme.palette.text.secondary }}
                            >
                              {oauthApps.length === 0
                                ? `A new OAuth app configuration will be created with the name "${newOAuthAppName || instanceName || 'your connector name'}". This OAuth app will be available for use by other connectors and team members within your organization. Enter the OAuth credentials below to complete the setup.`
                                : `A new OAuth app configuration will be created with the name &quot;${newOAuthAppName}&quot;. This OAuth app will be available for use by other connectors and team members within your organization. Enter the OAuth credentials below to complete the setup.`}
                            </Typography>
                          </Alert>
                        )}

                      {/* Info Alert for Non-Admin Users */}
                      {!isAdmin && selectedOAuthConfigId && (
                        <Alert
                          severity="info"
                          icon={<Iconify icon={shieldCheckIcon} width={20} />}
                          sx={{
                            mt: 1.5,
                            borderRadius: 1.25,
                            bgcolor: isDark
                              ? alpha(theme.palette.info.main, 0.1)
                              : alpha(theme.palette.info.main, 0.05),
                            border: `1px solid ${alpha(theme.palette.info.main, isDark ? 0.3 : 0.2)}`,
                            '& .MuiAlert-icon': {
                              color: theme.palette.info.main,
                            },
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{ fontSize: '0.8125rem', fontWeight: 500, mb: 0.5 }}
                          >
                            OAuth App Selected
                          </Typography>
                          <Typography
                            variant="caption"
                            sx={{ fontSize: '0.75rem', color: theme.palette.text.secondary }}
                          >
                            The selected OAuth app is configured and ready to use. Credentials are
                            securely managed by administrators and are not visible for security
                            reasons.
                          </Typography>
                        </Alert>
                      )}
                      </Box>
                    )}
                  </Grid>
                </Grid>
              </Box>
            )}

            <Fade
              in
              timeout={300}
              key={`auth-fields-${isCreateMode ? selectedAuthType || (auth as any).supportedAuthTypes?.[0] || '' : auth.type || ''}`}
            >
              <Box
                sx={{
                  position: 'relative',
                  minHeight: 100,
                }}
              >
                {/* Show loading indicator while OAuth config is being loaded */}
                {loadingOAuthConfig && isOAuthSelected && selectedOAuthConfigId ? (
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 2,
                      py: 4,
                    }}
                  >
                    <CircularProgress size={24} />
                    <Typography variant="body2" color="text.secondary">
                      Loading OAuth configuration...
                    </Typography>
                  </Box>
                ) : (
                  <Grid container spacing={2}>
                  {/* Use schema from selected auth type in create mode, or existing auth type in edit mode */}
                  {(() => {
                    // Use schema from selected auth type in create mode, or existing auth type in edit mode
                    // In create mode: use selectedAuthType (user's choice)
                    // In edit mode: use auth.type (stored in etcd/database when connector was created)
                    const schemaAuthType = isCreateMode
                      ? selectedAuthType || (auth as any).supportedAuthTypes?.[0] || ''
                      : auth.type || '';

                    // Get schema for current auth type
                    const schemaAuthSchemas = (auth as any).schemas || {};
                    let currentSchema = { fields: [] };
                    if (schemaAuthType && schemaAuthSchemas[schemaAuthType]) {
                      currentSchema = schemaAuthSchemas[schemaAuthType];
                    } else if (schemaAuthType && Object.keys(schemaAuthSchemas).length > 0) {
                      // If auth type doesn't match, try to find any schema (fallback)
                      const firstSchemaKey = Object.keys(schemaAuthSchemas)[0];
                      currentSchema = schemaAuthSchemas[firstSchemaKey];
                    }

                    // Handle empty schema gracefully
                    if (!currentSchema.fields || currentSchema.fields.length === 0) {
                      return (
                        <Grid item xs={12}>
                          <Alert severity="info" sx={{ borderRadius: 2 }}>
                            No authentication fields required for this authentication type.
                          </Alert>
                        </Grid>
                      );
                    }

                    return currentSchema.fields.map((field: any) => {
                      let shouldShow = true;
                      if (auth.conditionalDisplay && auth.conditionalDisplay[field.name]) {
                        shouldShow = shouldShowElement(
                          auth.conditionalDisplay,
                          field.name,
                          formData
                        );
                      }

                      const isBusinessOAuthField =
                        customGoogleBusinessOAuth(
                          connector,
                          isBusiness ? 'business' : 'individual'
                        ) &&
                        (field.name === 'clientId' || field.name === 'clientSecret');

                      const isSharePointCertField =
                        isSharePointCertificateAuth(connector) &&
                        (field.name === 'clientId' ||
                          field.name === 'tenantId' ||
                          field.name === 'sharepointDomain' ||
                          field.name === 'hasAdminConsent' ||
                          field.name === 'certificate' ||
                          field.name === 'privateKey');

                      // For OAuth: Handle credential field visibility based on user role
                      const isOAuthCredentialField =
                        isOAuthSelected &&
                        (field.name === 'clientId' || field.name === 'clientSecret');

                      const isOAuthMetadataField =
                        isOAuthSelected && (field.name === 'redirectUri' || field.name === 'scope');

                      // Non-admin users: Hide OAuth credential fields (clientId, clientSecret) when OAuth app is selected
                      // They can still see metadata fields like redirectUri if needed
                      if (
                        isOAuthSelected &&
                        !isAdmin &&
                        isOAuthCredentialField &&
                        selectedOAuthConfigId
                      ) {
                        return null;
                      }

                      // Admin users: Show credential fields when no OAuth app is selected (manual entry creates new OAuth app)
                      // The fields are populated from the selected OAuth app when one is selected and can be edited

                      // Hide metadata fields if OAuth app is selected (for both admin and non-admin)
                      // These are typically auto-populated from the OAuth app config
                      // Show them when no OAuth app is selected (manual entry)
                      if (isOAuthSelected && selectedOAuthConfigId && isOAuthMetadataField) {
                        return null;
                      }

                      if (!shouldShow || isBusinessOAuthField || isSharePointCertField) return null;

                      return (
                        <Grid
                          item
                          xs={12}
                          key={`${schemaAuthType}-${field.name}`}
                          sx={{
                            animation: 'fadeIn 0.2s ease-in-out',
                            '@keyframes fadeIn': {
                              from: { opacity: 0, transform: 'translateY(-4px)' },
                              to: { opacity: 1, transform: 'translateY(0)' },
                            },
                          }}
                        >
                          <FieldRenderer
                            field={field}
                            value={formData[field.name]}
                            onChange={(value) => onFieldChange('auth', field.name, value)}
                            error={
                              (formErrors[field.name] || undefined) as string | undefined
                            }
                          />
                        </Grid>
                      );
                    });
                  })()}

                  {auth.customFields?.map((field) => {
                    const shouldShow =
                      !auth.conditionalDisplay ||
                      !auth.conditionalDisplay[field.name] ||
                      shouldShowElement(auth.conditionalDisplay, field.name, formData);

                    const isBusinessOAuthField =
                      customGoogleBusinessOAuth(
                        connector,
                        isBusiness ? 'business' : 'individual'
                      ) &&
                      (field.name === 'clientId' || field.name === 'clientSecret');

                    const isSharePointCertField =
                      isSharePointCertificateAuth(connector) &&
                      (field.name === 'clientId' ||
                        field.name === 'tenantId' ||
                        field.name === 'sharepointDomain' ||
                        field.name === 'hasAdminConsent' ||
                        field.name === 'certificate' ||
                        field.name === 'privateKey');

                    if (!shouldShow || isBusinessOAuthField || isSharePointCertField) return null;

                    return (
                      <Grid item xs={12} key={field.name}>
                        <FieldRenderer
                          field={field}
                          value={formData[field.name]}
                          onChange={(value) => onFieldChange('auth', field.name, value)}
                          error={
                            (formErrors[field.name] || undefined) as string | undefined
                          }
                        />
                      </Grid>
                    );
                  })}

                  {auth.conditionalDisplay &&
                    Object.keys(auth.conditionalDisplay).map((fieldName: string) => {
                      // Check if field is in current schema (for selected auth type)
                      const conditionalAuthType = isCreateMode
                        ? selectedAuthType || (auth as any).supportedAuthTypes?.[0] || ''
                        : auth.type || '';
                      const conditionalAuthSchemas = (auth as any).schemas || {};
                      const conditionalSchema =
                        conditionalAuthType && conditionalAuthSchemas[conditionalAuthType]
                          ? conditionalAuthSchemas[conditionalAuthType]
                          : { fields: [] };

                      const isInSchema =
                        conditionalSchema.fields?.some((f: any) => f.name === fieldName) || false;
                      const isInCustomFields =
                        auth.customFields?.some((f: any) => f.name === fieldName) || false;

                      if (isInSchema || isInCustomFields) return null;

                      const shouldShow = shouldShowElement(
                        auth.conditionalDisplay,
                        fieldName,
                        formData
                      );
                      if (!shouldShow) return null;

                      const conditionalField = {
                        name: fieldName,
                        displayName:
                          fieldName.charAt(0).toUpperCase() +
                          fieldName.slice(1).replace(/([A-Z])/g, ' $1'),
                        fieldType: 'TEXT' as const,
                        required: false,
                        placeholder: `Enter ${fieldName}`,
                        description: `Enter ${fieldName}`,
                        defaultValue: '',
                        validation: {},
                        isSecret: false,
                      };

                      return (
                        <Grid item xs={12} key={fieldName}>
                          <FieldRenderer
                            field={conditionalField}
                            value={formData[fieldName]}
                            onChange={(value) => onFieldChange('auth', fieldName, value)}
                            error={
                              (formErrors[fieldName] || undefined) as string | undefined
                            }
                          />
                        </Grid>
                      );
                    })}
                </Grid>
                )}
              </Box>
            </Fade>
          </Paper>
        )}
    </Box>
  );
};

export default AuthSection;
