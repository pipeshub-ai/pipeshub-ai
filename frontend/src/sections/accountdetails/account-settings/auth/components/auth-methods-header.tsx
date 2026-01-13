import React from 'react';
import editIcon from '@iconify-icons/eva/edit-outline';
import checkMarkIcon from '@iconify-icons/eva/checkmark-outline';

import { useTheme } from '@mui/material/styles';
import { Box, Button, Typography, CircularProgress } from '@mui/material';

import { Iconify } from 'src/components/iconify';

interface AuthMethodsHeaderProps {
  authMethods: Array<{
    type: string;
    enabled: boolean;
  }>;
  isEditing: boolean;
  setIsEditing: (isEditing: boolean) => void;
  handleSaveChanges: () => void;
  handleCancelEdit: () => void;
  isLoading: boolean;
}

const AuthMethodsHeader: React.FC<AuthMethodsHeaderProps> = ({
  authMethods,
  isEditing,
  setIsEditing,
  handleSaveChanges,
  handleCancelEdit,
  isLoading,
}) => {
  const theme = useTheme();

  // Get the enabled method
  const enabledMethod = authMethods.find((m) => m.enabled);

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        mb: 3,
        pb: 2,
        borderBottom: '1px solid',
        borderColor: theme.palette.divider,
      }}
    >
      <Box>
        {enabledMethod && (
          <Typography variant="body2" color="text.secondary">
            Active: {getMethodName(enabledMethod.type)}
          </Typography>
        )}
      </Box>

      <Box sx={{ display: 'flex', gap: 1 }}>
        {!isEditing ? (
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon={editIcon} />}
            onClick={() => setIsEditing(true)}
            disabled={isLoading}
            sx={{
              borderRadius: 1,
              borderColor: theme.palette.divider,
              color: theme.palette.text.primary,
              '&:hover': {
                borderColor: theme.palette.text.primary,
              },
            }}
          >
            Edit
          </Button>
        ) : (
          <>
            <Button
              variant="text"
              size="small"
              onClick={handleCancelEdit}
              disabled={isLoading}
              sx={{
                color: theme.palette.text.secondary,
              }}
            >
              Cancel
            </Button>
            <Button
              variant="outlined"
              size="small"
              color="primary"
              onClick={handleSaveChanges}
              disabled={isLoading}
              startIcon={
                isLoading ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <Iconify icon={checkMarkIcon} />
                )
              }
              sx={{
                borderRadius: 1,
                fontWeight: 500,
              }}
            >
              Save
            </Button>
          </>
        )}
      </Box>
    </Box>
  );
};

// Helper function to get method display name
const getMethodName = (type: string): string => {
  const methodNames: Record<string, string> = {
    password: 'Password',
    otp: 'One-Time Password',
    google: 'Google',
    microsoft: 'Microsoft',
    azureAd: 'Azure AD', // Kept for backward compatibility
    samlSso: 'SAML SSO',
    oauth: 'OAuth',
  };

  return methodNames[type] || type;
};

export default AuthMethodsHeader;
