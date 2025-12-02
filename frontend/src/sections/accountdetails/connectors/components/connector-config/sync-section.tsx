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
  Collapse,
  IconButton,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import syncIcon from '@iconify-icons/mdi/sync';
import clockIcon from '@iconify-icons/mdi/clock-outline';
import optionsIcon from '@iconify-icons/mdi/dots-vertical';
import bookIcon from '@iconify-icons/mdi/book-outline';
import openInNewIcon from '@iconify-icons/mdi/open-in-new';
import chevronDownIcon from '@iconify-icons/mdi/chevron-down';
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
  const isDark = theme.palette.mode === 'dark';
  const [showDocs, setShowDocs] = React.useState(false);

  if (!connectorConfig) return null;

  const { sync } = connectorConfig.config;

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 1.5,
        height: '100%',
        pb: 3,
      }}
    >
      {/* Sync Strategy */}
      <Paper
        variant="outlined"
        sx={{
          p: 2,
          borderRadius: 1.25,
          bgcolor: isDark
            ? alpha(theme.palette.background.paper, 0.4)
            : theme.palette.background.paper,
          borderColor: isDark
            ? alpha(theme.palette.divider, 0.12)
            : alpha(theme.palette.divider, 0.1),
          boxShadow: isDark
            ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
            : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
          <Box
            sx={{
              p: 0.625,
              borderRadius: 1,
              bgcolor: alpha(theme.palette.primary.main, 0.1),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Iconify icon={syncIcon} width={16} color={theme.palette.primary.main} />
          </Box>
          <Box sx={{ flex: 1 }}>
            <Typography
              variant="subtitle2"
              sx={{
                fontWeight: 600,
                fontSize: '0.875rem',
                color: theme.palette.text.primary,
                lineHeight: 1.4,
              }}
            >
              Sync Strategy
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                fontSize: '0.75rem',
                lineHeight: 1.3,
              }}
            >
              Choose how data will be synchronized from {connectorConfig.name}
            </Typography>
          </Box>
        </Box>

        <FormControl fullWidth size="small">
          <InputLabel sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
            Select Sync Strategy
          </InputLabel>
          <Select
            value={formData.selectedStrategy || sync.supportedStrategies[0] || ''}
            onChange={(e) => onFieldChange('sync', 'selectedStrategy', e.target.value)}
            label="Select Sync Strategy"
            sx={{
              borderRadius: 1.25,
              '& .MuiSelect-select': {
                fontSize: '0.875rem',
                fontWeight: 500,
              },
              backgroundColor: alpha(theme.palette.background.paper, 0.8),
              transition: 'all 0.2s',
              '&:hover': {
                backgroundColor: alpha(theme.palette.background.paper, 1),
                '& .MuiOutlinedInput-notchedOutline': {
                  borderColor: alpha(theme.palette.primary.main, 0.3),
                },
              },
              '&.Mui-focused': {
                backgroundColor: theme.palette.background.paper,
              },
            }}
          >
            {sync.supportedStrategies.map((strategy) => (
              <MenuItem key={strategy} value={strategy} sx={{ fontSize: '0.875rem' }}>
                {strategy.replace('_', ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Paper>

      {/* Scheduled Sync Configuration */}
      {formData.selectedStrategy === 'SCHEDULED' && (
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            borderRadius: 1.25,
            bgcolor: isDark
              ? alpha(theme.palette.background.paper, 0.4)
              : theme.palette.background.paper,
            borderColor: isDark
              ? alpha(theme.palette.divider, 0.12)
              : alpha(theme.palette.divider, 0.1),
            boxShadow: isDark
              ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
              : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
            <Box
              sx={{
                p: 0.625,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.warning.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify icon={clockIcon} width={16} color={theme.palette.warning.main} />
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  color: theme.palette.text.primary,
                  lineHeight: 1.4,
                }}
              >
                Scheduled Sync Settings
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  fontSize: '0.75rem',
                  lineHeight: 1.3,
                }}
              >
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
            borderRadius: 1.25,
            bgcolor: isDark
              ? alpha(theme.palette.background.paper, 0.4)
              : theme.palette.background.paper,
            borderColor: isDark
              ? alpha(theme.palette.divider, 0.12)
              : alpha(theme.palette.divider, 0.1),
            boxShadow: isDark
              ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
              : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2 }}>
            <Box
              sx={{
                p: 0.625,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.text.primary, 0.05),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify icon={optionsIcon} width={16} color={theme.palette.text.secondary} />
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  color: theme.palette.text.primary,
                  lineHeight: 1.4,
                }}
              >
                Additional Settings
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  fontSize: '0.75rem',
                  lineHeight: 1.3,
                }}
              >
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

      {/* Collapsible Documentation Links */}
      <Box >
        {connectorConfig.config.documentationLinks &&
          connectorConfig.config.documentationLinks.length > 0 && (
            <Paper
              variant="outlined"
              sx={{
                borderRadius: 1.25,
                overflow: 'hidden',
                bgcolor: isDark
                  ? alpha(theme.palette.info.main, 0.08)
                  : alpha(theme.palette.info.main, 0.025),
                borderColor: isDark
                  ? alpha(theme.palette.info.main, 0.25)
                  : alpha(theme.palette.info.main, 0.12),
                mb: 2,
                alignItems: 'center',
              }}
            >
              <Box
                onClick={() => setShowDocs(!showDocs)}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  p: 1.5,
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                  '&:hover': { bgcolor: alpha(theme.palette.info.main, 0.04) },
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                  <Box
                    sx={{
                      p: 0.625,
                      borderRadius: 1,
                      bgcolor: alpha(theme.palette.info.main, 0.12),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Iconify icon={bookIcon} width={16} color={theme.palette.info.main} />
                  </Box>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontSize: '0.875rem',
                      fontWeight: 600,
                      color: theme.palette.info.main,
                    }}
                  >
                    Documentation & Resources
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      px: 1,
                      py: 0.375,
                      borderRadius: 0.75,
                      bgcolor: alpha(theme.palette.info.main, 0.12),
                      color: theme.palette.info.main,
                      fontSize: '0.75rem',
                      fontWeight: 600,
                    }}
                  >
                    {connectorConfig.config.documentationLinks.length}
                  </Typography>
                </Box>
                <Iconify
                  icon={chevronDownIcon}
                  width={20}
                  color={theme.palette.text.secondary}
                  sx={{
                    transform: showDocs ? 'rotate(180deg)' : 'rotate(0deg)',
                    transition: 'transform 0.2s',
                  }}
                />
              </Box>

              <Collapse in={showDocs}>
                <Box sx={{ px: 1.5, pb: 1.5, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                  {connectorConfig.config.documentationLinks.map((link, index) => (
                    <Box
                      key={index}
                      onClick={(e) => {
                        e.stopPropagation();
                        window.open(link.url, '_blank');
                      }}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      p: 1,
                      borderRadius: 1,
                      border: `1px solid ${alpha(theme.palette.divider, isDark ? 0.12 : 0.1)}`,
                      bgcolor: isDark
                        ? alpha(theme.palette.background.paper, 0.5)
                        : theme.palette.background.paper,
                      cursor: 'pointer',
                      transition: 'all 0.2s',
                      '&:hover': {
                        borderColor: alpha(theme.palette.info.main, isDark ? 0.4 : 0.25),
                        bgcolor: isDark
                          ? alpha(theme.palette.info.main, 0.12)
                          : alpha(theme.palette.info.main, 0.03),
                        transform: 'translateX(4px)',
                        boxShadow: `0 2px 8px ${alpha(theme.palette.info.main, isDark ? 0.2 : 0.08)}`,
                      },
                    }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          fontSize: '0.8125rem',
                          fontWeight: 500,
                          color: theme.palette.text.primary,
                          flex: 1,
                        }}
                      >
                        {link.title}
                      </Typography>
                      <Box
                        sx={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 0.5,
                        }}
                      >
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: '0.6875rem',
                            fontWeight: 500,
                            textTransform: 'uppercase',
                            letterSpacing: 0.3,
                            color: theme.palette.info.main,
                          }}
                        >
                          {link.type}
                        </Typography>
                        <Iconify
                          icon={openInNewIcon}
                          width={14}
                          color={theme.palette.text.secondary}
                          sx={{ opacity: 0.6 }}
                        />
                      </Box>
                    </Box>
                  ))}
                </Box>
              </Collapse>
            </Paper>
          )}
      </Box>
      {/* Sync Strategy Info */}
      <Alert
        severity="info"
        variant="outlined"
        sx={{
          borderRadius: 1.25,
          py: 1,
          px: 1.75,
          '& .MuiAlert-icon': { fontSize: '1.25rem', py: 0.5 },
          '& .MuiAlert-message': { py: 0.25 },
          alignItems: 'center',
        }}
      >
        <Typography variant="body2" sx={{ fontSize: '0.875rem', lineHeight: 1.5 }}>
          {sync.supportedStrategies.includes('WEBHOOK') &&
            'Webhook: Real-time updates when data changes. '}
          {sync.supportedStrategies.includes('SCHEDULED') &&
            'Scheduled: Periodic sync at regular intervals. '}
          {sync.supportedStrategies.includes('MANUAL') && 'Manual: On-demand sync when triggered. '}
          {sync.supportedStrategies.includes('REALTIME') &&
            'Real-time: Continuous sync for live updates.'}
        </Typography>
      </Alert>
    </Box>
  );
};

export default SyncSection;
