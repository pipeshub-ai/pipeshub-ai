import type { Icon as IconifyIcon } from '@iconify/react';

import { useNavigate } from 'react-router-dom';
import smtpIcon from '@iconify-icons/eva/email-outline';
import samlIcon from '@iconify-icons/eva/shield-outline';
import closeIcon from '@iconify-icons/eva/close-outline';
import React, { useRef, useState, useEffect } from 'react';
import googleIcon from '@iconify-icons/eva/google-outline';
import azureIcon from '@iconify-icons/mdi/microsoft-azure';
import microsoftIcon from '@iconify-icons/ri/microsoft-line';

import {
  Box,
  alpha,
  Dialog,
  Button,
  useTheme,
  IconButton,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';

import OAuthAuthForm from './oauth-auth-form';
import GoogleAuthForm from './google-auth-form';
import SmtpConfigForm from './smtp-config-form';
import AzureAdAuthForm from './azureAd-auth-form';
import MicrosoftAuthForm from './microsoft-auth-form';

// Import configuration forms
import type { GoogleAuthFormRef } from './google-auth-form';
import type { SmtpConfigFormRef } from './smtp-config-form';
import type { AzureAdAuthFormRef } from './azureAd-auth-form';
import type { MicrosoftAuthFormRef } from './microsoft-auth-form';

// Method configurations
interface MethodConfigType {
  [key: string]: {
    icon: React.ComponentProps<typeof IconifyIcon>['icon'];
    title: string;
    color: string;
  };
}

const METHOD_CONFIG: MethodConfigType = {
  google: {
    icon: googleIcon,
    title: 'Google',
    color: '#4285F4',
  },
  microsoft: {
    icon: microsoftIcon,
    title: 'Microsoft',
    color: '#00A4EF',
  },
  azureAd: {
    icon: azureIcon,
    title: 'Azure AD',
    color: '#0078D4',
  },
  samlSso: {
    icon: samlIcon,
    title: 'SAML SSO',
    color: '#FF6500',
  },
  oauth: {
    icon: 'mdi:key-variant',
    title: 'OAuth',
    color: '#6366F1',
  },
  smtp: {
    icon: smtpIcon,
    title: 'SMTP',
    color: '#2E7D32',
  },
};

interface ConfigureMethodDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: () => void;
  methodType: string | null;
}

// Create a type for any form ref that has a handleSave method
type AnyFormRef = {
  handleSave: () => Promise<any>;
};

