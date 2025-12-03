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
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Checkbox,
  FormControlLabel,
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
  const indexingFilters = filters?.indexing;

  const hasSyncFilters = syncFilters?.schema?.fields && syncFilters.schema.fields.length > 0;
  const hasIndexingFilters = indexingFilters?.schema?.fields && indexingFilters.schema.fields.length > 0;

  if (!hasSyncFilters && !hasIndexingFilters) {
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

  const formatOperatorLabel = (operator: string): string =>
    operator
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');

  // Helper function to normalize datetime values to {start, end} format
  const normalizeDatetimeValue = (value: any, operator: string): { start: string; end: string } => {
    // If already in {start, end} format, handle based on operator
    if (value && typeof value === 'object' && 'start' in value && 'end' in value) {
      // For is_between, ensure we have a proper object structure
      if (operator === 'is_between') {
        return { 
          start: typeof value.start === 'string' ? value.start : '', 
          end: typeof value.end === 'string' ? value.end : '' 
        };
      }
      // For other operators, return as is
      return { start: value.start || '', end: value.end || '' };
    }
    
    // Convert single string value to {start, end} format based on operator
    if (typeof value === 'string' && value !== '') {
      if (operator === 'is_before') {
        return { start: '', end: value };
      }
      if (operator === 'is_after') {
        return { start: value, end: '' };
      }
      if (operator === 'is_between') {
        // For is_between, initialize with empty values
        return { start: '', end: '' };
      }
      // Default: put in start (for is_after)
      return { start: value, end: '' };
    }
    
    // Default empty format
    return { start: '', end: '' };
  };

  const getFilterValue = (fieldName: string, filterSchema?: any) => {
    const filterData = formData[fieldName] || {};
    const schema = filterSchema || syncFilters?.schema;
    const field = schema?.fields?.find((f: any) => f.name === fieldName);
    
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
    } else if (field?.filterType === 'datetime') {
      // For datetime, initialize as {start, end} format
      value = { start: '', end: '' };
    } else {
      value = '';
    }
    
    // Normalize datetime values to {start, end} format
    if (field?.filterType === 'datetime' && value !== null && !operator.startsWith('last_')) {
      // If value is corrupted (e.g., "[object Object]" string or invalid object), reset it
      if (typeof value === 'string' && value.includes('[object Object]')) {
        value = { start: '', end: '' };
      } else if (value && typeof value === 'object' && !Array.isArray(value)) {
        // Ensure it's a valid object with start and end properties
        if (!('start' in value) || !('end' in value)) {
          value = { start: '', end: '' };
        } else {
          value = normalizeDatetimeValue(value, operator);
        }
      } else {
        value = normalizeDatetimeValue(value, operator);
      }
    }
    
    return {
      operator,
      value,
    };
  };

  const handleOperatorChange = (fieldName: string, operator: string, filterSchema?: any) => {
    const schema = filterSchema || syncFilters?.schema;
    const field = schema?.fields?.find((f: any) => f.name === fieldName);
    
    // Reset value when operator changes, except for relative date operators
    let newValue: any = null;
    if (operator.startsWith('last_')) {
      // For relative date operators, value is not needed
      newValue = null;
    } else if (field?.filterType === 'datetime' && !operator.startsWith('last_')) {
      // For datetime, always use {start, end} format
      if (operator === 'is_between') {
        // For is_between, always reset to empty {start, end} to allow fresh selection
        newValue = { start: '', end: '' };
      } else {
        // For other operators, get current value and normalize it
        const currentValue = getFilterValue(fieldName);
        if (currentValue.value && typeof currentValue.value === 'object' && 'start' in currentValue.value) {
          // Keep existing {start, end} but adjust based on operator
          newValue = normalizeDatetimeValue(currentValue.value, operator);
        } else {
          // Convert string value or initialize empty
          newValue = normalizeDatetimeValue(currentValue.value, operator);
        }
      }
    } else if (field?.filterType === 'list') {
      newValue = [];
    } else {
      // For other types, get current value
      const currentValue = getFilterValue(fieldName);
      newValue = currentValue.value;
    }

    onFieldChange('filters', fieldName, { operator, value: newValue, type: field?.filterType });
  };

  const handleValueChange = (fieldName: string, value: any, filterSchema?: any) => {
    const schema = filterSchema || syncFilters?.schema;
    const currentValue = getFilterValue(fieldName, schema);
    const field = schema?.fields?.find((f: any) => f.name === fieldName);
    
    // For boolean filters, use the default operator from the field definition
    if (field?.filterType === 'boolean') {
      const operator = field.defaultOperator || currentValue.operator || '';
      onFieldChange('filters', fieldName, { operator, value, type: field.filterType });
      return;
    }
    
    // For datetime fields, normalize the value to {start, end} format
    let normalizedValue = value;
    if (field?.filterType === 'datetime' && currentValue.operator && !currentValue.operator.startsWith('last_')) {
      if (currentValue.operator === 'is_between') {
        // For is_between, value should already be {start, end}
        normalizedValue = value && typeof value === 'object' ? value : { start: '', end: '' };
      } else {
        // For single date operators, convert string to {start, end} format
        normalizedValue = normalizeDatetimeValue(value, currentValue.operator);
      }
    }
    
    onFieldChange('filters', fieldName, { ...currentValue, value: normalizedValue, type: field?.filterType });
  };

  const renderFilterField = (field: any, index: number, totalFields: number, filterSchema?: any) => {
    const schema = filterSchema || syncFilters?.schema;
    const filterValue = getFilterValue(field.name, schema);
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
        handleOperatorChange(field.name, operatorEntry, schema);
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
                  onChange={(value) => handleValueChange(field.name, value, schema)}
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
      const isBetween = filterValue.operator === 'is_between';

      // For relative dates, no value input needed
      if (isRelativeDate) {
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
        // Ensure value is always a proper object with start and end
        let rangeValue: { start: string; end: string };
        if (
          filterValue.value &&
          typeof filterValue.value === 'object' &&
          !Array.isArray(filterValue.value) &&
          'start' in filterValue.value &&
          'end' in filterValue.value
        ) {
          // Validate and ensure both start and end are strings
          const startVal = filterValue.value.start;
          const endVal = filterValue.value.end;
          rangeValue = {
            start: typeof startVal === 'string' ? startVal : String(startVal || ''),
            end: typeof endVal === 'string' ? endVal : String(endVal || ''),
          };
        } else {
          // If value is not in correct format, reset to empty
          rangeValue = { start: '', end: '' };
        }
        
        const dateRangeField = {
          name: `${field.name}_range`,
          displayName: field.displayName,
          fieldType: 'DATETIMERANGE' as const,
          required: field.required,
          placeholder: '',
          description: field.description || '',
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
                  onChange={(value) => handleValueChange(field.name, value, schema)}
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

      // For single date operators - extract value from {start, end} format
      const datetimeValue = filterValue.value && typeof filterValue.value === 'object' && 'start' in filterValue.value
        ? (filterValue.operator === 'is_before'
            ? filterValue.value.end
            : filterValue.value.start)
        : (typeof filterValue.value === 'string' ? filterValue.value : '');
      
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
                  value={datetimeValue}
                  onChange={(value) => handleValueChange(field.name, value, schema)}
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

    // Handle boolean type filters (for indexing filters - just checkboxes)
    if (field.filterType === 'boolean') {
      const booleanValue = filterValue.value !== undefined && filterValue.value !== null
        ? filterValue.value
        : (field.defaultValue !== undefined ? field.defaultValue : false);

      return (
        <Grid item xs={12} sm={6} md={4} key={field.name}>
          <FormControlLabel
            control={
              <Checkbox
                checked={booleanValue}
                onChange={(e) => handleValueChange(field.name, e.target.checked, schema)}
                size="small"
              />
            }
            label={
              <Box>
                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: 500,
                    fontSize: '0.875rem',
                    color: theme.palette.text.primary,
                  }}
                >
                  {field.displayName}
                  {field.required && (
                    <Typography component="span" sx={{ color: theme.palette.error.main, ml: 0.5 }}>
                      *
                    </Typography>
                  )}
                </Typography>
                {field.description && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{
                      display: 'block',
                      fontSize: '0.75rem',
                      lineHeight: 1.4,
                      mt: 0.25,
                    }}
                  >
                    {field.description}
                  </Typography>
                )}
              </Box>
            }
          />
        </Grid>
      );
    }

    return null;
  };

  const renderFilterSection = (
    title: string,
    description: string,
    filterSchema: any,
    filterType: 'sync' | 'indexing'
  ) => {
    if (!filterSchema?.fields || filterSchema.fields.length === 0) {
      return null;
    }

    const filterFields = filterSchema.fields;
    const isBooleanOnly = filterFields.every((f: any) => f.filterType === 'boolean');

    return (
      <Accordion
        defaultExpanded={false}
        sx={{
          borderRadius: 1.25,
          border: `1px solid ${isDark ? alpha(theme.palette.divider, 0.12) : alpha(theme.palette.divider, 0.1)}`,
          boxShadow: isDark
            ? `0 1px 2px ${alpha(theme.palette.common.black, 0.2)}`
            : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
          bgcolor: isDark
            ? alpha(theme.palette.background.paper, 0.4)
            : theme.palette.background.paper,
          '&:before': { display: 'none' },
          '&.Mui-expanded': {
            margin: 0,
          },
        }}
      >
        <AccordionSummary
          expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" width={20} />}
          sx={{
            px: 2,
            py: 1.5,
            '& .MuiAccordionSummary-content': {
              my: 0,
            },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, width: '100%' }}>
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
                {title}
              </Typography>
              <Typography 
                variant="caption" 
                color="text.secondary" 
                sx={{ 
                  fontSize: '0.75rem',
                  lineHeight: 1.3,
                }}
              >
                {description}
              </Typography>
            </Box>
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 2, pb: 2 }}>
          {isBooleanOnly ? (
            <Grid container spacing={2}>
              {filterFields.map((field: any) => renderFilterField(field, 0, 1, filterSchema))}
            </Grid>
          ) : (
            <Box>
              {filterFields.map((field: any, index: number) => 
                renderFilterField(field, index, filterFields.length, filterSchema)
              )}
            </Box>
          )}
        </AccordionDetails>
      </Accordion>
    );
  };

  return (
    <Box 
      sx={{ 
        display: 'flex', 
        flexDirection: 'column', 
        gap: 1.5,
        height: '100%',
      }}
    >
      {hasSyncFilters && renderFilterSection(
        'Sync Filters',
        'Configure filters to control what data is synchronized',
        syncFilters.schema,
        'sync'
      )}

      {hasIndexingFilters && renderFilterSection(
        'Indexing Filters',
        'Configure filters to control what data is indexed',
        indexingFilters.schema,
        'indexing'
      )}

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