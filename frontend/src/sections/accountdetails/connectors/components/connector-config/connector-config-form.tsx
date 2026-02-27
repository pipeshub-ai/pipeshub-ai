import React, { useState, useEffect, useRef, useMemo, useCallback, createRef } from 'react';
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
  Stack,
} from '@mui/material';
import { ConfirmDialog } from 'src/components/custom-dialog';
import { Iconify } from 'src/components/iconify';
import { useAccountType } from 'src/hooks/use-account-type';
import closeIcon from '@iconify-icons/mdi/close';
import saveIcon from '@iconify-icons/eva/save-outline';
import { useConnectorConfig } from '../../hooks/use-connector-config';
import { useErrorAutoscroll } from '../../hooks/use-error-autoscroll';
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
  initialInstanceName?: string;
  enableMode?: boolean; // If true, opened from toggle - show filters and sync, then enable
  authOnly?: boolean; // If true, show only auth section
  syncOnly?: boolean; // If true, show only filters and sync (when connector is active) - DEPRECATED
  syncSettingsMode?: boolean; // If true, opened from Sync Settings button - only filters, never toggle
}

const ConnectorConfigForm: React.FC<ConnectorConfigFormProps> = ({
  connector,
  onClose,
  onSuccess,
  initialInstanceName,
  enableMode = false,
  authOnly = false,
  syncOnly = false,
  syncSettingsMode = false,
}) => {
  const theme = useTheme();
  const { isBusiness, isIndividual, loading: accountTypeLoading } = useAccountType();
  const isDark = theme.palette.mode === 'dark';
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [showTopFade, setShowTopFade] = useState(false);
  const [showBottomFade, setShowBottomFade] = useState(false);
  const [confirmSyncOpen, setConfirmSyncOpen] = useState(false);
  
  // Refs for scrolling to error sections
  const authSectionRef = createRef<HTMLDivElement>();
  const filtersSectionRef = createRef<HTMLDivElement>();
  const syncSectionRef = createRef<HTMLDivElement>();
  const sharepointSectionRef = createRef<HTMLDivElement>();
  const businessOAuthSectionRef = createRef<HTMLDivElement>();

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
    saveAttempted,

    // Business OAuth state (Google Workspace)
    isCreateMode,
    instanceName,
    instanceNameError,

    // Business OAuth state
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
    setInstanceName,
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
    
    // Auth type selection
    selectedAuthType,
    handleAuthTypeChange,
  } = useConnectorConfig({ connector, onClose, onSuccess, initialInstanceName, enableMode, authOnly, syncOnly, syncSettingsMode });

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
    () => {
      const syncFields = connectorConfig?.config?.filters?.sync?.schema?.fields?.length ?? 0;
      const indexingFields = connectorConfig?.config?.filters?.indexing?.schema?.fields?.length ?? 0;
      return syncFields > 0 || indexingFields > 0;
    },
    [
      connectorConfig?.config?.filters?.sync?.schema?.fields?.length,
      connectorConfig?.config?.filters?.indexing?.schema?.fields?.length
    ]
  );

  // True if user has selected at least one *sync* filter with a value (indexing filters are not considered – only sync filters affect what gets synced)
  const hasAnySyncFiltersSelected = useMemo(() => {
    if (!connectorConfig?.config?.filters || !formData.filters) return false;
    const hasMeaningfulValue = (
      field: { name: string; filterType?: string },
      f: { operator?: string; value?: unknown }
    ): boolean => {
      if (!f?.operator) return false;
      if (field.filterType === 'boolean') return true;
      if (Array.isArray(f.value)) return f.value.length > 0;
      // Datetime: value is { start, end } – only count if at least one is non-empty
      if (field.filterType === 'datetime' && f.value && typeof f.value === 'object' && !Array.isArray(f.value)) {
        const d = f.value as { start?: string; end?: string };
        return (d.start != null && d.start !== '') || (d.end != null && d.end !== '');
      }
      return f.value !== undefined && f.value !== null && f.value !== '';
    };
    const checkFields = (fields: { name: string; filterType?: string }[] | undefined) => {
      if (!fields || fields.length === 0) return false;
      return fields.some((field) => {
        const f = formData.filters[field.name];
        return f && hasMeaningfulValue(field, f);
      });
    };
    const syncFields = connectorConfig.config.filters.sync?.schema?.fields;
    return checkFields(syncFields);
  }, [connectorConfig?.config?.filters, formData.filters]);

  // Manual indexing enabled = user turned ON "enable_manual_sync" in Filters (indexing filters)
  const isManualIndexingEnabled = useMemo(
    () => formData.filters?.enable_manual_sync?.value === true,
    [formData.filters?.enable_manual_sync?.value]
  );

  const steps = useMemo(
    () => {
      // Auth only mode: only authentication
      if (authOnly) {
        return ['Authentication'];
      }
      // Sync Settings mode: filters (if available) and sync settings (always shown)
      // This mode is for viewing sync settings (filters + sync) - never toggle, view-only
      if (syncSettingsMode) {
        return hasFilters ? ['Filters', 'Sync Settings'] : ['Sync Settings'];
      }
      // Enable mode or sync only mode: filters and sync (skip auth)
      if (enableMode || syncOnly) {
        return hasFilters ? ['Filters', 'Sync Settings'] : ['Sync Settings'];
      }
      // Create mode: only auth (skip filters and sync)
      if (isCreateMode) {
        return ['Authentication'];
      }
      // Edit mode: show all steps based on auth type and filters
      return isNoAuthType
        ? hasFilters
          ? ['Filters', 'Sync Settings']
          : ['Sync Settings']
        : hasFilters
          ? ['Authentication', 'Filters', 'Sync Settings']
          : ['Authentication', 'Sync Settings'];
    },
    [authOnly, syncSettingsMode, enableMode, syncOnly, isCreateMode, isNoAuthType, hasFilters]
  );

  // Ensure activeStep doesn't exceed steps length
  useEffect(() => {
    if (activeStep >= steps.length) {
      // This will be handled by the hook, but we can add a safeguard here if needed
    }
  }, [activeStep, steps.length]);

  // Memoize fade gradients to avoid recalculation
  const topFadeGradient = useMemo(
    () =>
      isDark
        ? 'linear-gradient(to bottom, rgba(18, 18, 23, 0.98), transparent)'
        : `linear-gradient(to bottom, ${theme.palette.background.paper}, transparent)`,
    [isDark, theme.palette.background.paper]
  );

  const bottomFadeGradient = useMemo(
    () =>
      isDark
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
    setShowTopFade((prev) => (prev !== newShowTop ? newShowTop : prev));
    setShowBottomFade((prev) => (prev !== newShowBottom ? newShowBottom : prev));
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

  // Auto-scroll to first validation error when Save is clicked
  useErrorAutoscroll({
    saveAttempted,
    formErrors,
    instanceNameError,
    adminEmailError,
    fileError,
    certificateError,
    privateKeyError,
    activeStep,
    authOnly,
    syncSettingsMode,
    enableMode,
    syncOnly,
    isNoAuthType,
    hasFilters,
    connector,
    isBusiness,
    scrollContainerRef,
    authSectionRef,
    filtersSectionRef,
    syncSectionRef,
    sharepointSectionRef,
    businessOAuthSectionRef,
  });

  const renderStepContent = useCallback(() => {
    // Auth only mode: show only authentication
    if (authOnly) {
      return (
        <AuthSection
          ref={authSectionRef}
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
          isCreateMode={isCreateMode}
          instanceName={instanceName}
          instanceNameError={instanceNameError}
          onInstanceNameChange={setInstanceName}
          saveAttempted={saveAttempted}
          selectedAuthType={selectedAuthType}
          handleAuthTypeChange={handleAuthTypeChange}
          sharepointSectionRef={sharepointSectionRef}
          businessOAuthSectionRef={businessOAuthSectionRef}
        />
      );
    }
    // Sync Settings mode: show filters (if available) and sync settings (always shown)
    // This mode allows editing filters and sync settings
    if (syncSettingsMode) {
      // If filters are available, show them as the first step
      if (hasFilters) {
        switch (activeStep) {
          case 0:
            return (
              <FiltersSection
                ref={filtersSectionRef}
                connectorConfig={connectorConfig}
                formData={formData.filters}
                formErrors={formErrors.filters}
                onFieldChange={handleFieldChange}
                onRemoveFilter={handleRemoveFilter}
                connectorId={connector._key}
              />
            );
          case 1:
            return (
              <SyncSection
                ref={syncSectionRef}
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
      // If no filters, show sync settings as the only step
      return (
        <SyncSection
          ref={syncSectionRef}
          connectorConfig={connectorConfig}
          formData={formData.sync}
          formErrors={formErrors.sync}
          onFieldChange={handleFieldChange}
          saving={saving}
        />
      );
    }
    // Enable mode or sync only mode: show filters and sync only (skip auth)
    if (enableMode || syncOnly) {
      if (hasFilters) {
        switch (activeStep) {
          case 0:
            return (
              <FiltersSection
                ref={filtersSectionRef}
                connectorConfig={connectorConfig}
                formData={formData.filters}
                formErrors={formErrors.filters}
                onFieldChange={handleFieldChange}
                onRemoveFilter={handleRemoveFilter}
                connectorId={connector._key}
              />
            );
          case 1:
            return (
              <SyncSection
                ref={syncSectionRef}
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
          ref={syncSectionRef}
          connectorConfig={connectorConfig}
          formData={formData.sync}
          formErrors={formErrors.sync}
          onFieldChange={handleFieldChange}
          saving={saving}
        />
      );
    }

    // Create mode: show auth only
    if (isCreateMode) {
      return (
        <AuthSection
          ref={authSectionRef}
          connector={connector}
          connectorConfig={connectorConfig}
          formData={formData.auth}
          formErrors={formErrors.auth}
          conditionalDisplay={conditionalDisplay}
          accountTypeLoading={accountTypeLoading}
          isBusiness={isBusiness}
          isCreateMode={isCreateMode}
          instanceName={instanceName || ''}
          instanceNameError={instanceNameError}
          onInstanceNameChange={setInstanceName}
          selectedAuthType={selectedAuthType}
          handleAuthTypeChange={handleAuthTypeChange}
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
          saveAttempted={saveAttempted}
          sharepointSectionRef={sharepointSectionRef}
          businessOAuthSectionRef={businessOAuthSectionRef}
        />
      );
    }

    // Edit mode: show all steps based on auth type
    if (isNoAuthType) {
      // For 'NONE' authType, show filters (if available) then sync step
      if (hasFilters) {
        switch (activeStep) {
          case 0:
            return (
              <FiltersSection
                ref={filtersSectionRef}
                connectorConfig={connectorConfig}
                formData={formData.filters}
                formErrors={formErrors.filters}
                onFieldChange={handleFieldChange}
                onRemoveFilter={handleRemoveFilter}
                connectorId={connector._key}
              />
            );
          case 1:
            return (
              <SyncSection
                ref={syncSectionRef}
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
          ref={syncSectionRef}
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
              ref={authSectionRef}
              connector={connector}
              connectorConfig={connectorConfig}
              formData={formData.auth}
              formErrors={formErrors.auth}
              conditionalDisplay={conditionalDisplay}
              accountTypeLoading={accountTypeLoading}
              isBusiness={isBusiness}
              isCreateMode={isCreateMode}
              instanceName={instanceName || ''}
              instanceNameError={instanceNameError}
              onInstanceNameChange={setInstanceName}
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
              selectedAuthType={selectedAuthType}
              handleAuthTypeChange={handleAuthTypeChange}
              saveAttempted={saveAttempted}
              sharepointSectionRef={sharepointSectionRef}
              businessOAuthSectionRef={businessOAuthSectionRef}
            />
          );
        case 1:
          return (
            <FiltersSection
              ref={filtersSectionRef}
              connectorConfig={connectorConfig}
              formData={formData.filters}
              formErrors={formErrors.filters}
              onFieldChange={handleFieldChange}
              onRemoveFilter={handleRemoveFilter}
              connectorId={connector._key}
            />
          );
        case 2:
          return (
            <SyncSection
              ref={syncSectionRef}
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
            ref={authSectionRef}
            connector={connector}
            connectorConfig={connectorConfig}
            formData={formData.auth}
            formErrors={formErrors.auth}
            conditionalDisplay={conditionalDisplay}
            accountTypeLoading={accountTypeLoading}
            isBusiness={isBusiness}
            isCreateMode={isCreateMode}
            instanceName={instanceName || ''}
            instanceNameError={instanceNameError}
            onInstanceNameChange={setInstanceName}
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
            selectedAuthType={selectedAuthType}
            handleAuthTypeChange={handleAuthTypeChange}
            saveAttempted={saveAttempted}
            sharepointSectionRef={sharepointSectionRef}
            businessOAuthSectionRef={businessOAuthSectionRef}
          />
        );
      case 1:
        return (
          <SyncSection
            ref={syncSectionRef}
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
    authOnly,
    syncSettingsMode,
    enableMode,
    syncOnly,
    isCreateMode,
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
    instanceName,
    instanceNameError,
    setInstanceName,
    selectedAuthType,
    handleAuthTypeChange,
    saveAttempted,
    authSectionRef,
    businessOAuthSectionRef,
    filtersSectionRef,
    sharepointSectionRef,
    syncSectionRef,
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
              backgroundColor: isDark
                ? alpha(theme.palette.common.white, 0.9)
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
              Configure {connector.name[0].toUpperCase() + connector.name.slice(1).toLowerCase()}
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
                  bgcolor: isDark ? alpha(theme.palette.common.white, 0.05) : 'transparent',
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
                    bgcolor: isDark ? alpha(theme.palette.common.white, 0.05) : 'transparent',
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
            color: isDark ? alpha(theme.palette.text.secondary, 0.8) : theme.palette.text.secondary,
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
              bgcolor: isDark ? alpha(theme.palette.error.main, 0.15) : undefined,
              border: isDark ? `1px solid ${alpha(theme.palette.error.main, 0.3)}` : 'none',
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

        {/* Info notification for Sync Settings */}
        {syncSettingsMode && (
          <Alert
            severity="info"
            icon={<Iconify icon="mdi:information-outline" width={20} height={20} sx={{ color: theme.palette.info.main }} />}
            sx={{
              mx: 2.5,
              mt: saveError ? 1.5 : 2,
              mb: 0,
              borderRadius: 1.5,
              flexShrink: 0,
              bgcolor: isDark ? alpha(theme.palette.info.main, 0.1) : alpha(theme.palette.info.main, 0.05),
              border: isDark ? `1px solid ${alpha(theme.palette.info.main, 0.2)}` : `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
              alignItems: 'center',
            }}
          >
            <AlertTitle sx={{ fontWeight: 600, fontSize: '0.8125rem', mb: 0.25 }}>
              Sync Settings
            </AlertTitle>
            <Typography variant="body2" sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
              Configure filters and sync settings. The connector must be disabled and authenticated (for OAUTH connectors) to save changes.
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
            px: 1.5,
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
          <Box sx={{ p: 2 }}>
            <Stack spacing={0.5}>
              {steps.length > 1 && <ConfigStepper activeStep={activeStep} steps={steps} />}
              <Box>{renderStepContent()}</Box>
            </Stack>
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
              Configuration is locked while connector is active. Disable the connector to make
              changes.
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
              color="primary"
              onClick={handleNext}
              disabled={saving}
              sx={{
                textTransform: 'none',
                fontWeight: 500,
                px: 3,
                py: 0.625,
                borderRadius: 1,
                fontSize: '0.8125rem',
                boxShadow: isDark ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}` : 'none',
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
                color="primary"
                onClick={
                  enableMode
                    ? () => {
                        // Always show modal when manual indexing is enabled; otherwise only when no sync filters selected
                        if (isManualIndexingEnabled || !hasAnySyncFiltersSelected) {
                          setConfirmSyncOpen(true);
                        } else {
                          handleSave();
                        }
                      }
                    : handleSave
                }
                disabled={saving}
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
                  boxShadow: isDark ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}` : 'none',
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
                    opacity: (isConnectorActive && !enableMode && !syncSettingsMode) ? 0.5 : 0.38,
                  },
                  transition: 'all 0.2s ease',
                }}
              >
                {saving
                  ? enableMode
                    ? 'Saving & Enabling...'
                    : syncOnly
                      ? 'Saving Filters ...'
                      : syncSettingsMode
                        ? 'Saving Filters ...'
                        : authOnly
                          ? 'Saving Auth...'
                          : 'Saving...'
                  : enableMode
                    ? 'Save Filters & Enable Sync'
                    : syncOnly
                      ? 'Save Filters'
                      : syncSettingsMode
                        ? 'Save Filters'
                        : authOnly
                          ? 'Save Auth Settings'
                          : 'Save Configuration'}
              </Button>
          )}
        </Box>
      </DialogActions>

      {/* Confirmation modal when saving filters and enabling sync */}
      <ConfirmDialog
        open={confirmSyncOpen}
        onClose={() => setConfirmSyncOpen(false)}
        title="Start sync process?"
        content={
          isManualIndexingEnabled
            ? 'You have enabled Manual indexing for this connector. Records will be synced but won\'t be searchable by AI until you index them. You can select which records to index manually from All records. Do you want to proceed?'
            : 'This process could sync a large number of records. Are you sure you want to start the sync? Consider adding filters through the Filters section to reduce the number of records to sync.'
        }
        sx={{ zIndex: 1400 }}
        action={
          <Button
            variant="contained"
            color="primary"
            onClick={() => {
              setConfirmSyncOpen(false);
              handleSave();
            }}
            sx={{ textTransform: 'none' }}
          >
            Start sync
          </Button>
        }
      />
    </Dialog>
  );
};

export default ConnectorConfigForm;
