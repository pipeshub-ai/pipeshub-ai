import React from 'react';
import {
  Paper,
  Box,
  Typography,
  Grid,
  alpha,
  useTheme,
  Alert,
  Divider,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import filterIcon from '@iconify-icons/mdi/filter-outline';
import { ConnectorConfig } from '../../types/types';
import { FieldRenderer } from '../field-renderers';

interface FiltersSectionProps {
  connectorConfig: ConnectorConfig | null;
  formData: Record<string, any>;
  formErrors: Record<string, string>;
  onFieldChange: (section: string, fieldName: string, value: any) => void;
}

const FiltersSection: React.FC<FiltersSectionProps> = ({
  connectorConfig,
  formData,
  formErrors,
  onFieldChange,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  if (!connectorConfig) return null;

  const { filters } = connectorConfig.config;
  const syncFilters = filters?.sync;

  if (!syncFilters || !syncFilters.schema || !syncFilters.schema.fields || syncFilters.schema.fields.length === 0) {
    return (
      <Alert 
        severity="info" 
        variant="outlined" 
        sx={{ 
          borderRadius: 1.25, 
          py: 1,
          px: 1.75,
          '& .MuiAlert-icon': { fontSize: '1.25rem', py: 0.5 },
          '& .MuiAlert-message': { py: 0.25 },
        }}
      >
        <Typography variant="body2" sx={{ fontSize: '0.875rem', lineHeight: 1.5 }}>
          No filters available for this connector.
        </Typography>
      </Alert>
    );
  }

  const formatOperatorLabel = (operator: string): string => {
    const operatorMap: Record<string, string> = {
      in: 'In',
      not_in: 'Not In',
      last_7_days: 'Last 7 Days',
      last_14_days: 'Last 14 Days',
      last_30_days: 'Last 30 Days',
      last_90_days: 'Last 90 Days',
      last_180_days: 'Last 180 Days',
      last_365_days: 'Last 365 Days',
      empty: 'Empty',
      is: 'Is',
      is_after: 'Is After',
      is_on_or_after: 'Is On Or After',
      is_on_or_before: 'Is On Or Before',
      is_before: 'Is Before',
      is_between: 'Is Between',
    };
    return operatorMap[operator] || operator.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
  };

  const getFilterValue = (fieldName: string) => {
    const filterData = formData[fieldName] || {};
    const field = syncFilters.schema?.fields?.find((f: any) => f.name === fieldName);
    
    // Get operator, defaulting to field default or empty string
    const operator = filterData.operator !== undefined && filterData.operator !== null
      ? filterData.operator
      : (field?.defaultOperator || '');
    
    // Get value - handle null explicitly for relative date operators
    let value;
    if (filterData.value !== undefined) {
      value = filterData.value;
    } else if (field?.defaultValue !== undefined) {
      value = field.defaultValue;
    } else if (field?.filterType === 'list') {
      value = [];
    } else {
      value = '';
    }
    
    return {
      operator,
      value,
    };
  };

  const handleOperatorChange = (fieldName: string, operator: string) => {
    const currentValue = getFilterValue(fieldName);
    const field = syncFilters.schema?.fields?.find((f: any) => f.name === fieldName);
    
    // Reset value when operator changes, except for relative date operators
    let newValue = currentValue.value;
    if (operator.startsWith('last_')) {
      // For relative date operators, value is not needed
      newValue = null;
    } else if (operator === 'empty') {
      newValue = null;
    } else if (operator === 'is_between' && field?.filterType === 'datetime') {
      newValue = { start: '', end: '' };
    } else if (field?.filterType === 'datetime' && !operator.startsWith('last_') && operator !== 'empty') {
      newValue = '';
    } else if (field?.filterType === 'list') {
      newValue = [];
    }

    onFieldChange('filters', fieldName, { operator, value: newValue });
  };

  const handleValueChange = (fieldName: string, value: any) => {
    const currentValue = getFilterValue(fieldName);
    onFieldChange('filters', fieldName, { ...currentValue, value });
  };

  const renderFilterField = (field: any, index: number, totalFields: number) => {
    const filterValue = getFilterValue(field.name);
    const error = formErrors[field.name];

    // Convert filter field to standard field format for FieldRenderer
    const createOperatorField = () => ({
      name: `${field.name}_operator`,
      displayName: 'Operator',
      fieldType: 'SELECT' as const,
      required: false,
      placeholder: 'Select operator',
      options: field.operators.map((op: string) => formatOperatorLabel(op)),
      description: '',
      defaultValue: '',
      validation: {},
      isSecret: false,
    });

    const getOperatorValue = () => {
      const rawOperator = filterValue.operator;
      return formatOperatorLabel(rawOperator);
    };

    const handleOperatorFieldChange = (formattedValue: string) => {
      // Convert formatted label back to operator key
      const operatorEntry = field.operators.find(
        (op: string) => formatOperatorLabel(op) === formattedValue
      );
      if (operatorEntry) {
        handleOperatorChange(field.name, operatorEntry);
      }
    };

    if (field.filterType === 'list') {
      const valueField = {
        name: `${field.name}_value`,
        displayName: field.displayName,
        fieldType: 'TAGS' as const,
        required: field.required,
        placeholder: field.description || `Enter ${field.displayName.toLowerCase()}`,
        description: '',
        defaultValue: [],
        validation: {},
        isSecret: false,
      };

      return (
        <Box key={field.name}>
          <Box sx={{ mb: 2.5 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 600,
                fontSize: '0.875rem',
                color: theme.palette.text.primary,
                mb: 1.5,
                display: 'flex',
                alignItems: 'center',
                gap: 0.5,
              }}
            >
              {field.displayName}
              {field.required && (
                <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.875rem' }}>
                  *
                </Typography>
              )}
            </Typography>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={4}>
                <FieldRenderer
                  field={createOperatorField()}
                  value={getOperatorValue()}
                  onChange={handleOperatorFieldChange}
                  error={undefined}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={valueField}
                  value={Array.isArray(filterValue.value) ? filterValue.value : []}
                  onChange={(value) => handleValueChange(field.name, value)}
                  error={error}
                />
              </Grid>
            </Grid>
            {field.description && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  display: 'block',
                  mt: 1,
                  ml: 1,
                  fontSize: '0.8125rem',
                  lineHeight: 1.5,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
            {index < totalFields - 1 && (
            <Divider sx={{ my: 3, borderColor: alpha(theme.palette.divider, isDark ? 0.12 : 0.08) }} />
          )}
        </Box>
      );
    }

    if (field.filterType === 'datetime') {
      const isRelativeDate = filterValue.operator?.startsWith('last_');
      const isEmpty = filterValue.operator === 'empty';
      const isBetween = filterValue.operator === 'is_between';

      // For relative dates and empty, no value input needed
      if (isRelativeDate || isEmpty) {
        const disabledField = {
          name: `${field.name}_disabled`,
          displayName: '',
          fieldType: 'TEXT' as const,
          required: false,
          placeholder: '',
          description: '',
          defaultValue: '',
          validation: {},
          isSecret: false,
        };

        return (
          <Box key={field.name}>
            <Box sx={{ mb: 2.5 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  color: theme.palette.text.primary,
                  mb: 1.5,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.875rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={4}>
                  <FieldRenderer
                    field={createOperatorField()}
                    value={getOperatorValue()}
                    onChange={handleOperatorFieldChange}
                    error={undefined}
                  />
                </Grid>
                <Grid item xs={12} sm={8}>
                  <FieldRenderer
                    field={disabledField}
                    value={formatOperatorLabel(filterValue.operator)}
                    onChange={() => {}}
                    error={undefined}
                    disabled
                  />
                </Grid>
              </Grid>
              {field.description && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    display: 'block',
                    mt: 1,
                    ml: 1,
                    fontSize: '0.8125rem',
                    lineHeight: 1.5,
                  }}
                >
                  {field.description}
                </Typography>
              )}
            </Box>
            {index < totalFields - 1 && (
            <Divider sx={{ my: 3, borderColor: alpha(theme.palette.divider, isDark ? 0.12 : 0.08) }} />
          )}
          </Box>
        );
      }

      // For date range (is_between)
      if (isBetween) {
        const rangeValue = filterValue.value || { start: '', end: '' };
        const dateRangeField = {
          name: `${field.name}_range`,
          displayName: '',
          fieldType: 'DATERANGE' as const,
          required: field.required,
          placeholder: '',
          description: '',
          defaultValue: { start: '', end: '' },
          validation: {},
          isSecret: false,
        };

        return (
          <Box key={field.name}>
            <Box sx={{ mb: 2.5 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  color: theme.palette.text.primary,
                  mb: 1.5,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.875rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={4}>
                  <FieldRenderer
                    field={createOperatorField()}
                    value={getOperatorValue()}
                    onChange={handleOperatorFieldChange}
                    error={undefined}
                  />
                </Grid>
                <Grid item xs={12} sm={8}>
                  <FieldRenderer
                    field={dateRangeField}
                    value={rangeValue}
                    onChange={(value) => handleValueChange(field.name, value)}
                    error={error}
                  />
                </Grid>
              </Grid>
              {field.description && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    display: 'block',
                    mt: 1,
                    ml: 1,
                    fontSize: '0.8125rem',
                    lineHeight: 1.5,
                  }}
                >
                  {field.description}
                </Typography>
              )}
            </Box>
            {index < totalFields - 1 && (
            <Divider sx={{ my: 3, borderColor: alpha(theme.palette.divider, isDark ? 0.12 : 0.08) }} />
          )}
          </Box>
        );
      }

      // For single date operators
      const dateField = {
        name: `${field.name}_date`,
        displayName: '',
        fieldType: 'DATETIME' as const,
        required: field.required,
        placeholder: '',
        description: '',
        defaultValue: '',
        validation: {},
        isSecret: false,
      };

      return (
        <Box key={field.name}>
          <Box sx={{ mb: 2.5 }}>
            <Typography
              variant="body2"
              sx={{
                fontWeight: 600,
                fontSize: '0.875rem',
                color: theme.palette.text.primary,
                mb: 1.5,
                display: 'flex',
                alignItems: 'center',
                gap: 0.5,
              }}
            >
              {field.displayName}
              {field.required && (
                <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.875rem' }}>
                  *
                </Typography>
              )}
            </Typography>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={4}>
                <FieldRenderer
                  field={createOperatorField()}
                  value={getOperatorValue()}
                  onChange={handleOperatorFieldChange}
                  error={undefined}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={dateField}
                  value={filterValue.value || ''}
                  onChange={(value) => handleValueChange(field.name, value)}
                  error={error}
                />
              </Grid>
            </Grid>
            {field.description && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  display: 'block',
                  mt: 1,
                  ml: 1,
                  fontSize: '0.8125rem',
                  lineHeight: 1.5,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
            {index < totalFields - 1 && (
            <Divider sx={{ my: 3, borderColor: alpha(theme.palette.divider, isDark ? 0.12 : 0.08) }} />
          )}
        </Box>
      );
    }

    return null;
  };

  const filterFields = syncFilters.schema?.fields || [];

  return (
    <Box 
      sx={{ 
        display: 'flex', 
        flexDirection: 'column', 
        gap: 1.5,
        height: '100%',
      }}
    >
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
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 2.5 }}>
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
            <Iconify icon={filterIcon} width={16} color={theme.palette.primary.main} />
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
              Sync Filters
            </Typography>
            <Typography 
              variant="caption" 
              color="text.secondary" 
              sx={{ 
                fontSize: '0.75rem',
                lineHeight: 1.3,
              }}
            >
              Configure filters to control what data is synchronized
            </Typography>
          </Box>
        </Box>

        <Box sx={{ flex: 1 }}>
          {filterFields.map((field: any, index: number) => 
            renderFilterField(field, index, filterFields.length)
          )}
        </Box>
      </Paper>

      <Alert 
        severity="info" 
        variant="outlined" 
        sx={{ 
          borderRadius: 1.25,
          py: 1,
          px: 1.75,
          flexShrink: 0,
          bgcolor: isDark
            ? alpha(theme.palette.info.main, 0.08)
            : alpha(theme.palette.info.main, 0.02),
          borderColor: isDark
            ? alpha(theme.palette.info.main, 0.25)
            : alpha(theme.palette.info.main, 0.1),
          '& .MuiAlert-icon': { fontSize: '1.25rem', py: 0.5 },
          '& .MuiAlert-message': { py: 0.25 },
        }}
      >
        <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.5, fontWeight: 400 }}>
          Filters are optional. Leave them empty to sync all available data.
        </Typography>
      </Alert>
    </Box>
  );
};

export default FiltersSection;