import React from 'react';
import {
  Paper,
  Box,
  Typography,
  TextField,
  Button,
  FormControlLabel,
  Checkbox,
  alpha,
  useTheme,
  Divider,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import cloudIcon from '@iconify-icons/mdi/microsoft-azure';
import certificateIcon from '@iconify-icons/mdi/certificate';
import lockIcon from '@iconify-icons/mdi/lock';
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import fileDocumentIcon from '@iconify-icons/mdi/file-document-outline';
import uploadIcon from '@iconify-icons/mdi/upload';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';

interface SharePointOAuthSectionProps {
  // Basic Auth Fields
  clientId: string;
  tenantId: string;
  sharepointDomain: string;
  hasAdminConsent: boolean;
  
  // Validation Errors
  clientIdError: string | null;
  tenantIdError: string | null;
  sharepointDomainError: string | null;
  
  // Certificate File
  certificateFile: File | null;
  certificateFileName: string | null;
  certificateError: string | null;
  certificateData: Record<string, any> | null;
  
  // Private Key File
  privateKeyFile: File | null;
  privateKeyFileName: string | null;
  privateKeyError: string | null;
  privateKeyData: string | null;
  
  // Event Handlers
  onClientIdChange: (clientId: string) => void;
  onTenantIdChange: (tenantId: string) => void;
  onSharePointDomainChange: (domain: string) => void;
  onAdminConsentChange: (checked: boolean) => void;
  onCertificateUpload: () => void;
  onCertificateChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onPrivateKeyUpload: () => void;
  onPrivateKeyChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  
  // Refs
  certificateInputRef: React.RefObject<HTMLInputElement>;
  privateKeyInputRef: React.RefObject<HTMLInputElement>;
}

const SharePointOAuthSection: React.FC<SharePointOAuthSectionProps> = ({
  clientId,
  tenantId,
  sharepointDomain,
  hasAdminConsent,
  clientIdError,
  tenantIdError,
  sharepointDomainError,
  certificateFile,
  certificateFileName,
  certificateError,
  certificateData,
  privateKeyFile,
  privateKeyFileName,
  privateKeyError,
  privateKeyData,
  onClientIdChange,
  onTenantIdChange,
  onSharePointDomainChange,
  onAdminConsentChange,
  onCertificateUpload,
  onCertificateChange,
  onPrivateKeyUpload,
  onPrivateKeyChange,
  certificateInputRef,
  privateKeyInputRef,
}) => {
  const theme = useTheme();

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 2,
        borderRadius: 1.25,
        bgcolor: alpha(theme.palette.info.main, 0.02),
        borderColor: alpha(theme.palette.divider, 0.1),
        mb: 2,
      }}
    >
      {/* Section Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
        <Box
          sx={{
            p: 0.625,
            borderRadius: 1,
            bgcolor: alpha(theme.palette.info.main, 0.1),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Iconify
            icon={cloudIcon}
            width={16}
            sx={{ color: theme.palette.info.main }}
          />
        </Box>
        <Box sx={{ flex: 1 }}>
          <Typography 
            variant="subtitle2" 
            sx={{ 
              fontSize: '0.875rem', 
              fontWeight: 600,
              color: theme.palette.text.primary,
            }}
          >
            SharePoint OAuth Configuration
          </Typography>
          <Typography 
            variant="caption" 
            sx={{ 
              fontSize: '0.75rem',
              color: theme.palette.text.secondary,
              mt: 0.25,
              display: 'block',
            }}
          >
            Azure AD application credentials and certificate authentication
          </Typography>
        </Box>
      </Box>

      {/* Azure AD Application Fields */}
      <Box sx={{ mb: 2 }}>
        <TextField
          fullWidth
          size="small"
          label="Application (Client) ID"
          value={clientId}
          onChange={(e) => onClientIdChange(e.target.value)}
          error={!!clientIdError}
          helperText={clientIdError || 'Azure AD Application (Client) ID'}
          placeholder="00000000-0000-0000-0000-000000000000"
          sx={{
            mb: 2,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1.25,
              fontSize: '0.875rem',
              backgroundColor: alpha(theme.palette.background.paper, 0.8),
              transition: 'all 0.2s',
              '&:hover': {
                backgroundColor: alpha(theme.palette.background.paper, 1),
              },
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: alpha(theme.palette.primary.main, 0.3),
              },
              '&.Mui-focused': {
                backgroundColor: theme.palette.background.paper,
              },
              '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                borderWidth: 1.5,
              },
            },
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
              padding: '10.5px 14px',
              fontWeight: 400,
            },
            '& .MuiInputLabel-root': {
              fontSize: '0.875rem',
              fontWeight: 500,
            },
            '& .MuiFormHelperText-root': {
              fontSize: '0.75rem',
              mt: 0.75,
              ml: 1,
            },
          }}
        />

        <TextField
          fullWidth
          size="small"
          label="Directory (Tenant) ID"
          value={tenantId}
          onChange={(e) => onTenantIdChange(e.target.value)}
          error={!!tenantIdError}
          helperText={tenantIdError || 'Azure AD Directory (Tenant) ID (Optional)'}
          placeholder="00000000-0000-0000-0000-000000000000"
          sx={{
            mb: 2,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1.25,
              fontSize: '0.875rem',
              backgroundColor: alpha(theme.palette.background.paper, 0.8),
              transition: 'all 0.2s',
              '&:hover': {
                backgroundColor: alpha(theme.palette.background.paper, 1),
              },
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: alpha(theme.palette.primary.main, 0.3),
              },
              '&.Mui-focused': {
                backgroundColor: theme.palette.background.paper,
              },
              '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                borderWidth: 1.5,
              },
            },
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
              padding: '10.5px 14px',
              fontWeight: 400,
            },
            '& .MuiInputLabel-root': {
              fontSize: '0.875rem',
              fontWeight: 500,
            },
            '& .MuiFormHelperText-root': {
              fontSize: '0.75rem',
              mt: 0.75,
              ml: 1,
            },
          }}
        />

        <TextField
          fullWidth
          size="small"
          label="SharePoint Domain"
          value={sharepointDomain}
          onChange={(e) => onSharePointDomainChange(e.target.value)}
          error={!!sharepointDomainError}
          helperText={sharepointDomainError || 'SharePoint domain URL'}
          placeholder="https://your-domain.sharepoint.com"
          sx={{
            mb: 2,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1.25,
              fontSize: '0.875rem',
              backgroundColor: alpha(theme.palette.background.paper, 0.8),
              transition: 'all 0.2s',
              '&:hover': {
                backgroundColor: alpha(theme.palette.background.paper, 1),
              },
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: alpha(theme.palette.primary.main, 0.3),
              },
              '&.Mui-focused': {
                backgroundColor: theme.palette.background.paper,
              },
              '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                borderWidth: 1.5,
              },
            },
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
              padding: '10.5px 14px',
              fontWeight: 400,
            },
            '& .MuiInputLabel-root': {
              fontSize: '0.875rem',
              fontWeight: 500,
            },
            '& .MuiFormHelperText-root': {
              fontSize: '0.75rem',
              mt: 0.75,
              ml: 1,
            },
          }}
        />

        <FormControlLabel
          control={
            <Checkbox
              checked={hasAdminConsent}
              onChange={(e) => onAdminConsentChange(e.target.checked)}
              color="primary"
              sx={{
                '& .MuiSvgIcon-root': {
                  fontSize: '1.25rem',
                },
              }}
            />
          }
          label={
            <Box>
              <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
                Has Admin Consent
              </Typography>
              <Typography variant="caption" sx={{ fontSize: '0.75rem', color: theme.palette.text.secondary }}>
                Admin consent has been granted for the application
              </Typography>
            </Box>
          }
        />
      </Box>

      <Divider sx={{ my: 2 }} />

      {/* Certificate Authentication Section */}
      <Box sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
          <Box
            sx={{
              p: 0.625,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.warning.main, 0.1),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Iconify
              icon={certificateIcon}
              width={16}
              sx={{ color: theme.palette.warning.main }}
            />
          </Box>
          <Box sx={{ flex: 1 }}>
            <Typography 
              variant="subtitle2" 
              sx={{ 
                fontSize: '0.875rem', 
                fontWeight: 600,
                color: theme.palette.text.primary,
              }}
            >
              Certificate-Based Authentication
            </Typography>
            <Typography 
              variant="caption" 
              sx={{ 
                fontSize: '0.75rem',
                color: theme.palette.text.secondary,
                mt: 0.25,
                display: 'block',
              }}
            >
              X.509 certificate and private key for secure authentication
            </Typography>
          </Box>
        </Box>

        {/* Certificate File Upload */}
        <Box sx={{ mb: 2 }}>
          <Typography 
            variant="subtitle2" 
            sx={{ 
              fontSize: '0.875rem', 
              fontWeight: 500,
              mb: 1,
              color: theme.palette.text.primary,
            }}
          >
            Client Certificate (.crt)
          </Typography>
          
          <Paper
            variant="outlined"
            onClick={onCertificateUpload}
            sx={{
              p: 2,
              borderRadius: 1.25,
              borderWidth: '1.5px',
              borderStyle: (certificateFile || certificateData) ? 'solid' : 'dashed',
              borderColor: (certificateFile || certificateData)
                ? alpha(theme.palette.success.main, 0.3)
                : alpha(theme.palette.divider, 0.3),
              bgcolor: (certificateFile || certificateData)
                ? alpha(theme.palette.success.main, 0.04)
                : 'transparent',
              cursor: 'pointer',
              transition: 'all 0.2s',
              '&:hover': {
                borderColor: (certificateFile || certificateData)
                  ? alpha(theme.palette.success.main, 0.5)
                  : alpha(theme.palette.primary.main, 0.4),
                bgcolor: (certificateFile || certificateData)
                  ? alpha(theme.palette.success.main, 0.06)
                  : alpha(theme.palette.primary.main, 0.04),
              },
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  p: 1,
                  borderRadius: 1,
                  bgcolor: (certificateFile || certificateData)
                    ? alpha(theme.palette.success.main, 0.12)
                    : alpha(theme.palette.primary.main, 0.08),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Iconify
                  icon={(certificateFile || certificateData) ? checkCircleIcon : fileDocumentIcon}
                  width={18}
                  sx={{
                    color: (certificateFile || certificateData) 
                      ? theme.palette.success.main 
                      : theme.palette.primary.main
                  }}
                />
              </Box>
              
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography 
                  variant="body2" 
                  sx={{ 
                    fontSize: '0.875rem',
                    fontWeight: 500,
                    mb: 0.25,
                    color: theme.palette.text.primary,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {certificateFileName || 'No file selected'}
                </Typography>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontSize: '0.75rem',
                    color: theme.palette.text.secondary,
                  }}
                >
                  {(certificateFile || certificateData)
                    ? 'Certificate file loaded successfully'
                    : 'Click to upload .crt, .cer, or .pem file'
                  }
                </Typography>
              </Box>
              
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon={uploadIcon} width={14} />}
                onClick={(e) => {
                  e.stopPropagation();
                  onCertificateUpload();
                }}
                sx={{
                  textTransform: 'none',
                  fontWeight: 500,
                  px: 2,
                  py: 0.625,
                  borderRadius: 1,
                  fontSize: '0.8125rem',
                  flexShrink: 0,
                  borderColor: alpha(theme.palette.divider, 0.3),
                  '&:hover': {
                    borderColor: theme.palette.primary.main,
                    backgroundColor: alpha(theme.palette.primary.main, 0.04),
                  },
                }}
              >
                {certificateFile ? 'Replace' : 'Upload'}
              </Button>
            </Box>
          </Paper>

          {certificateError && (
            <Typography 
              variant="caption" 
              sx={{ 
                color: theme.palette.error.main,
                mt: 0.75,
                ml: 1,
                display: 'block',
                fontSize: '0.75rem',
              }}
            >
              {certificateError}
            </Typography>
          )}

          <input
            ref={certificateInputRef}
            type="file"
            accept=".crt,.cer,.pem"
            onChange={onCertificateChange}
            style={{ display: 'none' }}
          />
        </Box>

        {/* Private Key File Upload */}
        <Box sx={{ mb: 2 }}>
          <Typography 
            variant="subtitle2" 
            sx={{ 
              fontSize: '0.875rem', 
              fontWeight: 500,
              mb: 1,
              color: theme.palette.text.primary,
            }}
          >
            Private Key (.key)
          </Typography>
          
          <Paper
            variant="outlined"
            onClick={onPrivateKeyUpload}
            sx={{
              p: 2,
              borderRadius: 1.25,
              borderWidth: '1.5px',
              borderStyle: (privateKeyFile || privateKeyData) ? 'solid' : 'dashed',
              borderColor: (privateKeyFile || privateKeyData)
                ? alpha(theme.palette.success.main, 0.3)
                : alpha(theme.palette.divider, 0.3),
              bgcolor: (privateKeyFile || privateKeyData)
                ? alpha(theme.palette.success.main, 0.04)
                : 'transparent',
              cursor: 'pointer',
              transition: 'all 0.2s',
              '&:hover': {
                borderColor: (privateKeyFile || privateKeyData)
                  ? alpha(theme.palette.success.main, 0.5)
                  : alpha(theme.palette.primary.main, 0.4),
                bgcolor: (privateKeyFile || privateKeyData)
                  ? alpha(theme.palette.success.main, 0.06)
                  : alpha(theme.palette.primary.main, 0.04),
              },
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  p: 1,
                  borderRadius: 1,
                  bgcolor: (privateKeyFile || privateKeyData)
                    ? alpha(theme.palette.success.main, 0.12)
                    : alpha(theme.palette.warning.main, 0.08),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Iconify
                  icon={(privateKeyFile || privateKeyData) ? checkCircleIcon : lockIcon}
                  width={18}
                  sx={{
                    color: (privateKeyFile || privateKeyData) 
                      ? theme.palette.success.main 
                      : theme.palette.warning.main
                  }}
                />
              </Box>
              
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography 
                  variant="body2" 
                  sx={{ 
                    fontSize: '0.875rem',
                    fontWeight: 500,
                    mb: 0.25,
                    color: theme.palette.text.primary,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {privateKeyFileName || 'No file selected'}
                </Typography>
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontSize: '0.75rem',
                    color: theme.palette.text.secondary,
                  }}
                >
                  {(privateKeyFile || privateKeyData)
                    ? 'Private key loaded successfully (PKCS#8)'
                    : 'Click to upload .key or .pem file'
                  }
                </Typography>
              </Box>
              
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon={uploadIcon} width={14} />}
                onClick={(e) => {
                  e.stopPropagation();
                  onPrivateKeyUpload();
                }}
                sx={{
                  textTransform: 'none',
                  fontWeight: 500,
                  px: 2,
                  py: 0.625,
                  borderRadius: 1,
                  fontSize: '0.8125rem',
                  flexShrink: 0,
                  borderColor: alpha(theme.palette.divider, 0.3),
                  '&:hover': {
                    borderColor: theme.palette.primary.main,
                    backgroundColor: alpha(theme.palette.primary.main, 0.04),
                  },
                }}
              >
                {privateKeyFile ? 'Replace' : 'Upload'}
              </Button>
            </Box>
          </Paper>

          {privateKeyError && (
            <Typography 
              variant="caption" 
              sx={{ 
                color: theme.palette.error.main,
                mt: 0.75,
                ml: 1,
                display: 'block',
                fontSize: '0.75rem',
              }}
            >
              {privateKeyError}
            </Typography>
          )}

          <input
            ref={privateKeyInputRef}
            type="file"
            accept=".key,.pem"
            onChange={onPrivateKeyChange}
            style={{ display: 'none' }}
          />
        </Box>

        {/* Security Notice */}
        <Paper
          variant="outlined"
          sx={{
            p: 1.5,
            borderRadius: 1.25,
            bgcolor: alpha(theme.palette.warning.main, 0.04),
            borderColor: alpha(theme.palette.warning.main, 0.2),
          }}
        >
          <Box sx={{ display: 'flex', gap: 1.5 }}>
            <Iconify
              icon={alertCircleIcon}
              width={18}
              sx={{ 
                color: theme.palette.warning.main,
                flexShrink: 0,
                mt: 0.25,
              }}
            />
            <Box>
              <Typography 
                variant="subtitle2" 
                sx={{ 
                  mb: 0.75, 
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  color: theme.palette.text.primary,
                }}
              >
                Security Requirements
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2, '& li': { mb: 0.5 } }}>
                <Typography 
                  component="li" 
                  variant="caption" 
                  sx={{ 
                    fontSize: '0.75rem',
                    color: theme.palette.text.secondary,
                    lineHeight: 1.5,
                  }}
                >
                  Certificate must be in X.509 format (BEGIN CERTIFICATE)
                </Typography>
                <Typography 
                  component="li" 
                  variant="caption" 
                  sx={{ 
                    fontSize: '0.75rem',
                    color: theme.palette.text.secondary,
                    lineHeight: 1.5,
                  }}
                >
                  Private key must be in PKCS#8 format (BEGIN PRIVATE KEY)
                </Typography>
                <Typography 
                  component="li" 
                  variant="caption" 
                  sx={{ 
                    fontSize: '0.75rem',
                    color: theme.palette.text.secondary,
                    lineHeight: 1.5,
                  }}
                >
                  Private key must not be encrypted (use -nocrypt flag)
                </Typography>
              </Box>
            </Box>
          </Box>
        </Paper>
      </Box>

      {/* Private Key Status */}
      {privateKeyData && (
        <Box sx={{ mt: 2 }}>
          <Paper
            variant="outlined"
            sx={{
              p: 1.5,
              borderRadius: 1.25,
              bgcolor: alpha(theme.palette.success.main, 0.04),
              borderColor: alpha(theme.palette.success.main, 0.2),
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Iconify
                icon={checkCircleIcon}
                width={18}
                sx={{ color: theme.palette.success.main }}
              />
              <Typography 
                variant="body2" 
                sx={{ 
                  fontSize: '0.875rem',
                  color: theme.palette.text.secondary,
                }}
              >
                Private key successfully loaded and validated (PKCS#8 format)
              </Typography>
            </Box>
          </Paper>
        </Box>
      )}
    </Paper>
  );
};

export default SharePointOAuthSection;