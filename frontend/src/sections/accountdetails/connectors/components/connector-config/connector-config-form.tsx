import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogActions,
  Typography,
  Box,
  Button,
  Alert,
  AlertTitle,
  CircularProgress,
  alpha,
  useTheme,
  IconButton,
  Chip,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import { useAccountType } from 'src/hooks/use-account-type';
import closeIcon from '@iconify-icons/mdi/close';
import saveIcon from '@iconify-icons/eva/save-outline';
import { useConnectorConfig } from '../../hooks/use-connector-config';
import AuthSection from './auth-section';
import SyncSection from './sync-section';
import ConfigStepper from './config-stepper';
import { Connector } from '../../types/types';
import { isNoneAuthType } from '../../utils/auth';

interface ConnectorConfigFormProps {
  connector: Connector;
  onClose: () => void;
  onSuccess?: () => void;
}

const ConnectorConfigForm: React.FC<ConnectorConfigFormProps> = ({
  connector,
  onClose,
  onSuccess,
}) => {
  const theme = useTheme();
  const { isBusiness, isIndividual, loading: accountTypeLoading } = useAccountType();
  const isDark = theme.palette.mode === 'dark';
  const {
    // State
    connectorConfig,
    loading,
    saving,
    activeStep,
    formData,
    formErrors,
    saveError,
    conditionalDisplay,

    // Business OAuth state
    adminEmail,
    adminEmailError,
    selectedFile,
    fileName,
    fileError,
    jsonData,

    // Actions
    handleFieldChange,
    handleNext,
    handleBack,
    handleSave,
    handleFileSelect,
    handleFileUpload,
    handleFileChange,
    handleAdminEmailChange,
    validateAdminEmail,
    isBusinessGoogleOAuthValid,
    fileInputRef,
  } = useConnectorConfig({ connector, onClose, onSuccess });

  // Skip auth step if authType is 'NONE'
  const isNoAuthType = isNoneAuthType(connector.authType);
  const steps = isNoAuthType ? ['Sync Settings'] : ['Authentication', 'Sync Settings'];

  const renderStepContent = () => {
    if (isNoAuthType) {
      // For 'NONE' authType, only show sync step
      return (
        <SyncSection
          connectorConfig={connectorConfig}
          formData={formData.sync}
          formErrors={formErrors.sync}
          onFieldChange={handleFieldChange}
          saving={saving}
        />
      );
    }

    switch (activeStep) {
      case 0:
        return (
          <AuthSection
            connector={connector}
            connectorConfig={connectorConfig}
            formData={formData.auth}
            formErrors={formErrors.auth}
            conditionalDisplay={conditionalDisplay}
            accountTypeLoading={accountTypeLoading}
            isBusiness={isBusiness}
            adminEmail={adminEmail}
            adminEmailError={adminEmailError}
            selectedFile={selectedFile}
            fileName={fileName}
            fileError={fileError}
            jsonData={jsonData}
            onAdminEmailChange={handleAdminEmailChange}
            onFileUpload={handleFileUpload}
            onFileChange={handleFileChange}
            fileInputRef={fileInputRef}
            onFieldChange={handleFieldChange}
          />
        );
      case 1:
        return (
          <SyncSection
            connectorConfig={connectorConfig}
            formData={formData.sync}
            formErrors={formErrors.sync}
            onFieldChange={handleFieldChange}
            saving={saving}
          />
        );
      default:
        return null;
    }
  };

  if (loading) {
    return (
      <Dialog
        open={Boolean(true)}
        onClose={onClose}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2,
            boxShadow: '0 20px 60px rgba(0, 0, 0, 0.08)',
          },
        }}
      >
        <DialogContent
          sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}
        >
          <CircularProgress size={32} />
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog
      open={Boolean(true)}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          boxShadow: '0 20px 60px rgba(0, 0, 0, 0.08)',
          overflow: 'hidden',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          p: 3,
          pb: 0,
          backgroundColor: 'transparent',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Box
            sx={{
              p: 1,
              borderRadius: 1.5,
              bgcolor: isDark ? alpha(theme.palette.common.white, 0.9) : alpha(theme.palette.grey[100], 0.8),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >

            <img
              src={connector.iconPath}
              alt={connector.name}
              width={32}
              height={32}
              style={{ objectFit: 'contain' }}
              onError={(e: React.SyntheticEvent<HTMLImageElement>) => {
                const target = e.target as HTMLImageElement;
                target.src = '/assets/icons/connectors/default.svg';
              }}
            />
          </Box>
          <Box>
            <Typography
              variant="h5"
              sx={{ fontWeight: 700, mb: 0.25, color: theme.palette.text.primary }}
            >
              Configure {connector.name}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Chip
                label={connector.appGroup}
                size="small"
                variant="outlined"
                sx={{
                  fontSize: '0.75rem',
                  height: 20,
                  '& .MuiChip-label': { px: 1 },
                }}
              />
              {!isNoneAuthType(connector.authType) && (
                <Chip
                  label={connector.authType.split('_').join(' ')}
                  size="small"
                  variant="outlined"
                  sx={{
                    fontSize: '0.75rem',
                    height: 20,
                    '& .MuiChip-label': { px: 1 },
                  }}
                />
              )}
            </Box>
          </Box>
        </Box>

        <IconButton
          onClick={onClose}
          size="small"
          sx={{
            color: theme.palette.text.secondary,
            p: 1,
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.secondary, 0.08),
            },
          }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ p: 0, overflow: 'auto', flex: 1 }}>
        {saveError && (
          <Alert
            severity="error"
            sx={{
              m: 3,
              mb: 0,
              borderRadius: 1.5,
            }}
          >
            <AlertTitle sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
              Configuration Error
            </AlertTitle>
            <Typography variant="body2" sx={{ fontSize: '0.8125rem' }}>
              {saveError}
            </Typography>
          </Alert>
        )}

        <Box sx={{ p: 3 }}>
          <ConfigStepper activeStep={activeStep} steps={steps} />
          {renderStepContent()}
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          p: 2.5,
          pt: 2,
          borderTop: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
          backgroundColor: alpha(theme.palette.background.default, 0.3),
          backdropFilter: 'blur(8px)',
          flexShrink: 0,
        }}
      >
        <Box sx={{ display: 'flex', gap: 1.5, width: '100%', justifyContent: 'flex-end' }}>
          <Button
            onClick={onClose}
            disabled={saving}
            variant="outlined"
            sx={{
              textTransform: 'none',
              fontWeight: 600,
              px: 3,
              py: 0.75,
              borderRadius: 1.5,
              fontSize: '0.875rem',
              borderColor: alpha(theme.palette.divider, 0.3),
              color: theme.palette.text.secondary,
              '&:hover': {
                borderColor: theme.palette.text.secondary,
                backgroundColor: alpha(theme.palette.text.secondary, 0.04),
              },
            }}
          >
            Cancel
          </Button>

          {!isNoAuthType && activeStep > 0 && (
            <Button
              onClick={handleBack}
              disabled={saving}
              variant="outlined"
              sx={{
                textTransform: 'none',
                fontWeight: 600,
                px: 3,
                py: 0.75,
                borderRadius: 1.5,
                fontSize: '0.875rem',
                borderColor: alpha(theme.palette.primary.main, 0.3),
                color: theme.palette.primary.main,
                '&:hover': {
                  borderColor: theme.palette.primary.main,
                  backgroundColor: alpha(theme.palette.primary.main, 0.04),
                },
              }}
            >
              Back
            </Button>
          )}

          {!isNoAuthType && activeStep < 1 ? (
            <Button
              variant="contained"
              onClick={handleNext}
              disabled={saving}
              sx={{
                textTransform: 'none',
                fontWeight: 600,
                px: 4,
                py: 0.75,
                borderRadius: 1.5,
                fontSize: '0.875rem',
                boxShadow: `0 2px 8px ${alpha(theme.palette.primary.main, 0.25)}`,
                '&:hover': {
                  boxShadow: `0 4px 12px ${alpha(theme.palette.primary.main, 0.35)}`,
                  transform: 'translateY(-1px)',
                },
                '&:active': {
                  transform: 'translateY(0)',
                },
                transition: 'all 0.15s ease-in-out',
              }}
            >
              Next
            </Button>
          ) : (
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={saving}
              startIcon={
                saving ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <Iconify icon={saveIcon} width={16} height={16} />
                )
              }
              sx={{
                textTransform: 'none',
                fontWeight: 600,
                px: 4,
                py: 0.75,
                borderRadius: 1.5,
                fontSize: '0.875rem',
                boxShadow: `0 2px 8px ${alpha(theme.palette.primary.main, 0.25)}`,
                '&:hover': {
                  boxShadow: `0 4px 12px ${alpha(theme.palette.primary.main, 0.35)}`,
                  transform: 'translateY(-1px)',
                },
                '&:active': {
                  transform: 'translateY(0)',
                },
                '&:disabled': {
                  transform: 'none',
                },
                transition: 'all 0.15s ease-in-out',
              }}
            >
              {saving ? 'Saving...' : 'Save Configuration'}
            </Button>
          )}
        </Box>
      </DialogActions>
    </Dialog>
  );
};

export default ConnectorConfigForm;
