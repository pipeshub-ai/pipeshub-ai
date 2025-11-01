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
import { FieldRenderer } from '../field-renderers';
import { shouldShowElement } from '../../utils/conditional-display';
import BusinessOAuthSection from './business-oauth-section';
import { Connector, ConnectorConfig } from '../../types/types';

interface AuthSectionProps {
  connector: Connector;
  connectorConfig: ConnectorConfig | null;
  formData: Record<string, any>;
  formErrors: Record<string, string>;
  conditionalDisplay: Record<string, boolean>;
  accountTypeLoading: boolean;
  isBusiness: boolean;
  
  // Business OAuth props
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
  onFieldChange,
}) => {
  const theme = useTheme();

  if (!connectorConfig) return null;
  const { auth } = connectorConfig.config;
  let { documentationLinks } = connectorConfig.config;
  // Simplified helper function for business OAuth support
  const customGoogleBusinessOAuth = (connectorParam: Connector, accountType: string): boolean => 
    accountType === 'business' && 
    (connectorParam.appGroup === 'Google Workspace') && 
    connectorParam.authType === 'OAUTH';
  const pipeshubDocumentationUrl = documentationLinks?.find((link) => link.type === 'pipeshub')?.url || `https://docs.pipeshub.com/connectors/overview`;

  documentationLinks = documentationLinks?.filter((link) => link.type !== 'pipeshub');
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Documentation Alert */}
      <Alert
        variant="outlined"
        severity="info"
        sx={{
          borderRadius: 1.5,
          py: 1.25,
        }}
      >
        <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.4 }}>
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
          for setup instructions.
        </Typography>
      </Alert>

      {/* Redirect URI Info - Conditionally displayed */}
      {((auth.displayRedirectUri && auth.redirectUri!=="") ||
        (auth.conditionalDisplay && Object.keys(auth.conditionalDisplay).length>0 &&
          shouldShowElement(auth.conditionalDisplay, 'redirectUri', formData))) && (
        <Paper
          variant="outlined"
          sx={{
            p: 2.25,
            borderRadius: 1.5,
            bgcolor: alpha(theme.palette.primary.main, 0.02),
            borderColor: alpha(theme.palette.primary.main, 0.12),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5 }}>
            <Box
              sx={{
                p: 0.5,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mt: 0.25,
              }}
            >
              <Iconify
                icon={infoIcon}
                width={14}
                height={14}
                color={theme.palette.primary.main}
              />
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography
                variant="subtitle2"
                color="primary.main"
                sx={{
                  mb: 0.75,
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                }}
              >
                Redirect URI
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{
                  mb: 1.5,
                  fontSize: '0.8125rem',
                  lineHeight: 1.4,
                }}
              >
                {connector.name === 'OneDrive'
                  ? 'Use this URL when configuring your Azure AD App registration.'
                  : `Use this URL when configuring your ${connector.name} OAuth2 App.`}
              </Typography>
              <Box
                sx={{
                  p: 1.5,
                  borderRadius: 1.25,
                  bgcolor:
                    theme.palette.mode === 'dark'
                      ? alpha(theme.palette.grey[900], 0.6)
                      : alpha(theme.palette.grey[50], 0.8),
                  border: `1px solid ${alpha(theme.palette.divider, 0.15)}`,
                  position: 'relative',
                  overflow: 'hidden',
                  '&::before': {
                    content: '""',
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    height: '2px',
                    background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${alpha(theme.palette.primary.main, 0.3)})`,
                  },
                }}
              >
                <Typography
                  variant="body2"
                  sx={{
                    fontFamily: 'Monaco, Consolas, "SF Mono", "Roboto Mono", monospace',
                    fontSize: '0.75rem',
                    wordBreak: 'break-all',
                    color:
                      theme.palette.mode === 'dark'
                        ? theme.palette.primary.light
                        : theme.palette.primary.dark,
                    fontWeight: 500,
                    lineHeight: 1.5,
                    userSelect: 'all',
                    cursor: 'text',
                  }}
                >
                  {`${window.location.origin}/${auth.redirectUri}`}
                </Typography>
              </Box>
            </Box>
          </Box>
        </Paper>
      )}

      {/* Documentation Links - Compact Visual Guide */}
      {documentationLinks && documentationLinks.length > 0 && (
        <Paper
          variant="outlined"
          sx={{
            p: 1.5,
            borderRadius: 1.5,
            bgcolor: alpha(theme.palette.info.main, 0.02),
            borderColor: alpha(theme.palette.info.main, 0.08),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.25 }}>
            <Box
              sx={{
                p: 0.375,
                borderRadius: 0.75,
                bgcolor: alpha(theme.palette.info.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mt: 0.125,
              }}
            >
              <Iconify
                icon={bookIcon}
                width={12}
                height={12}
                color={theme.palette.info.main}
              />
            </Box>

            <Box sx={{ flex: 1 }}>
              <Typography
                variant="subtitle2"
                color="info.main"
                sx={{
                  mb: 0.5,
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                }}
              >
                Setup Documentation
              </Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{
                  mb: 1.25,
                  fontSize: '0.75rem',
                  lineHeight: 1.3,
                }}
              >
                Follow these guides to complete your {connector.name} integration setup.
              </Typography>

              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 0.5,
                }}
              >
                {documentationLinks.map((link, index) => (
                  <Box
                    key={index}
                    onClick={() => window.open(link.url, '_blank')}
                    sx={{
                      p: 1,
                      borderRadius: 1,
                      border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                      bgcolor: theme.palette.background.paper,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      transition: 'all 0.15s ease',
                      '&:hover': {
                        borderColor: alpha(theme.palette.info.main, 0.15),
                        bgcolor: alpha(theme.palette.info.main, 0.015),
                      },
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Box
                        sx={{
                          p: 0.375,
                          borderRadius: 0.75,
                          bgcolor: alpha(theme.palette.info.main, 0.06),
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          flexShrink: 0,
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
                          width={10}
                          height={10}
                          color={theme.palette.info.main}
                        />
                      </Box>

                      <Box>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            fontSize: '0.75rem',
                            color: theme.palette.text.primary,
                            lineHeight: 1.2,
                          }}
                        >
                          {link.title}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: '0.6875rem',
                            color: theme.palette.text.secondary,
                            lineHeight: 1.2,
                          }}
                        >
                          {link.type === 'setup'
                            ? 'Setup guide'
                            : link.type === 'api'
                              ? 'API reference'
                              : 'Documentation'}
                        </Typography>
                      </Box>
                    </Box>

                    <Iconify
                      icon={openInNewIcon}
                      width={12}
                      height={12}
                      color={theme.palette.text.secondary}
                      sx={{ opacity: 0.5, flexShrink: 0 }}
                    />
                  </Box>
                ))}
              </Box>
            </Box>
          </Box>
        </Paper>
      )}

      {/* Account Type Loading */}
      {accountTypeLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
          <CircularProgress size={24} />
        </Box>
      )}

      {/* Business OAuth Section */}
      {!accountTypeLoading && customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') && (
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

      {/* Form Fields */}
      <Paper
        variant="outlined"
        sx={{
          p: 2,
          borderRadius: 1.5,
          bgcolor: theme.palette.background.paper,
          borderColor: alpha(theme.palette.divider, 0.12),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
          <Box
            sx={{
              p: 0.375,
              borderRadius: 0.75,
              bgcolor: alpha(theme.palette.text.primary, 0.04),
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
              width={14}
              height={14}
              color={theme.palette.text.secondary}
            />
          </Box>
          <Box>
            <Typography
              variant="subtitle2"
              sx={{
                fontWeight: 600,
                fontSize: '0.8125rem',
                color: theme.palette.text.primary,
                mb: 0.125,
              }}
            >
              {auth.type === 'OAUTH'
                ? 'OAuth2 Credentials'
                : auth.type === 'API_TOKEN'
                  ? 'API Credentials'
                  : auth.type === 'USERNAME_PASSWORD'
                    ? 'Login Credentials'
                    : 'Authentication Settings'}
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                fontSize: '0.75rem',
                lineHeight: 1.3,
              }}
            >
              Enter your {connector.name} authentication details
            </Typography>
          </Box>
        </Box>

        <Grid container spacing={2}>
          {auth.schema.fields.map((field) => {
            // Check if field should be displayed based on conditional display rules
            let shouldShow = true; // Default to showing the field
            
            // If there's a conditional display rule for this field, evaluate it
            if (auth.conditionalDisplay && auth.conditionalDisplay[field.name]) {
              shouldShow = shouldShowElement(auth.conditionalDisplay, field.name, formData);
            }

            // Hide client_id and client_secret fields for business OAuth
            const isBusinessOAuthField = customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') &&
              (field.name === 'clientId' || field.name === 'clientSecret');

            if (!shouldShow || isBusinessOAuthField) return null;

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
            // Check if custom field should be displayed based on conditional display rules
            const shouldShow =
              !auth.conditionalDisplay ||
              !auth.conditionalDisplay[field.name] ||
              shouldShowElement(auth.conditionalDisplay, field.name, formData);

            // Hide client_id and client_secret fields for business OAuth
            const isBusinessOAuthField = customGoogleBusinessOAuth(connector, isBusiness ? 'business' : 'individual') &&
              (field.name === 'clientId' || field.name === 'clientSecret');

            if (!shouldShow || isBusinessOAuthField) return null;

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

          {/* Render conditionally displayed fields that might not be in schema */}
          {auth.conditionalDisplay &&
            Object.keys(auth.conditionalDisplay).map((fieldName) => {
              // Skip if field is already rendered in schema or custom fields
              const isInSchema = auth.schema.fields.some((f) => f.name === fieldName);
              const isInCustomFields = auth.customFields.some((f) => f.name === fieldName);

              if (isInSchema || isInCustomFields) return null;

              // Check if this conditional field should be shown
              const shouldShow = shouldShowElement(
                auth.conditionalDisplay,
                fieldName,
                formData
              );
              if (!shouldShow) return null;

              // Create a basic field definition for conditional fields
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
