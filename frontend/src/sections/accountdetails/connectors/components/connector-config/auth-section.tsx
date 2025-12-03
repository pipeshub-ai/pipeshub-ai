import React from 'react';
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
import { FieldRenderer } from '../field-renderers';
import { shouldShowElement } from '../../utils/conditional-display';
import BusinessOAuthSection from './business-oauth-section';
import SharePointOAuthSection from './sharepoint-oauth-section';
import { Connector, ConnectorConfig } from '../../types/types';

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
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [copied, setCopied] = React.useState(false);
  const [showDocs, setShowDocs] = React.useState(false);
  const [showRedirectUri, setShowRedirectUri] = React.useState(true);

  if (!connectorConfig) return null;
  const { auth } = connectorConfig.config;
  let { documentationLinks } = connectorConfig.config;

  const customGoogleBusinessOAuth = (connectorParam: Connector, accountType: string): boolean =>
    accountType === 'business' &&
    connectorParam.appGroup === 'Google Workspace' &&
    connectorParam.authType === 'OAUTH';

  const isSharePointCertificateAuth = (connectorParam: Connector): boolean =>
    connectorParam.name === 'SharePoint Online' &&
    (connectorParam.authType === 'OAUTH_CERTIFICATE' ||
      connectorParam.authType === 'OAUTH_ADMIN_CONSENT');

  const pipeshubDocumentationUrl =
    documentationLinks?.find((link) => link.type === 'pipeshub')?.url ||
    `https://docs.pipeshub.com/connectors/overview`;

  documentationLinks = documentationLinks?.filter((link) => link.type !== 'pipeshub');

  const redirectUri = `${window.location.origin}/${auth.redirectUri}`;
  const shouldShowRedirectUri =
    (auth.displayRedirectUri && auth.redirectUri !== '') ||
    (auth.conditionalDisplay &&
      Object.keys(auth.conditionalDisplay).length > 0 &&
      shouldShowElement(auth.conditionalDisplay, 'redirectUri', formData));

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
          certificateFile={certificateFile}
          certificateFileName={certificateFileName}
          certificateError={certificateError}
          certificateData={certificateData}
          privateKeyFile={privateKeyFile}
          privateKeyFileName={privateKeyFileName}
          privateKeyError={privateKeyError}
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
        />
      )}

      {/* Form Fields - More Compact */}
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
            <Iconify
              icon={
                auth.type === 'OAUTH'
                  ? shieldIcon
                  : auth.type === 'API_TOKEN'
                    ? keyIcon
                    : auth.type === 'USERNAME_PASSWORD'
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
              {auth.type === 'OAUTH'
                ? 'OAuth2 Credentials'
                : auth.type === 'API_TOKEN'
                  ? 'API Credentials'
                  : auth.type === 'USERNAME_PASSWORD'
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

        <Grid container spacing={2}>
          {auth.schema.fields.map((field) => {
            let shouldShow = true;
            if (auth.conditionalDisplay && auth.conditionalDisplay[field.name]) {
              shouldShow = shouldShowElement(auth.conditionalDisplay, field.name, formData);
            }

            const isBusinessOAuthField =
              customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') &&
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
                  error={formErrors[field.name]}
                />
              </Grid>
            );
          })}

          {auth.customFields.map((field) => {
            const shouldShow =
              !auth.conditionalDisplay ||
              !auth.conditionalDisplay[field.name] ||
              shouldShowElement(auth.conditionalDisplay, field.name, formData);

            const isBusinessOAuthField =
              customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') &&
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
                  error={formErrors[field.name]}
                />
              </Grid>
            );
          })}

          {auth.conditionalDisplay &&
            Object.keys(auth.conditionalDisplay).map((fieldName) => {
              const isInSchema = auth.schema.fields.some((f) => f.name === fieldName);
              const isInCustomFields = auth.customFields.some((f) => f.name === fieldName);

              if (isInSchema || isInCustomFields) return null;

              const shouldShow = shouldShowElement(auth.conditionalDisplay, fieldName, formData);
              if (!shouldShow) return null;

              const conditionalField = {
                name: fieldName,
                displayName:
                  fieldName.charAt(0).toUpperCase() + fieldName.slice(1).replace(/([A-Z])/g, ' $1'),
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
                    error={formErrors[fieldName]}
                  />
                </Grid>
              );
            })}
        </Grid>
      </Paper>
    </Box>
  );
};

export default AuthSection;
