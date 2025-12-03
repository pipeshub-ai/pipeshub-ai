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
import checkCircleIcon from '@iconify-icons/mdi/check-circle';
import uploadIcon from '@iconify-icons/mdi/upload';
import fileDocumentIcon from '@iconify-icons/mdi/file-document-outline';

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
            icon={keyIcon}
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
            Business OAuth Configuration
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
            Required for Google Workspace business accounts
          </Typography>
        </Box>
      </Box>

      {/* Admin Email Field */}
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
          Admin Email Address
        </Typography>
        <TextField
          fullWidth
          size="small"
          value={adminEmail}
          onChange={(e) => onAdminEmailChange(e.target.value)}
          error={!!adminEmailError}
          helperText={adminEmailError || 'Google Workspace administrator email'}
          placeholder="admin@yourdomain.com"
          sx={{
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
      </Box>

      {/* JSON File Upload */}
      <Box>
        <Typography 
          variant="subtitle2" 
          sx={{ 
            fontSize: '0.875rem', 
            fontWeight: 500,
            mb: 1,
            color: theme.palette.text.primary,
          }}
        >
          Service Account Credentials
        </Typography>
        
        <Paper
          variant="outlined"
          onClick={onFileUpload}
          sx={{
            p: 2,
            borderRadius: 1.25,
            borderWidth: '1.5px',
            borderStyle: (selectedFile || jsonData) ? 'solid' : 'dashed',
            borderColor: (selectedFile || jsonData)
              ? alpha(theme.palette.success.main, 0.3)
              : alpha(theme.palette.divider, 0.3),
            bgcolor: (selectedFile || jsonData)
              ? alpha(theme.palette.success.main, 0.04)
              : 'transparent',
            cursor: 'pointer',
            transition: 'all 0.2s',
            '&:hover': {
              borderColor: (selectedFile || jsonData)
                ? alpha(theme.palette.success.main, 0.5)
                : alpha(theme.palette.primary.main, 0.4),
              bgcolor: (selectedFile || jsonData)
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
                bgcolor: (selectedFile || jsonData)
                  ? alpha(theme.palette.success.main, 0.12)
                  : alpha(theme.palette.primary.main, 0.08),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon={(selectedFile || jsonData) ? checkCircleIcon : fileDocumentIcon}
                width={18}
                sx={{
                  color: (selectedFile || jsonData) 
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
                {fileName || 'No file selected'}
              </Typography>
              <Typography 
                variant="caption" 
                sx={{ 
                  fontSize: '0.75rem',
                  color: theme.palette.text.secondary,
                }}
              >
                {(selectedFile || jsonData)
                  ? 'Credentials file loaded successfully'
                  : 'Click to upload JSON credentials file'
                }
              </Typography>
            </Box>
            
            <Button
              variant="outlined"
              size="small"
              startIcon={<Iconify icon={uploadIcon} width={14} />}
              onClick={(e) => {
                e.stopPropagation();
                onFileUpload();
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
              {selectedFile ? 'Replace' : 'Upload'}
            </Button>
          </Box>
        </Paper>

        {fileError && (
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
          <Typography 
            variant="subtitle2" 
            sx={{ 
              fontSize: '0.875rem', 
              fontWeight: 500,
              mb: 1,
              color: theme.palette.text.primary,
            }}
          >
            Credentials Preview
          </Typography>
          <Paper
            variant="outlined"
            sx={{
              p: 1.5,
              borderRadius: 1.25,
              bgcolor: alpha(theme.palette.grey[500], 0.04),
              borderColor: alpha(theme.palette.divider, 0.1),
            }}
          >
            <Box 
              component="pre"
              sx={{
                m: 0,
                fontFamily: '"Roboto Mono", "Courier New", monospace',
                fontSize: '0.75rem',
                lineHeight: 1.6,
                color: theme.palette.text.secondary,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              <Box component="span" sx={{ color: theme.palette.text.disabled }}>Project ID:</Box> {jsonData.project_id}{'\n'}
              <Box component="span" sx={{ color: theme.palette.text.disabled }}>Client ID:</Box> {jsonData.client_id}{'\n'}
              <Box component="span" sx={{ color: theme.palette.text.disabled }}>Type:</Box> {jsonData.type}
              {jsonData.client_email && (
                <>
                  {'\n'}
                  <Box component="span" sx={{ color: theme.palette.text.disabled }}>Service Account:</Box> {jsonData.client_email}
                </>
              )}
            </Box>
          </Paper>
        </Box>
      )}
    </Paper>
  );
};

export default BusinessOAuthSection;