import React from 'react';
import {
  Paper,
  Box,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  Grid,
  alpha,
  useTheme,
  Link,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import syncIcon from '@iconify-icons/mdi/sync';
import clockIcon from '@iconify-icons/mdi/clock-outline';
import optionsIcon from '@iconify-icons/mdi/dots-vertical';
import bookIcon from '@iconify-icons/mdi/book-outline';
import openInNewIcon from '@iconify-icons/mdi/open-in-new';
import { FieldRenderer } from '../field-renderers';
import ScheduledSyncConfig from '../scheduled-sync-config';
import { ConnectorConfig } from '../../types/types';

interface SyncSectionProps {
  connectorConfig: ConnectorConfig | null;
  formData: Record<string, any>;
  formErrors: Record<string, string>;
  onFieldChange: (section: string, fieldName: string, value: any) => void;
  saving: boolean;
}

const SyncSection: React.FC<SyncSectionProps> = ({
  connectorConfig,
  formData,
  formErrors,
  onFieldChange,
  saving,
}) => {
  const theme = useTheme();

  if (!connectorConfig) return null;

  const { sync } = connectorConfig.config;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Sync Strategy */}
      <Paper
        variant="outlined"
        sx={{
          p: 2,
          borderRadius: 1.5,
          bgcolor: theme.palette.background.paper,
          borderColor: alpha(theme.palette.divider, 0.12),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
          <Box
            sx={{
              p: 0.375,
              borderRadius: 0.75,
              bgcolor: alpha(theme.palette.text.primary, 0.04),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Iconify icon={syncIcon} width={14} height={14} color={theme.palette.text.secondary} />
          </Box>
          <Box>
            <Typography variant="subtitle2" sx={{ 
              fontWeight: 600, 
              fontSize: '0.8125rem',
              color: theme.palette.text.primary,
              mb: 0.125
            }}>
              Sync Strategy
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ 
              fontSize: '0.75rem',
              lineHeight: 1.3
            }}>
              Choose how data will be synchronized from {connectorConfig.name}
            </Typography>
          </Box>
        </Box>

        <FormControl fullWidth size="small">
          <InputLabel sx={{ fontSize: '0.8125rem' }}>Select Sync Strategy</InputLabel>
          <Select
            value={formData.selectedStrategy || sync.supportedStrategies[0] || ''}
            onChange={(e) => onFieldChange('sync', 'selectedStrategy', e.target.value)}
            label="Select Sync Strategy"
            sx={{
              borderRadius: 1.25,
              '& .MuiSelect-select': { fontSize: '0.8125rem' },
            }}
          >
            {sync.supportedStrategies.map((strategy) => (
              <MenuItem key={strategy} value={strategy} sx={{ fontSize: '0.8125rem' }}>
                {strategy.replace('_', ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Paper>

      {/* Scheduled Sync Configuration (Start time and max repetitions hidden for now) */}
      {formData.selectedStrategy === 'SCHEDULED' && (
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            borderRadius: 1.5,
            bgcolor: theme.palette.background.paper,
            borderColor: alpha(theme.palette.divider, 0.12),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
            <Box
              sx={{
                p: 0.375,
                borderRadius: 0.75,
                bgcolor: alpha(theme.palette.text.primary, 0.04),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon={clockIcon}
                width={14}
                height={14}
                color={theme.palette.text.secondary}
              />
            </Box>
            <Box>
              <Typography variant="subtitle2" sx={{ 
                fontWeight: 600, 
                fontSize: '0.8125rem',
                color: theme.palette.text.primary,
                mb: 0.125
              }}>
                Scheduled Sync Settings
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ 
                fontSize: '0.75rem',
                lineHeight: 1.3
              }}>
                Configure synchronization interval and timezone
              </Typography>
            </Box>
          </Box>
          <ScheduledSyncConfig
            value={formData.scheduledConfig || {}}
            onChange={(value) => onFieldChange('sync', 'scheduledConfig', value)}
            disabled={saving}
          />
        </Paper>
      )}

      {/* Additional Sync Settings */}
      {sync.customFields.length > 0 && (
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            borderRadius: 1.5,
            bgcolor: theme.palette.background.paper,
            borderColor: alpha(theme.palette.divider, 0.12),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
            <Box
              sx={{
                p: 0.375,
                borderRadius: 0.75,
                bgcolor: alpha(theme.palette.text.primary, 0.04),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon={optionsIcon}
                width={14}
                height={14}
                color={theme.palette.text.secondary}
              />
            </Box>
            <Box>
              <Typography variant="subtitle2" sx={{ 
                fontWeight: 600, 
                fontSize: '0.8125rem',
                color: theme.palette.text.primary,
                mb: 0.125
              }}>
                Additional Settings
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ 
                fontSize: '0.75rem',
                lineHeight: 1.3
              }}>
                Configure advanced sync options
              </Typography>
            </Box>
          </Box>
          <Grid container spacing={2}>
            {sync.customFields.map((field) => (
              <Grid item xs={12} key={field.name}>
                <FieldRenderer
                  field={field}
                  value={formData[field.name]}
                  onChange={(value) => onFieldChange('sync', field.name, value)}
                  error={formErrors[field.name]}
                />
              </Grid>
            ))}
          </Grid>
        </Paper>
      )}

      {/* Sync Strategy Info */}
      <Alert
        severity="info"
        variant="outlined"
        sx={{
          borderRadius: 1.5,
          py: 1.25,
        }}
      >
        <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.4 }}>
          {sync.supportedStrategies.includes('WEBHOOK') &&
            'Webhook: Real-time updates when data changes. '}
          {sync.supportedStrategies.includes('SCHEDULED') &&
            'Scheduled: Periodic sync at regular intervals. '}
          {sync.supportedStrategies.includes('MANUAL') &&
            'Manual: On-demand sync when triggered. '}
          {sync.supportedStrategies.includes('REALTIME') &&
            'Real-time: Continuous sync for live updates.'}
        </Typography>
      </Alert>

      {/* Documentation Links - Compact Visual Guide */}
      {connectorConfig.config.documentationLinks && connectorConfig.config.documentationLinks.length > 0 && (
        <Paper
          variant="outlined"
          sx={{
            p: 1.5,
            borderRadius: 1.5,
            bgcolor: alpha(theme.palette.info.main, 0.02),
            borderColor: alpha(theme.palette.info.main, 0.08),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.25 }}>
            <Box
              sx={{
                p: 0.375,
                borderRadius: 0.75,
                bgcolor: alpha(theme.palette.info.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                mt: 0.125,
              }}
            >
              <Iconify
                icon={bookIcon}
                width={12}
                height={12}
                color={theme.palette.info.main}
              />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                variant="caption"
                sx={{
                  fontWeight: 600,
                  color: theme.palette.info.main,
                  fontSize: '0.6875rem',
                  textTransform: 'uppercase',
                  letterSpacing: 0.5,
                  mb: 0.5,
                  display: 'block',
                }}
              >
                Documentation & Resources
              </Typography>
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 0.5,
                }}
              >
                {connectorConfig.config.documentationLinks.map((link, index) => (
                  <Box
                    key={index}
                    onClick={() => window.open(link.url, '_blank')}
                    sx={{
                      p: 1,
                      borderRadius: 0.75,
                      bgcolor: alpha(theme.palette.info.main, 0.04),
                      border: `1px solid ${alpha(theme.palette.info.main, 0.12)}`,
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                      '&:hover': {
                        bgcolor: alpha(theme.palette.info.main, 0.08),
                        borderColor: alpha(theme.palette.info.main, 0.2),
                        transform: 'translateY(-1px)',
                      },
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: 500,
                          color: theme.palette.text.primary,
                          fontSize: '0.75rem',
                          flex: 1,
                          minWidth: 0,
                        }}
                      >
                        {link.title}
                      </Typography>
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 0.25,
                          color: theme.palette.info.main,
                        }}
                      >
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: '0.625rem',
                            fontWeight: 500,
                            textTransform: 'uppercase',
                            letterSpacing: 0.5,
                          }}
                        >
                          {link.type}
                        </Typography>
                        <Iconify
                          icon={openInNewIcon}
                          width={10}
                          height={10}
                        />
                      </Box>
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          </Box>
        </Paper>
      )}
    </Box>
  );
};

export default SyncSection;
