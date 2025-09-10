import React, { useEffect, useState, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogActions,
  Typography,
  Box,
  Button,
  Stepper,
  Step,
  StepLabel,
  Paper,
  Divider,
  Alert,
  CircularProgress,
  Stack,
  Chip,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  useTheme,
  alpha,
  IconButton,
  Grid,
  Link,
  AlertTitle,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import closeIcon from '@iconify-icons/mdi/close';
import filterIcon from '@iconify-icons/mdi/filter';
import infoIcon from '@iconify-icons/eva/info-outline';
import saveIcon from '@iconify-icons/eva/save-outline';
import { Connector, ConnectorConfig } from '../types/types';
import { ConnectorApiService } from '../services/api';
import { FieldRenderer } from './field-renderers';
import ScheduledSyncConfig from './scheduled-sync-config';
import { CrawlingManagerApi } from '../services/crawling-manager';
import { buildCronFromSchedule } from '../utils/cron';

interface ConnectorConfigFormProps {
  connector: Connector;
  onClose: () => void;
  onSuccess?: () => void;
}

interface FormData {
  auth: Record<string, any>;
  sync: Record<string, any>;
  filters: Record<string, any>;
}

interface FormErrors {
  auth: Record<string, string>;
  sync: Record<string, string>;
  filters: Record<string, string>;
}

const ConnectorConfigForm = ({ connector, onClose, onSuccess }: ConnectorConfigFormProps) => {
  const theme = useTheme();
  const [connectorConfig, setConnectorConfig] = useState<ConnectorConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [formData, setFormData] = useState<FormData>({
    auth: {},
    sync: {},
    filters: {},
  });
  const [formErrors, setFormErrors] = useState<FormErrors>({
    auth: {},
    sync: {},
    filters: {},
  });
  const [saveError, setSaveError] = useState<string | null>(null);

  const steps = ['Authentication', 'Sync Settings', 'Filters'];

  useEffect(() => {
    const fetchConnectorConfig = async () => {
      try {
        setLoading(true);
        
        // Fetch both config and schema
        const [configResponse, schemaResponse] = await Promise.all([
          ConnectorApiService.getConnectorConfig(connector.name.toUpperCase()),
          ConnectorApiService.getConnectorSchema(connector.name.toUpperCase())
        ]);
        
        // Merge schema with config values
        const mergedConfig = {
          ...configResponse,
          config: {
            auth: {
              ...schemaResponse.auth,
              values: configResponse.config.auth.values || {},
              customValues: configResponse.config.auth.customValues || {},
            },
            sync: {
              ...schemaResponse.sync,
              selectedStrategy: configResponse.config.sync.selectedStrategy || schemaResponse.sync.supportedStrategies?.[0] || 'MANUAL',
              scheduledConfig: configResponse.config.sync.scheduledConfig || {},
              values: configResponse.config.sync.values || {},
              customValues: configResponse.config.sync.customValues || {},
            },
            filters: {
              ...schemaResponse.filters,
              values: configResponse.config.filters.values || {},
              customValues: configResponse.config.filters.customValues || {},
            }
          }
        };
        
        setConnectorConfig(mergedConfig);
        
        // Initialize form data with existing values
        setFormData({
          auth: mergedConfig.config.auth.values || {},
          sync: {
            selectedStrategy: mergedConfig.config.sync.selectedStrategy || mergedConfig.config.sync.supportedStrategies?.[0] || 'MANUAL',
            scheduledConfig: mergedConfig.config.sync.scheduledConfig || {},
            ...(mergedConfig.config.sync.values || {}),
          },
          filters: mergedConfig.config.filters.values || {},
        });
      } catch (error) {
        console.error('Error fetching connector config:', error);
        setSaveError('Failed to load connector configuration');
      } finally {
        setLoading(false);
      }
    };
    
    fetchConnectorConfig();
  }, [connector]);

  const validateField = useCallback((field: any, value: any): string => {
    if (field.required && (!value || (typeof value === 'string' && !value.trim()))) {
      return `${field.displayName} is required`;
    }
    
    if (field.validation) {
      const { minLength, maxLength, pattern } = field.validation;
      
      if (minLength && value && value.length < minLength) {
        return `${field.displayName} must be at least ${minLength} characters`;
      }
      
      if (maxLength && value && value.length > maxLength) {
        return `${field.displayName} must be no more than ${maxLength} characters`;
      }
      
      if (pattern && value && !new RegExp(pattern).test(value)) {
        return `${field.displayName} format is invalid`;
      }
    }
    
    return '';
  }, []);

  const validateSection = useCallback((section: string, fields: any[], values: Record<string, any>): Record<string, string> => {
    const errors: Record<string, string> = {};
    
    fields.forEach(field => {
      const error = validateField(field, values[field.name]);
      if (error) {
        errors[field.name] = error;
      }
    });
    
    return errors;
  }, [validateField]);

  const handleFieldChange = useCallback((section: string, fieldName: string, value: any) => {
    setFormData(prev => ({
      ...prev,
      [section]: {
        ...prev[section as keyof FormData],
        [fieldName]: value,
      },
    }));
    
    // Clear error for this field
    setFormErrors(prev => ({
      ...prev,
      [section]: {
        ...prev[section as keyof FormErrors],
        [fieldName]: '',
      },
    }));
  }, []);

  const handleNext = useCallback(() => {
    if (!connectorConfig) return;
    
    let errors: Record<string, string> = {};
    
    // Validate current step
    switch (activeStep) {
      case 0: // Auth
        errors = validateSection('auth', connectorConfig.config.auth.schema.fields, formData.auth);
        break;
      case 1: // Sync
        errors = validateSection('sync', connectorConfig.config.sync.customFields, formData.sync);
        
        // Validate scheduled config if SCHEDULED strategy is selected
        if (formData.sync.selectedStrategy === 'SCHEDULED') {
          const scheduledConfig = formData.sync.scheduledConfig || {};
          if (!scheduledConfig.startTime || scheduledConfig.startTime === 0) {
            errors.scheduledConfig = 'Start date and time is required for scheduled sync';
          } else if (scheduledConfig.intervalMinutes <= 0) {
            errors.scheduledConfig = 'Sync interval must be greater than 0';
          } else if (scheduledConfig.maxRepetitions < 0) {
            errors.scheduledConfig = 'Max repetitions cannot be negative';
          }
        }
        break;
      case 2: // Filters
        errors = validateSection('filters', connectorConfig.config.filters.schema?.fields || [], formData.filters);
        break;
      default:
        break;
    }
    
    setFormErrors(prev => ({
      ...prev,
      [activeStep === 0 ? 'auth' : activeStep === 1 ? 'sync' : 'filters']: errors,
    }));
    
    if (Object.keys(errors).length === 0) {
      setActiveStep(prev => prev + 1);
    }
  }, [activeStep, connectorConfig, formData, validateSection]);

  const handleBack = useCallback(() => {
    setActiveStep(prev => prev - 1);
  }, []);

  const handleSave = useCallback(async () => {
    if (!connectorConfig) return;
    
    try {
      setSaving(true);
      setSaveError(null);
      
      // Validate all sections
      const authErrors = validateSection('auth', connectorConfig.config.auth.schema.fields, formData.auth);
      const syncErrors = validateSection('sync', connectorConfig.config.sync.customFields, formData.sync);
      const filterErrors = validateSection('filters', connectorConfig.config.filters.schema?.fields || [], formData.filters);
      
      const allErrors = { auth: authErrors, sync: syncErrors, filters: filterErrors };
      setFormErrors(allErrors);
      
      if (Object.values(allErrors).some(section => Object.keys(section).length > 0)) {
        return;
      }
      
      // Prepare config for API - store values in etcd
      const configToSave = {
        auth: {
          values: formData.auth,
          customValues: {},
        },
        sync: {
          selectedStrategy: formData.sync.selectedStrategy,
          scheduledConfig: formData.sync.scheduledConfig || {},
          values: formData.sync,
          customValues: {},
        },
        filters: {
          values: formData.filters,
          customValues: {},
        },
      };
      
      await ConnectorApiService.updateConnectorConfig(connector.name.toUpperCase(), configToSave as any);

      // After saving config, if strategy is SCHEDULED then schedule crawling via NodeJS
      const syncStrategy = String(formData.sync.selectedStrategy || '').toUpperCase();
      if (syncStrategy === 'SCHEDULED') {
        const scheduled = (formData.sync.scheduledConfig || {}) as any;
        const cron = buildCronFromSchedule({
          startTime: scheduled.startTime,
          intervalMinutes: scheduled.intervalMinutes,
          timezone: scheduled.timezone.toUpperCase() || 'UTC',
        });
        await CrawlingManagerApi.schedule(connector.name.toLowerCase(), {
          scheduleConfig: {
            scheduleType: 'custom',
            isEnabled: true,
            timezone: scheduled.timezone.toUpperCase() || 'UTC',
            cronExpression: cron,
          },
          priority: 5,
          maxRetries: 3,
          timeout: 300000,
        });
      } else {
        // Remove any existing schedule if switching away from SCHEDULED
        await CrawlingManagerApi.remove(connector.name.toLowerCase());
      }
      
      onSuccess?.();
      onClose();
    } catch (error) {
      console.error('Error saving connector config:', error);
      setSaveError('Failed to save configuration. Please try again.');
    } finally {
      setSaving(false);
    }
  }, [connectorConfig, formData, validateSection, onClose, onSuccess, connector.name]);

  const renderAuthSection = () => {
    if (!connectorConfig) return null;
    
    const { auth } = connectorConfig.config;
    
    return (
      <Box>
        <Stack spacing={3}>
          {/* Header */}
          <Box>
            <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
              Authentication Configuration
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Configure authentication settings for {connector.name}
            </Typography>
          </Box>

          {/* Documentation Alert */}
          <Alert variant="outlined" severity="info" sx={{ borderRadius: 1 }}>
            Refer to{' '}
            <Link
              href="https://docs.pipeshub.com/connectors"
              target="_blank"
              rel="noopener"
              sx={{ fontWeight: 500 }}
            >
              the documentation
            </Link>{' '}
            for more information.
          </Alert>
          
          {/* Redirect URI Info */}
          {auth.displayRedirectUri && auth.redirectUri && (
            <Box
              sx={{
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.primary.main, 0.04),
                border: `1px solid ${alpha(theme.palette.primary.main, 0.15)}`,
                display: 'flex',
                alignItems: 'flex-start',
                gap: 1,
              }}
            >
              <Iconify
                icon={infoIcon}
                width={20}
                height={20}
                color={theme.palette.primary.main}
                style={{ marginTop: 2 }}
              />
              <Box>
                <Typography variant="subtitle2" color="primary.main" sx={{ mb: 0.5 }}>
                  Redirect URI
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Use this URL when configuring your {connector.name} OAuth2 App.
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    mt: 1,
                    p: 1,
                    borderRadius: 0.5,
                    bgcolor: alpha(theme.palette.background.paper, 0.8),
                    border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                    fontFamily: 'monospace',
                    fontSize: '0.875rem',
                    wordBreak: 'break-all',
                  }}
                >
                  {auth.redirectUri}
                </Typography>
              </Box>
            </Box>
          )}
          
          {/* Documentation Links */}
          {auth.documentationLinks && auth.documentationLinks.length > 0 && (
            <Box
              sx={{
                p: 2,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.info.main, 0.04),
                border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
                display: 'flex',
                alignItems: 'flex-start',
                gap: 1,
              }}
            >
              <Iconify
                icon={infoIcon}
                width={20}
                height={20}
                color={theme.palette.info.main}
                style={{ marginTop: 2 }}
              />
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Documentation Links
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap">
                  {auth.documentationLinks.map((link, index) => (
                    <Chip
                      key={index}
                      label={link.title}
                      variant="outlined"
                      size="small"
                      clickable
                      onClick={() => window.open(link.url, '_blank')}
                      sx={{
                        '&:hover': {
                          bgcolor: alpha(theme.palette.info.main, 0.08),
                        },
                      }}
                    />
                  ))}
                </Stack>
              </Box>
            </Box>
          )}

          {/* Form Fields */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
              {auth.type === 'OAUTH' ? 'OAuth2 Credentials' : 
               auth.type === 'API_TOKEN' ? 'API Credentials' :
               auth.type === 'USERNAME_PASSWORD' ? 'Login Credentials' :
               'Authentication Settings'}
            </Typography>
            
            <Grid container spacing={2.5}>
              {auth.schema.fields.map((field) => (
                <Grid item xs={12} key={field.name}>
                  <FieldRenderer
                    field={field}
                    value={formData.auth[field.name]}
                    onChange={(value) => handleFieldChange('auth', field.name, value)}
                    error={formErrors.auth[field.name]}
                  />
                </Grid>
              ))}
              
              {auth.customFields.map((field) => (
                <Grid item xs={12} key={field.name}>
                  <FieldRenderer
                    field={field}
                    value={formData.auth[field.name]}
                    onChange={(value) => handleFieldChange('auth', field.name, value)}
                    error={formErrors.auth[field.name]}
                  />
                </Grid>
              ))}
            </Grid>
          </Box>
        </Stack>
      </Box>
    );
  };

  const renderSyncSection = () => {
    if (!connectorConfig) return null;
    
    const { sync } = connectorConfig.config;
    
    return (
      <Box>
        <Stack spacing={3}>
          {/* Header */}
          <Box>
            <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
              Sync Configuration
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Configure how data is synchronized from {connector.name}
            </Typography>
          </Box>

          {/* Sync Strategy */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600 }}>
              Sync Strategy
            </Typography>
            <FormControl fullWidth size="small">
              <InputLabel>Select Sync Strategy</InputLabel>
              <Select
                value={formData.sync.selectedStrategy || sync.supportedStrategies[0] || ''}
                onChange={(e) => handleFieldChange('sync', 'selectedStrategy', e.target.value)}
                label="Select Sync Strategy"
                sx={{ borderRadius: 1.5 }}
              >
                {sync.supportedStrategies.map((strategy) => (
                  <MenuItem key={strategy} value={strategy}>
                    {strategy.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
              Choose how data will be synchronized from {connector.name}
            </Typography>
          </Box>

          {/* Scheduled Sync Configuration */}
          {formData.sync.selectedStrategy === 'SCHEDULED' && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
                Scheduled Sync Settings
              </Typography>
              <ScheduledSyncConfig
                value={formData.sync.scheduledConfig || {}}
                onChange={(value) => handleFieldChange('sync', 'scheduledConfig', value)}
                error={formErrors.sync.scheduledConfig}
                disabled={saving}
              />
            </Box>
          )}

          {/* Additional Sync Settings */}
          {sync.customFields.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
                Additional Settings
              </Typography>
              <Grid container spacing={2.5}>
                {sync.customFields.map((field) => (
                  <Grid item xs={12} key={field.name}>
                    <FieldRenderer
                      field={field}
                      value={formData.sync[field.name]}
                      onChange={(value) => handleFieldChange('sync', field.name, value)}
                      error={formErrors.sync[field.name]}
                    />
                  </Grid>
                ))}
              </Grid>
            </Box>
          )}

          {/* Sync Strategy Info */}
          <Box
            sx={{
              p: 2,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.info.main, 0.04),
              border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 1,
            }}
          >
            <Iconify
              icon={infoIcon}
              width={20}
              height={20}
              color={theme.palette.info.main}
              style={{ marginTop: 2 }}
            />
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                Sync Strategy Information
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {sync.supportedStrategies.includes('WEBHOOK') && 
                  'Webhook: Real-time updates when data changes in the source system.'}
                {sync.supportedStrategies.includes('SCHEDULED') && 
                  'Scheduled: Periodic synchronization at regular intervals.'}
                {sync.supportedStrategies.includes('MANUAL') && 
                  'Manual: On-demand synchronization when triggered by user actions.'}
                {sync.supportedStrategies.includes('REALTIME') && 
                  'Real-time: Continuous synchronization for live data updates.'}
              </Typography>
            </Box>
          </Box>
        </Stack>
      </Box>
    );
  };

  const renderFiltersSection = () => {
    if (!connectorConfig) return null;
    
    const { filters } = connectorConfig.config;
    const hasFilters = (filters.schema?.fields && filters.schema.fields.length > 0) || 
                      (filters.customFields && filters.customFields.length > 0);
    
    return (
      <Box>
        <Stack spacing={3}>
          {/* Header */}
          <Box>
            <Typography variant="h6" sx={{ mb: 1, fontWeight: 600 }}>
              Filter Configuration
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Configure data filters for {connector.name}
            </Typography>
          </Box>

          {hasFilters ? (
            <>
              {/* Filter Fields */}
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 600 }}>
                  Data Filters
                </Typography>
                <Grid container spacing={2.5}>
                  {filters.schema?.fields.map((field) => (
                    <Grid item xs={12} key={field.name}>
                      <FieldRenderer
                        field={field}
                        value={formData.filters[field.name]}
                        onChange={(value) => handleFieldChange('filters', field.name, value)}
                        error={formErrors.filters[field.name]}
                      />
                    </Grid>
                  ))}
                  
                  {filters.customFields?.map((field) => (
                    <Grid item xs={12} key={field.name}>
                      <FieldRenderer
                        field={field}
                        value={formData.filters[field.name]}
                        onChange={(value) => handleFieldChange('filters', field.name, value)}
                        error={formErrors.filters[field.name]}
                      />
                    </Grid>
                  ))}
                </Grid>
              </Box>

              {/* Filter Info */}
              <Box
                sx={{
                  p: 2,
                  borderRadius: 1,
                  bgcolor: alpha(theme.palette.info.main, 0.04),
                  border: `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 1,
                }}
              >
                <Iconify
                  icon={infoIcon}
                  width={20}
                  height={20}
                  color={theme.palette.info.main}
                  style={{ marginTop: 2 }}
                />
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    Filter Information
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Configure filters to control which data is synchronized from {connector.name}. 
                    These settings help you focus on the most relevant information for your needs.
                  </Typography>
                </Box>
              </Box>
            </>
          ) : (
            /* No Filters Available */
            <Box
              sx={{
                p: 3,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.grey[500], 0.04),
                border: `1px solid ${alpha(theme.palette.grey[500], 0.15)}`,
                textAlign: 'center',
              }}
            >
              <Iconify
                icon={filterIcon}
                width={32}
                height={32}
                color={theme.palette.text.disabled}
                style={{ marginBottom: 16 }}
              />
              <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
                No Filters Available
              </Typography>
              <Typography variant="body2" color="text.secondary">
                This connector doesn&apos;t support custom data filtering. All available data will be synchronized.
              </Typography>
            </Box>
          )}
        </Stack>
      </Box>
    );
  };

  const renderStepContent = () => {
    switch (activeStep) {
      case 0:
        return renderAuthSection();
      case 1:
        return renderSyncSection();
      case 2:
        return renderFiltersSection();
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
            boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
          },
        }}
      >
        <DialogContent sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}>
          <CircularProgress />
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
          boxShadow: '0 10px 35px rgba(0, 0, 0, 0.1)',
          overflow: 'hidden',
        //   minHeight: '80vh',
        },
      }}
    >
      <DialogTitle 
        sx={{ 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'space-between',
          p: 3,
          borderBottom: `1px solid ${theme.palette.divider}`,
          backgroundColor: alpha(theme.palette.background.default, 0.3),
        }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 600, mb: 0.5 }}>
            Configure {connector.name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {connector.appGroup} • {connector.authType}
          </Typography>
        </Box>
        <IconButton 
          onClick={onClose} 
          size="small"
          sx={{
            color: theme.palette.text.secondary,
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.secondary, 0.08),
            },
          }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </IconButton>
      </DialogTitle>
      
      <DialogContent sx={{ p: 0 }}>
        {saveError && (
          <Alert 
            severity="error" 
            sx={{ 
              m: 3, 
              mb: 0,
              borderRadius: 1,
            }}
          >
            <AlertTitle sx={{ fontWeight: 500, fontSize: '0.875rem' }}>Error</AlertTitle>
            {saveError}
          </Alert>
        )}
        
        <Box sx={{ p: 3, pb: 0 }}>
          <Stepper 
            activeStep={activeStep} 
            sx={{ 
              mb: 3,
              '& .MuiStepLabel-label': {
                fontSize: '0.875rem',
                fontWeight: 500,
              },
            }}
          >
            {steps.map((label) => (
              <Step key={label}>
                <StepLabel>{label}</StepLabel>
              </Step>
            ))}
          </Stepper>
        </Box>
        
        <Box sx={{ p: 3, pt: 0 }}>
          {renderStepContent()}
        </Box>
      </DialogContent>
      
      <DialogActions 
        sx={{ 
          p: 3, 
          pt: 2,
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor: alpha(theme.palette.background.default, 0.3),
        }}
      >
        <Button 
          onClick={onClose} 
          disabled={saving}
          sx={{ textTransform: 'none', fontWeight: 500 }}
        >
          Cancel
        </Button>
        
        {activeStep > 0 && (
          <Button 
            onClick={handleBack} 
            disabled={saving}
            sx={{ textTransform: 'none', fontWeight: 500 }}
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
              fontWeight: 600,
              px: 3,
            }}
          >
            Next
          </Button>
        ) : (
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={saving}
            startIcon={saving ? <CircularProgress size={16} /> : <Iconify icon={saveIcon} width={16} height={16} />}
            sx={{ 
              textTransform: 'none', 
              fontWeight: 600,
              px: 3,
            }}
          >
            {saving ? 'Saving...' : 'Save Configuration'}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default ConnectorConfigForm;