const ConfigureMethodDialog: React.FC<ConfigureMethodDialogProps> = ({
  open,
  onClose,
  onSave,
  methodType,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [isValid, setIsValid] = useState(false);
  const [showOAuthForm, setShowOAuthForm] = useState(false);
  const { isAdmin } = useAdmin();
  const { user } = useAuthContext();
  const accountType = user?.accountType;

  // Using a more flexible ref approach
  const googleFormRef = useRef<GoogleAuthFormRef>(null);
  const microsoftFormRef = useRef<MicrosoftAuthFormRef>(null);
  const azureAdFormRef = useRef<AzureAdAuthFormRef>(null);
  const smtpFormRef = useRef<SmtpConfigFormRef>(null);

  // Get method config if available
  const methodConfig = methodType ? METHOD_CONFIG[methodType] : null;

  // Handle special cases - SAML SSO and OAuth
  useEffect(() => {
    if (open && methodType === 'samlSso') {
      // Close dialog and navigate to SAML config page
      onClose();
      if (isAdmin && accountType === 'business') {
        navigate('/account/company-settings/settings/authentication/saml');
        return;
      }
      if (isAdmin && accountType === 'individual') {
        navigate('/account/individual/settings/authentication/config-saml');
        return;
      }
      navigate('/');
    }
    
    if (open && methodType === 'oauth') {
      setShowOAuthForm(true);
    } else {
      setShowOAuthForm(false);
    }
  }, [open, methodType, navigate, onClose, accountType, isAdmin]);

  // If method type is samlSso, don't render the dialog
  if (methodType === 'samlSso') {
    return null;
  }

  // Form validation state
  const handleValidationChange = (valid: boolean) => {
    setIsValid(valid);
  };

  // Handle save button click - triggers the form's save method based on the active method type
  const handleSaveClick = async () => {
    let currentRef: React.RefObject<AnyFormRef> | null = null;

    // Determine which form ref to use based on method type
    switch (methodType) {
      case 'google':
        currentRef = googleFormRef;
        break;
      case 'microsoft':
        currentRef = microsoftFormRef;
        break;
      case 'azureAd':
        currentRef = azureAdFormRef;
        break;
      case 'smtp':
        currentRef = smtpFormRef;
        break;
      default:
        currentRef = null;
    }

    // If we have a valid ref with handleSave method
    if (currentRef?.current?.handleSave) {
      const result = await currentRef.current.handleSave();
      // Don't call onSave if the result is explicitly false
      if (result !== false) {
        onSave();
      }
    }
  };

  // Handle successful save in forms
  const handleFormSaveSuccess = () => {
    onSave();
  };

  // Handle OAuth form success
  const handleOAuthSuccess = () => {
    setShowOAuthForm(false);
    onSave();
  };

  // Handle OAuth form close
  const handleOAuthClose = () => {
    setShowOAuthForm(false);
    onClose();
  };

  return (
    <>
      {/* OAuth form as separate dialog */}
      <OAuthAuthForm
        open={showOAuthForm}
        onClose={handleOAuthClose}
        onSuccess={handleOAuthSuccess}
      />
      
      <Dialog
        open={open && methodType !== 'oauth'}
        onClose={onClose}
        maxWidth="md"
        fullWidth
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 1,
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
            overflow: 'hidden',
          },
        }}
      >
      {methodConfig && (
        <>
          <DialogTitle
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              p: 2.5,
              pl: 3,
              color: theme.palette.text.primary,
              borderBottom: '1px solid',
              borderColor: theme.palette.divider,
              fontWeight: 500,
              fontSize: '1rem',
              m: 0,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 32,
                  height: 32,
                  borderRadius: '6px',
                  bgcolor: alpha(methodConfig.color, 0.1),
                  color: methodConfig.color,
                }}
              >
                <Iconify icon={methodConfig.icon} width={18} height={18} />
              </Box>
              Configure {methodConfig.title} {methodType !== 'smtp' ? 'Authentication' : 'Settings'}
            </Box>

            <IconButton
              onClick={onClose}
              size="small"
              sx={{ color: theme.palette.text.secondary }}
              aria-label="close"
            >
              <Iconify icon={closeIcon} width={20} height={20} />
            </IconButton>
          </DialogTitle>

          <DialogContent
            sx={{
              p: 0,
              '&.MuiDialogContent-root': {
                pt: 3,
                px: 3,
                pb: 0,
              },
            }}
          >
            <Box>
              {methodType === 'google' && (
                <GoogleAuthForm
                  onValidationChange={handleValidationChange}
                  onSaveSuccess={handleFormSaveSuccess}
                  ref={googleFormRef}
                />
              )}

              {methodType === 'microsoft' && (
                <MicrosoftAuthForm
                  onValidationChange={handleValidationChange}
                  onSaveSuccess={handleFormSaveSuccess}
                  ref={microsoftFormRef}
                />
              )}

              {methodType === 'azureAd' && (
                <AzureAdAuthForm
                  onValidationChange={handleValidationChange}
                  onSaveSuccess={handleFormSaveSuccess}
                  ref={azureAdFormRef}
                />
              )}

              {methodType === 'smtp' && (
                <SmtpConfigForm
                  onValidationChange={handleValidationChange}
                  onSaveSuccess={handleFormSaveSuccess}
                  ref={smtpFormRef}
                />
              )}
            </Box>
          </DialogContent>

          <DialogActions
            sx={{
              p: 2.5,
              borderTop: '1px solid',
              borderColor: theme.palette.divider,
              bgcolor: alpha(theme.palette.background.default, 0.5),
            }}
          >
            <Button
              variant="text"
              onClick={onClose}
              sx={{
                color: theme.palette.text.secondary,
                fontWeight: 500,
                '&:hover': {
                  backgroundColor: alpha(theme.palette.divider, 0.8),
                },
              }}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleSaveClick}
              disabled={!isValid}
              sx={{
                bgcolor: theme.palette.primary.main,
                boxShadow: 'none',
                fontWeight: 500,
                '&:hover': {
                  bgcolor: theme.palette.primary.dark,
                  boxShadow: 'none',
                },
                px: 3,
              }}
            >
              Save
            </Button>
          </DialogActions>
        </>
      )}
      </Dialog>
    </>
  );
};

export default ConfigureMethodDialog;
