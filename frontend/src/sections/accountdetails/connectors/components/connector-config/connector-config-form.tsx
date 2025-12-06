import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
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
import FiltersSection from './filters-section';
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
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [showTopFade, setShowTopFade] = useState(false);
  const [showBottomFade, setShowBottomFade] = useState(false);
  
  // Check if connector is active - prevents saving while active
  const isConnectorActive = connector.isActive;
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

    // Business OAuth state (Google Workspace)
    adminEmail,
    adminEmailError,
    selectedFile,
    fileName,
    fileError,
    jsonData,

    // NEW: SharePoint Certificate OAuth state
    certificateFile,
    certificateFileName,
    certificateError,
    certificateData,
    privateKeyFile,
    privateKeyFileName,
    privateKeyError,
    privateKeyData,

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

    // NEW: SharePoint Certificate actions
    handleCertificateUpload,
    handleCertificateChange,
    handlePrivateKeyUpload,
    handlePrivateKeyChange,
    certificateInputRef,
    privateKeyInputRef,
  } = useConnectorConfig({ connector, onClose, onSuccess });

  // Handler for removing filters
  const handleRemoveFilter = useCallback(
    (section: string, fieldName: string) => {
      handleFieldChange(section, fieldName, undefined);
    },
    [handleFieldChange]
  );

  // Skip auth step if authType is 'NONE'
  const isNoAuthType = useMemo(() => isNoneAuthType(connector.authType), [connector.authType]);
  const hasFilters = useMemo(
    () => (connectorConfig?.config?.filters?.sync?.schema?.fields?.length ?? 0) > 0,
    [connectorConfig?.config?.filters?.sync?.schema?.fields?.length]
  );
  const steps = useMemo(
    () => isNoAuthType 
      ? (hasFilters ? ['Filters', 'Sync Settings'] : ['Sync Settings'])
      : (hasFilters ? ['Authentication', 'Filters', 'Sync Settings'] : ['Authentication', 'Sync Settings']),
    [isNoAuthType, hasFilters]
  );

  // Memoize fade gradients to avoid recalculation
  const topFadeGradient = useMemo(
    () => isDark
      ? 'linear-gradient(to bottom, rgba(18, 18, 23, 0.98), transparent)'
      : `linear-gradient(to bottom, ${theme.palette.background.paper}, transparent)`,
    [isDark, theme.palette.background.paper]
  );

  const bottomFadeGradient = useMemo(
    () => isDark
      ? 'linear-gradient(to top, rgba(18, 18, 23, 0.98), transparent)'
      : `linear-gradient(to top, ${theme.palette.background.paper}, transparent)`,
    [isDark, theme.palette.background.paper]
  );

  // Check scroll position to show/hide fade indicators with throttling
  const checkScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    
    const { scrollTop, scrollHeight, clientHeight } = container;
    const newShowTop = scrollTop > 10;
    const newShowBottom = scrollTop < scrollHeight - clientHeight - 10;
    
    // Batch state updates to avoid multiple re-renders
    setShowTopFade((prev) => prev !== newShowTop ? newShowTop : prev);
    setShowBottomFade((prev) => prev !== newShowBottom ? newShowBottom : prev);
  }, []);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return undefined;
    }

    checkScroll();
    
    // Throttle scroll events for better performance
    let ticking = false;
    const handleScroll = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          checkScroll();
          ticking = false;
        });
        ticking = true;
      }
    };

    // Throttle resize events
    let resizeTimeout: NodeJS.Timeout;
    const handleResize = () => {
      clearTimeout(resizeTimeout);
      resizeTimeout = setTimeout(checkScroll, 150);
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(container);

    return () => {
      container.removeEventListener('scroll', handleScroll);
      resizeObserver.disconnect();
      clearTimeout(resizeTimeout);
    };
  }, [activeStep, checkScroll]);

  const renderStepContent = useMemo(() => {
    if (isNoAuthType) {
      // For 'NONE' authType, show filters (if available) then sync step
      if (hasFilters) {
        switch (activeStep) {
          case 0:
            return (
              <FiltersSection
                connectorConfig={connectorConfig}
                formData={formData.filters}
                formErrors={formErrors.filters}
                onFieldChange={handleFieldChange}
                onRemoveFilter={handleRemoveFilter}
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
      }
      // No filters, only sync
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

    // With auth, show auth -> filters (if available) -> sync
    if (hasFilters) {
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
              
              // Google Workspace Business OAuth props
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
              
              // SharePoint Certificate OAuth props
              certificateFile={certificateFile}
              certificateFileName={certificateFileName}
              certificateError={certificateError}
              certificateData={certificateData}
              privateKeyFile={privateKeyFile}
              privateKeyFileName={privateKeyFileName}
              privateKeyError={privateKeyError}
              privateKeyData={privateKeyData}
              onCertificateUpload={handleCertificateUpload}
              onCertificateChange={handleCertificateChange}
              onPrivateKeyUpload={handlePrivateKeyUpload}
              onPrivateKeyChange={handlePrivateKeyChange}
              certificateInputRef={certificateInputRef}
              privateKeyInputRef={privateKeyInputRef}
              
              onFieldChange={handleFieldChange}
            />
          );
        case 1:
          return (
            <FiltersSection
              connectorConfig={connectorConfig}
              formData={formData.filters}
              formErrors={formErrors.filters}
              onFieldChange={handleFieldChange}
              onRemoveFilter={handleRemoveFilter}
            />
          );
        case 2:
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
    }

    // No filters, show auth -> sync
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
            
            // Google Workspace Business OAuth props
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
            
            // SharePoint Certificate OAuth props
            certificateFile={certificateFile}
            certificateFileName={certificateFileName}
            certificateError={certificateError}
            certificateData={certificateData}
            privateKeyFile={privateKeyFile}
            privateKeyFileName={privateKeyFileName}
            privateKeyError={privateKeyError}
            privateKeyData={privateKeyData}
            onCertificateUpload={handleCertificateUpload}
            onCertificateChange={handleCertificateChange}
            onPrivateKeyUpload={handlePrivateKeyUpload}
            onPrivateKeyChange={handlePrivateKeyChange}
            certificateInputRef={certificateInputRef}
            privateKeyInputRef={privateKeyInputRef}
            
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
  }, [
    isNoAuthType,
    hasFilters,
    activeStep,
    connectorConfig,
    formData,
    formErrors,
    handleFieldChange,
    handleRemoveFilter,
    saving,
    connector,
    conditionalDisplay,
    accountTypeLoading,
    isBusiness,
    adminEmail,
    adminEmailError,
    selectedFile,
    fileName,
    fileError,
    jsonData,
    handleAdminEmailChange,
    handleFileUpload,
    handleFileChange,
    fileInputRef,
    certificateFile,
    certificateFileName,
    certificateError,
    certificateData,
    privateKeyFile,
    privateKeyFileName,
    privateKeyError,
    privateKeyData,
    handleCertificateUpload,
    handleCertificateChange,
    handlePrivateKeyUpload,
    handlePrivateKeyChange,
    certificateInputRef,
    privateKeyInputRef,
  ]);

  if (loading) {
    return (
      <Dialog
        open={Boolean(true)}
        onClose={onClose}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            borderRadius: 2.5,
            boxShadow: isDark 
              ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
              : '0 20px 60px rgba(0, 0, 0, 0.12)',
          },
        }}
        slotProps={{
          backdrop: {
            sx: {
              backgroundColor: isDark ? 'rgba(0, 0, 0, 0.25)' : 'rgba(0, 0, 0, 0.5)',
            },
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
          borderRadius: 2.5,
          boxShadow: isDark 
            ? '0 24px 48px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255, 255, 255, 0.05)'
            : '0 20px 60px rgba(0, 0, 0, 0.12)',
          overflow: 'hidden',
          height: '85vh',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          border: isDark ? '1px solid rgba(255, 255, 255, 0.08)' : 'none',
        },
      }}
      slotProps={{
        backdrop: {
          sx: {
            backgroundColor: isDark ? 'rgba(0, 0, 0, 0.35)' : 'rgba(0, 0, 0, 0.5)',
          },
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          px: 3,
          py: 2.5,
          backgroundColor: 'transparent',
          flexShrink: 0,
          borderBottom: isDark 
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1.5,
              bgcolor: isDark 
                ? alpha(theme.palette.common.white, 0.08)
                : alpha(theme.palette.grey[100], 0.8),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: isDark ? `1px solid ${alpha(theme.palette.common.white, 0.1)}` : 'none',
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
              variant="h6"
              sx={{ 
                fontWeight: 600, 
                mb: 0.5, 
                color: theme.palette.text.primary,
                fontSize: '1.125rem',
                letterSpacing: '-0.01em',
              }}
            >
              Configure {connector.name}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Chip
                label={connector.appGroup}
                size="small"
                variant="outlined"
                sx={{
                  fontSize: '0.6875rem',
                  height: 20,
                  fontWeight: 500,
                  borderColor: isDark 
                    ? alpha(theme.palette.divider, 0.3)
                    : alpha(theme.palette.divider, 0.2),
                  bgcolor: isDark 
                    ? alpha(theme.palette.common.white, 0.05)
                    : 'transparent',
                  color: isDark 
                    ? alpha(theme.palette.text.primary, 0.9)
                    : theme.palette.text.secondary,
                  '& .MuiChip-label': { px: 1.25, py: 0 },
                }}
              />
              {!isNoneAuthType(connector.authType) && (
                <Chip
                  label={connector.authType.split('_').join(' ')}
                  size="small"
                  variant="outlined"
                  sx={{
                    fontSize: '0.6875rem',
                    height: 20,
                    fontWeight: 500,
                    borderColor: isDark 
                      ? alpha(theme.palette.divider, 0.3)
                      : alpha(theme.palette.divider, 0.2),
                    bgcolor: isDark 
                      ? alpha(theme.palette.common.white, 0.05)
                      : 'transparent',
                    color: isDark 
                      ? alpha(theme.palette.text.primary, 0.9)
                      : theme.palette.text.secondary,
                    '& .MuiChip-label': { px: 1.25, py: 0 },
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
            color: isDark 
              ? alpha(theme.palette.text.secondary, 0.8)
              : theme.palette.text.secondary,
            p: 1,
            '&:hover': {
              backgroundColor: isDark 
                ? alpha(theme.palette.common.white, 0.1)
                : alpha(theme.palette.text.secondary, 0.08),
              color: theme.palette.text.primary,
            },
            transition: 'all 0.2s ease',
          }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>

      <DialogContent 
        sx={{ 
          p: 0, 
          overflow: 'hidden', 
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          position: 'relative',
        }}
      >
        {saveError && (
          <Alert
            severity="error"
            sx={{
              mx: 2.5,
              mt: 2,
              mb: 0,
              borderRadius: 1.5,
              flexShrink: 0,
              bgcolor: isDark 
                ? alpha(theme.palette.error.main, 0.15)
                : undefined,
              border: isDark
                ? `1px solid ${alpha(theme.palette.error.main, 0.3)}`
                : 'none',
              alignItems: 'center',
            }}
          >
            <AlertTitle sx={{ fontWeight: 600, fontSize: '0.8125rem', mb: 0.25 }}>
              Configuration Error
            </AlertTitle>
            <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
              {saveError}
            </Typography>
          </Alert>
        )}

        {/* Top fade indicator */}
        {showTopFade && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 24,
              pointerEvents: 'none',
              zIndex: 1,
            }}
          />
        )}

        {/* Bottom fade indicator */}
        {showBottomFade && (
          <Box
            sx={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: 24,
              pointerEvents: 'none',
              zIndex: 1,
            }}
          />
        )}

        <Box 
          ref={scrollContainerRef}
          sx={{ 
            px: 2.5,
            py: 2,
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            overflow: 'auto',
            '&::-webkit-scrollbar': {
              width: '6px',
            },
            '&::-webkit-scrollbar-track': {
              backgroundColor: 'transparent',
            },
            '&::-webkit-scrollbar-thumb': {
              backgroundColor: isDark
                ? alpha(theme.palette.text.secondary, 0.25)
                : alpha(theme.palette.text.secondary, 0.16),
              borderRadius: '3px',
              '&:hover': {
                backgroundColor: isDark
                  ? alpha(theme.palette.text.secondary, 0.4)
                  : alpha(theme.palette.text.secondary, 0.24),
              },
            },
          }}
        >
          <Box sx={{ flexShrink: 0, mb: 2.5 }}>
            <ConfigStepper activeStep={activeStep} steps={steps} />
          </Box>
          <Box 
            sx={{ 
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              minHeight: 0,
            }}
          >
            {renderStepContent}
          </Box>
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          px: 2.5,
          py: 2,
          borderTop: isDark
            ? `1px solid ${alpha(theme.palette.divider, 0.12)}`
            : `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          flexShrink: 0,
          flexDirection: 'column',
          gap: 1.5,
          alignItems: 'stretch',
        }}
      >
        {/* Active Connector Notice - Subtle placement in footer */}
        {isConnectorActive && (
          <Box
            sx={{
              p: 1.25,
              borderRadius: 1,
              bgcolor: isDark 
                ? alpha(theme.palette.info.main, 0.06)
                : alpha(theme.palette.info.main, 0.03),
              border: `1px solid ${alpha(theme.palette.info.main, isDark ? 0.15 : 0.1)}`,
              display: 'flex',
              alignItems: 'center',
              gap: 1.25,
            }}
          >
            <Iconify 
              icon="mdi:lock-outline" 
              width={16} 
              color={theme.palette.info.main}
              sx={{ flexShrink: 0 }}
            />
            <Typography 
              variant="caption" 
              sx={{ 
                fontSize: '0.75rem',
                color: theme.palette.text.secondary,
                lineHeight: 1.4,
                fontWeight: 500,
              }}
            >
              Configuration is locked while connector is active. Disable the connector to make changes.
            </Typography>
          </Box>
        )}

        <Box sx={{ display: 'flex', gap: 1.5, width: '100%', justifyContent: 'flex-end' }}>
          <Button
            onClick={onClose}
            disabled={saving}
            variant="outlined"
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              px: 2.5,
              py: 0.625,
              borderRadius: 1,
              fontSize: '0.8125rem',
              borderColor: isDark
                ? alpha(theme.palette.divider, 0.3)
                : alpha(theme.palette.divider, 0.2),
              color: isDark
                ? alpha(theme.palette.text.secondary, 0.9)
                : theme.palette.text.secondary,
              '&:hover': {
                borderColor: isDark
                  ? alpha(theme.palette.text.secondary, 0.5)
                  : alpha(theme.palette.text.secondary, 0.4),
                backgroundColor: isDark
                  ? alpha(theme.palette.common.white, 0.08)
                  : alpha(theme.palette.text.secondary, 0.04),
              },
              transition: 'all 0.2s ease',
            }}
          >
            Cancel
          </Button>

          {activeStep > 0 && (
            <Button
              onClick={handleBack}
              disabled={saving}
              variant="outlined"
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                px: 2.5,
                py: 0.625,
                borderRadius: 1,
                fontSize: '0.8125rem',
                borderColor: isDark
                  ? alpha(theme.palette.primary.main, 0.3)
                  : alpha(theme.palette.primary.main, 0.2),
                color: theme.palette.primary.main,
                '&:hover': {
                  borderColor: theme.palette.primary.main,
                  backgroundColor: isDark
                    ? alpha(theme.palette.primary.main, 0.12)
                    : alpha(theme.palette.primary.main, 0.04),
                },
                transition: 'all 0.2s ease',
              }}
            >
              Back
            </Button>
          )}

          {activeStep < steps.length - 1 ? (
            <Button
              variant="contained"
              onClick={handleNext}
              disabled={saving}
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                px: 3,
                py: 0.625,
                borderRadius: 1,
                fontSize: '0.8125rem',
                boxShadow: isDark
                  ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}`
                  : 'none',
                '&:hover': {
                  boxShadow: isDark
                    ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                    : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                },
                '&:active': {
                  boxShadow: 'none',
                },
                transition: 'all 0.2s ease',
              }}
            >
              Next
            </Button>
          ) : (
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={saving || isConnectorActive}
              startIcon={
                saving ? (
                  <CircularProgress size={14} color="inherit" />
                ) : (
                  <Iconify icon={saveIcon} width={14} height={14} />
                )
              }
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                px: 3,
                py: 0.625,
                borderRadius: 1,
                fontSize: '0.8125rem',
                boxShadow: isDark
                  ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}`
                  : 'none',
                '&:hover': {
                  boxShadow: isDark
                    ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                    : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                },
                '&:active': {
                  boxShadow: 'none',
                },
                '&:disabled': {
                  boxShadow: 'none',
                  opacity: isConnectorActive ? 0.5 : 0.38,
                },
                transition: 'all 0.2s ease',
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