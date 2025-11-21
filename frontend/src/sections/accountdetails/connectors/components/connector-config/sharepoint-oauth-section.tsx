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
import keyIcon from '@iconify-icons/mdi/key';
import checkIcon from '@iconify-icons/mdi/check';
import certificateIcon from '@iconify-icons/mdi/certificate';
import lockIcon from '@iconify-icons/mdi/lock';
import cloudIcon from '@iconify-icons/mdi/microsoft-azure';

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
        p: 3,
        borderRadius: 1.5,
        bgcolor: alpha(theme.palette.primary.main, 0.02),
        borderColor: alpha(theme.palette.primary.main, 0.12),
        mb: 3,
      }}
    >
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2.5 }}>
        <Box
          sx={{
            p: 1,
            borderRadius: 1,
            bgcolor: alpha(theme.palette.primary.main, 0.1),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Iconify
            icon={cloudIcon}
            width={20}
            height={20}
            color={theme.palette.primary.main}
          />
        </Box>
        <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
          SharePoint Online OAuth Configuration
        </Typography>
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 3, fontSize: '0.8125rem' }}>
        Configure your Azure AD application credentials to connect to SharePoint Online.
        Upload your certificate and private key files for secure authentication.
      </Typography>

      {/* Azure AD Application Fields */}
      <Box sx={{ mb: 3 }}>
        <TextField
          fullWidth
          label="Application (Client) ID"
          value={clientId}
          onChange={(e) => onClientIdChange(e.target.value)}
          error={!!clientIdError}
          helperText={clientIdError || 'Enter your Azure AD Application (Client) ID'}
          placeholder="00000000-0000-0000-0000-000000000000"
          sx={{ mb: 2 }}
        />

        <TextField
          fullWidth
          label="Directory (Tenant) ID"
          value={tenantId}
          onChange={(e) => onTenantIdChange(e.target.value)}
          error={!!tenantIdError}
          helperText={tenantIdError || 'Enter your Azure AD Directory (Tenant) ID (Optional)'}
          placeholder="00000000-0000-0000-0000-000000000000"
          sx={{ mb: 2 }}
        />

        <TextField
          fullWidth
          label="SharePoint Domain"
          value={sharepointDomain}
          onChange={(e) => onSharePointDomainChange(e.target.value)}
          error={!!sharepointDomainError}
          helperText={sharepointDomainError || 'Enter your SharePoint domain URL'}
          placeholder="https://your-domain.sharepoint.com"
          sx={{ mb: 2 }}
        />

        <FormControlLabel
          control={
            <Checkbox
              checked={hasAdminConsent}
              onChange={(e) => onAdminConsentChange(e.target.checked)}
              color="primary"
            />
          }
          label={
            <Box>
              <Typography variant="body2">Has Admin Consent</Typography>
              <Typography variant="caption" color="text.secondary">
                Check if admin consent has been granted for the application
              </Typography>
            </Box>
          }
        />
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* Certificate Authentication Section */}
      <Box sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
          <Box
            sx={{
              p: 0.75,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.info.main, 0.1),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Iconify
              icon={certificateIcon}
              width={18}
              height={18}
              color={theme.palette.info.main}
            />
          </Box>
          <Typography variant="subtitle1" sx={{ fontSize: '0.9375rem', fontWeight: 600 }}>
            Certificate-Based Authentication
          </Typography>
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5, fontSize: '0.8125rem' }}>
          Upload your X.509 certificate (.crt) and private key (.key) files for secure authentication.
        </Typography>

        {/* Certificate File Upload */}
        <Box sx={{ mb: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1.5, fontSize: '0.875rem' }}>
            Client Certificate (.crt)
          </Typography>
          
          <Paper
            variant="outlined"
            sx={{
              p: 2,
              borderRadius: 1,
              borderStyle: (certificateFile || certificateData) ? 'solid' : 'dashed',
              borderColor: (certificateFile || certificateData)
                ? alpha(theme.palette.success.main, 0.3)
                : alpha(theme.palette.info.main, 0.3),
              bgcolor: (certificateFile || certificateData)
                ? alpha(theme.palette.success.main, 0.02)
                : alpha(theme.palette.info.main, 0.02),
              cursor: 'pointer',
              transition: 'all 0.2s ease-in-out',
              '&:hover': {
                borderColor: alpha(theme.palette.info.main, 0.5),
                bgcolor: alpha(theme.palette.info.main, 0.04),
              },
            }}
            onClick={onCertificateUpload}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <Box
                sx={{
                  p: 1,
                  borderRadius: 1,
                  bgcolor: (certificateFile || certificateData)
                    ? alpha(theme.palette.success.main, 0.1)
                    : alpha(theme.palette.info.main, 0.1),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Iconify
                  icon={(certificateFile || certificateData) ? checkIcon : certificateIcon}
                  width={24}
                  height={24}
                  color={(certificateFile || certificateData) ? theme.palette.success.main : theme.palette.info.main}
                />
              </Box>
              
              <Box sx={{ flex: 1 }}>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  {certificateFileName || 'Click to upload certificate file'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {(certificateFile || certificateData)
                    ? 'Certificate file loaded (BEGIN CERTIFICATE format)'
                    : 'Upload your .crt certificate file'
                  }
                </Typography>
              </Box>
              
              <Button
                variant="outlined"
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onCertificateUpload();
                }}
                sx={{ minWidth: 100 }}
              >
                {certificateFile ? 'Replace' : 'Upload'}
              </Button>
            </Box>
          </Paper>

          {certificateError && (
            <Typography variant="caption" color="error" sx={{ mt: 1, display: 'block' }}>
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
          <Typography variant="subtitle2" sx={{ mb: 1.5, fontSize: '0.875rem' }}>
            Private Key (.key)
          </Typography>
          
          <Paper
            variant="outlined"
            sx={{
              p: 2,
              borderRadius: 1,
              borderStyle: (privateKeyFile || privateKeyData) ? 'solid' : 'dashed',
              borderColor: (privateKeyFile || privateKeyData)
                ? alpha(theme.palette.success.main, 0.3)
                : alpha(theme.palette.warning.main, 0.3),
              bgcolor: (privateKeyFile || privateKeyData)
                ? alpha(theme.palette.success.main, 0.02)
                : alpha(theme.palette.warning.main, 0.02),
              cursor: 'pointer',
              transition: 'all 0.2s ease-in-out',
              '&:hover': {
                borderColor: alpha(theme.palette.warning.main, 0.5),
                bgcolor: alpha(theme.palette.warning.main, 0.04),
              },
            }}
            onClick={onPrivateKeyUpload}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <Box
                sx={{
                  p: 1,
                  borderRadius: 1,
                  bgcolor: (privateKeyFile || privateKeyData)
                    ? alpha(theme.palette.success.main, 0.1)
                    : alpha(theme.palette.warning.main, 0.1),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Iconify
                  icon={(privateKeyFile || privateKeyData) ? checkIcon : lockIcon}
                  width={24}
                  height={24}
                  color={(privateKeyFile || privateKeyData) ? theme.palette.success.main : theme.palette.warning.main}
                />
              </Box>
              
              <Box sx={{ flex: 1 }}>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  {privateKeyFileName || 'Click to upload private key file'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {(privateKeyFile || privateKeyData)
                    ? 'Private key loaded (BEGIN PRIVATE KEY format, PKCS#8)'
                    : 'Upload your .key private key file'
                  }
                </Typography>
              </Box>
              
              <Button
                variant="outlined"
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onPrivateKeyUpload();
                }}
                sx={{ minWidth: 100 }}
              >
                {privateKeyFile ? 'Replace' : 'Upload'}
              </Button>
            </Box>
          </Paper>

          {privateKeyError && (
            <Typography variant="caption" color="error" sx={{ mt: 1, display: 'block' }}>
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
            p: 2,
            borderRadius: 1,
            bgcolor: alpha(theme.palette.warning.main, 0.02),
            borderColor: alpha(theme.palette.warning.main, 0.2),
          }}
        >
          <Box sx={{ display: 'flex', gap: 1.5 }}>
            <Iconify
              icon={keyIcon}
              width={20}
              height={20}
              color={theme.palette.warning.main}
              sx={{ flexShrink: 0, mt: 0.25 }}
            />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.5, fontSize: '0.875rem' }}>
                Security Requirements
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                • Certificate must be in X.509 format (BEGIN CERTIFICATE / END CERTIFICATE)
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                • Private key must be in PKCS#8 format (BEGIN PRIVATE KEY / END PRIVATE KEY)
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                • Private key must not be encrypted (use -nocrypt flag during generation)
              </Typography>
            </Box>
          </Box>
        </Paper>
      </Box>

      {/* Private Key Preview (Show only that it's loaded, not the actual key) */}
      {privateKeyData && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1, fontSize: '0.875rem' }}>
            Private Key Status
          </Typography>
          <Paper
            variant="outlined"
            sx={{
              p: 2,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.success.main, 0.04),
              borderColor: alpha(theme.palette.success.main, 0.12),
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Iconify
                icon={checkIcon}
                width={20}
                height={20}
                color={theme.palette.success.main}
              />
              <Typography variant="caption" color="text.secondary">
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