/**
 * Create OAuth App Dialog Component
 * 
 * Dialog for creating a new OAuth app configuration
 */

import React, { useState, useEffect, useMemo } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  Typography,
  CircularProgress,
  Alert,
  alpha,
  useTheme,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import closeIcon from '@iconify-icons/mdi/close';
import { ConnectorApiService } from '../../services/api';
import { FieldRenderer } from '../field-renderers';

interface CreateOAuthAppDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  connectorType?: string;
}

const CreateOAuthAppDialog: React.FC<CreateOAuthAppDialogProps> = ({
  open,
  onClose,
  onSuccess,
  connectorType: initialConnectorType,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // State
  const [connectorType, setConnectorType] = useState<string>(initialConnectorType || '');
  const [oauthInstanceName, setOAuthInstanceName] = useState('');
  const [config, setConfig] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [schema, setSchema] = useState<any>(null);
  const [availableConnectors, setAvailableConnectors] = useState<any[]>([]);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [selectedConnectorInfo, setSelectedConnectorInfo] = useState<any>(null);

  // Fetch available connectors if connectorType is not provided
  useEffect(() => {
    if (!initialConnectorType && open) {
      setLoadingConnectors(true);
      ConnectorApiService.getOAuthConfigRegistry(1, 100)
        .then((result) => {
          // Map 'type' field to 'connectorType' for consistency
          const connectors = (result.connectors || []).map((c: any) => ({
            ...c,
            connectorType: c.type || c.connectorType,
          }));
          setAvailableConnectors(connectors);
        })
        .catch((error) => {
          console.error('Error fetching connectors:', error);
        })
        .finally(() => {
          setLoadingConnectors(false);
        });
    } else if (initialConnectorType && open) {
      // If connectorType is provided, fetch only that connector's registry info (more efficient)
      ConnectorApiService.getOAuthConfigRegistryByType(initialConnectorType)
        .then((connector) => {
          if (connector) {
            setSelectedConnectorInfo(connector);
          }
        })
        .catch((error) => {
          console.error('Error fetching connector registry:', error);
        });
    }
  }, [initialConnectorType, open]);

  // Fetch schema when connectorType changes
  useEffect(() => {
    if (connectorType && open) {
      setLoadingSchema(true);
      setSchema(null);
      setConfig({});
      setErrors({});

      // First, try to get authFields from registry response
      const connector = availableConnectors.find(
        (c) => (c.type || c.connectorType) === connectorType
      );
      
      if (connector && connector.authFields) {
        // Use authFields from registry if available
        setSelectedConnectorInfo(connector);
        const fields = connector.authFields || [];
        const initialConfig: Record<string, any> = {};
        fields.forEach((field: any) => {
          if (field.defaultValue !== undefined) {
            initialConfig[field.name] = field.defaultValue;
          }
        });
        setConfig(initialConfig);
        setSchema({ auth: { schemas: { OAUTH: { fields } } } });
        setLoadingSchema(false);
      } else {
        // Fallback to fetching schema from API
        ConnectorApiService.getConnectorSchema(connectorType)
          .then((fetchedSchema) => {
            setSchema(fetchedSchema);
            // Initialize config with default values
            const authSchemas = fetchedSchema?.auth?.schemas || {};
            const oauthSchema = authSchemas.OAUTH || authSchemas.OAUTH_ADMIN_CONSENT || {};
            const fields = oauthSchema.fields || [];
            const initialConfig: Record<string, any> = {};
            fields.forEach((field: any) => {
              if (field.defaultValue !== undefined) {
                initialConfig[field.name] = field.defaultValue;
              }
            });
            setConfig(initialConfig);
          })
          .catch((error) => {
            console.error('Error fetching schema:', error);
            setErrors({ schema: 'Failed to load connector schema' });
          })
          .finally(() => {
            setLoadingSchema(false);
          });
      }
    }
  }, [connectorType, open, availableConnectors]);

  // Reset form when dialog closes
  useEffect(() => {
    if (!open) {
      setConnectorType(initialConnectorType || '');
      setOAuthInstanceName('');
      setConfig({});
      setErrors({});
      setSchema(null);
      setSelectedConnectorInfo(null);
    }
  }, [open, initialConnectorType]);

  // Get OAuth schema fields
  const oauthFields = useMemo(() => {
    if (!schema) return [];
    const authSchemas = schema.auth?.schemas || {};
    const oauthSchema = authSchemas.OAUTH || authSchemas.OAUTH_ADMIN_CONSENT || {};
    return oauthSchema.fields || [];
  }, [schema]);

  // Validation
  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!connectorType) {
      newErrors.connectorType = 'Connector type is required';
    }

    if (!oauthInstanceName.trim()) {
      newErrors.oauthInstanceName = 'OAuth app name is required';
    } else if (oauthInstanceName.trim().length < 3) {
      newErrors.oauthInstanceName = 'OAuth app name must be at least 3 characters';
    }

    // Validate required fields from schema
    oauthFields.forEach((field: any) => {
      if (field.required && !config[field.name]) {
        newErrors[`config.${field.name}`] = `${field.displayName || field.name} is required`;
      }
    });

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle create
  const handleCreate = async () => {
    if (!validate()) {
      return;
    }

    setLoading(true);
    try {
      await ConnectorApiService.createOAuthConfig(
        connectorType,
        oauthInstanceName.trim(),
        config
      );
      onSuccess();
    } catch (error: any) {
      const errorMessage =
        error?.response?.data?.detail || error?.message || 'Failed to create OAuth app';
      setErrors({ submit: errorMessage });
    } finally {
      setLoading(false);
    }
  };

  // Handle field change
  const handleFieldChange = (fieldName: string, value: any) => {
    setConfig((prev) => ({
      ...prev,
      [fieldName]: value,
    }));
    // Clear error for this field
    if (errors[`config.${fieldName}`]) {
      setErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors[`config.${fieldName}`];
        return newErrors;
      });
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          backgroundColor: isDark
            ? alpha(theme.palette.background.paper, 0.95)
            : theme.palette.background.paper,
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pb: 2,
          pt: 3,
          px: 3,
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flex: 1 }}>
          {selectedConnectorInfo?.iconPath && (
            <Box
              component="img"
              src={selectedConnectorInfo.iconPath}
              alt={selectedConnectorInfo.name}
              sx={{
                width: 40,
                height: 40,
                objectFit: 'contain',
                borderRadius: 1,
                backgroundColor: isDark
                  ? alpha(theme.palette.common.white, 0.9)
                  : alpha(theme.palette.grey[100], 0.8),
                border: `1px solid ${theme.palette.divider}`,
                p: 0.5,
              }}
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                target.src = '/assets/icons/connectors/default.svg';
              }}
            />
          )}
          <Box sx={{ flex: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 600, mb: 0.25 }}>
              Create OAuth App
            </Typography>
            {selectedConnectorInfo?.name && (
              <Typography variant="body2" sx={{ color: theme.palette.text.secondary }}>
                {selectedConnectorInfo.name}
                {selectedConnectorInfo.appGroup && ` • ${selectedConnectorInfo.appGroup}`}
              </Typography>
            )}
          </Box>
        </Box>
        <Button
          onClick={onClose}
          sx={{
            minWidth: 'auto',
            p: 0.5,
            color: theme.palette.text.secondary,
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.secondary, 0.08),
            },
          }}
        >
          <Iconify icon={closeIcon} width={20} height={20} />
        </Button>
      </DialogTitle>

      <DialogContent sx={{ pt: 3, px: 3 }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {/* Connector Type Selector (if not provided) */}
          {!initialConnectorType && (
            <FormControl fullWidth error={!!errors.connectorType}>
              <InputLabel>Connector Type</InputLabel>
              <Select
                value={connectorType}
                onChange={(e) => {
                  const newType = e.target.value;
                  setConnectorType(newType);
                  const connector = availableConnectors.find(
                    (c) => (c.type || c.connectorType) === newType
                  );
                  if (connector) {
                    setSelectedConnectorInfo(connector);
                  }
                }}
                label="Connector Type"
                disabled={loadingConnectors}
              >
                {availableConnectors.map((connector) => (
                  <MenuItem key={connector.connectorType} value={connector.connectorType}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                      {connector.iconPath && (
                        <Box
                          component="img"
                          src={connector.iconPath}
                          alt={connector.name}
                          sx={{
                            width: 24,
                            height: 24,
                            objectFit: 'contain',
                          }}
                          onError={(e) => {
                            const target = e.target as HTMLImageElement;
                            target.src = '/assets/icons/connectors/default.svg';
                          }}
                        />
                      )}
                      <Box>
                        <Typography variant="body2">{connector.name}</Typography>
                        {connector.appGroup && (
                          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                            {connector.appGroup}
                          </Typography>
                        )}
                      </Box>
                    </Box>
                  </MenuItem>
                ))}
              </Select>
              {errors.connectorType && (
                <Typography variant="caption" color="error" sx={{ mt: 0.5 }}>
                  {errors.connectorType}
                </Typography>
              )}
            </FormControl>
          )}

          {/* OAuth Instance Name */}
          <TextField
            label="OAuth App Name"
            value={oauthInstanceName}
            onChange={(e) => {
              setOAuthInstanceName(e.target.value);
              if (errors.oauthInstanceName) {
                setErrors((prev) => {
                  const newErrors = { ...prev };
                  delete newErrors.oauthInstanceName;
                  return newErrors;
                });
              }
            }}
            error={!!errors.oauthInstanceName}
            helperText={errors.oauthInstanceName || 'Give this OAuth app a unique name'}
            fullWidth
            required
            placeholder="e.g., Production OAuth App"
          />

          {/* Loading Schema */}
          {loadingSchema && (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress size={32} />
            </Box>
          )}

          {/* OAuth Fields */}
          {!loadingSchema && schema && oauthFields.length > 0 && (
            <Box>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  mb: 2,
                  color: theme.palette.text.primary,
                }}
              >
                OAuth Credentials
              </Typography>
              <Grid container spacing={2}>
                {oauthFields.map((field: any) => (
                  <Grid item xs={12} key={field.name}>
                    <FieldRenderer
                      field={field}
                      value={config[field.name]}
                      onChange={(value) => handleFieldChange(field.name, value)}
                      error={errors[`config.${field.name}`]}
                    />
                  </Grid>
                ))}
              </Grid>
            </Box>
          )}

          {/* Error Alert */}
          {errors.submit && (
            <Alert severity="error" onClose={() => setErrors((prev) => ({ ...prev, submit: '' }))}>
              {errors.submit}
            </Alert>
          )}
        </Box>
      </DialogContent>

      <DialogActions
        sx={{
          p: 3,
          px: 3,
          borderTop: `1px solid ${theme.palette.divider}`,
          gap: 1.5,
        }}
      >
        <Button 
          onClick={onClose} 
          disabled={loading}
          sx={{
            textTransform: 'none',
            fontWeight: 500,
          }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleCreate}
          disabled={loading || !connectorType || !oauthInstanceName.trim()}
          startIcon={loading ? <CircularProgress size={16} /> : <Iconify icon="mdi:check" width={18} height={18} />}
          sx={{
            textTransform: 'none',
            fontWeight: 600,
            px: 3,
          }}
        >
          {loading ? 'Creating...' : 'Create OAuth App'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateOAuthAppDialog;

