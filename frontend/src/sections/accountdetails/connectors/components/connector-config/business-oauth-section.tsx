import React from 'react';
import {
  Paper,
  Box,
  Typography,
  TextField,
  Button,
  alpha,
  useTheme,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import keyIcon from '@iconify-icons/mdi/key';
import checkIcon from '@iconify-icons/mdi/check';
import codeIcon from '@iconify-icons/mdi/code';

interface BusinessOAuthSectionProps {
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
}

const BusinessOAuthSection: React.FC<BusinessOAuthSectionProps> = ({
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
            icon={keyIcon}
            width={20}
            height={20}
            color={theme.palette.primary.main}
          />
        </Box>
        <Typography variant="h6" sx={{ fontSize: '1rem', fontWeight: 600 }}>
          Business OAuth Configuration
        </Typography>
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 3, fontSize: '0.8125rem' }}>
        For business accounts, upload your Google Cloud Service Account JSON credentials file
        and provide the admin email address.
      </Typography>

      {/* Admin Email Field */}
      <Box sx={{ mb: 3 }}>
        <TextField
          fullWidth
          label="Admin Email"
          value={adminEmail}
          onChange={(e) => onAdminEmailChange(e.target.value)}
          error={!!adminEmailError}
          helperText={adminEmailError || 'Enter the Google Workspace admin email address'}
          placeholder="admin@yourdomain.com"
          sx={{ mb: 1 }}
        />
      </Box>

      {/* JSON File Upload */}
      <Box sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1.5, fontSize: '0.875rem' }}>
          Google Cloud Service Account JSON
        </Typography>
        
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            borderRadius: 1,
            borderStyle: (selectedFile || jsonData) ? 'solid' : 'dashed',
            borderColor: (selectedFile || jsonData)
              ? alpha(theme.palette.success.main, 0.3)
              : alpha(theme.palette.primary.main, 0.3),
            bgcolor: (selectedFile || jsonData)
              ? alpha(theme.palette.success.main, 0.02)
              : alpha(theme.palette.primary.main, 0.02),
            cursor: 'pointer',
            transition: 'all 0.2s ease-in-out',
            '&:hover': {
              borderColor: alpha(theme.palette.primary.main, 0.5),
              bgcolor: alpha(theme.palette.primary.main, 0.04),
            },
          }}
          onClick={onFileUpload}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Box
              sx={{
                p: 1,
                borderRadius: 1,
                bgcolor: (selectedFile || jsonData)
                  ? alpha(theme.palette.success.main, 0.1)
                  : alpha(theme.palette.primary.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon={(selectedFile || jsonData) ? checkIcon : codeIcon}
                width={24}
                height={24}
                color={(selectedFile || jsonData) ? theme.palette.success.main : theme.palette.primary.main}
              />
            </Box>
            
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                {fileName || 'Click to upload JSON file'}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {(selectedFile || jsonData)
                  ? 'Google Cloud Service Account credentials loaded'
                  : 'Upload your Google Cloud Service Account JSON file'
                }
              </Typography>
            </Box>
            
            <Button
              variant="outlined"
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onFileUpload();
              }}
              sx={{ minWidth: 100 }}
            >
              {selectedFile ? 'Replace' : 'Upload'}
            </Button>
          </Box>
        </Paper>

        {fileError && (
          <Typography variant="caption" color="error" sx={{ mt: 1, display: 'block' }}>
            {fileError}
          </Typography>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json"
          onChange={onFileChange}
          style={{ display: 'none' }}
        />
      </Box>

      {/* JSON Data Preview */}
      {jsonData && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1, fontSize: '0.875rem' }}>
            Loaded Credentials Preview
          </Typography>
          <Paper
            variant="outlined"
            sx={{
              p: 2,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.grey[500], 0.04),
              borderColor: alpha(theme.palette.grey[500], 0.12),
            }}
          >
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
              Project ID: {jsonData.project_id}<br/>
              Client ID: {jsonData.client_id}<br/>
              Type: {jsonData.type}<br/>
              {jsonData.client_email && `Service Account: ${jsonData.client_email}`}
            </Typography>
          </Paper>
        </Box>
      )}
    </Paper>
  );
};

export default BusinessOAuthSection;
