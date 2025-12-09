import React, { useState, useMemo, useEffect } from 'react';
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
  Button,
  IconButton,
  Autocomplete,
  TextField,
  Popper,
  Chip,
  Tooltip,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import filterIcon from '@iconify-icons/mdi/filter-outline';
import addIcon from '@iconify-icons/mdi/plus';
import removeIcon from '@iconify-icons/mdi/delete-outline';
import infoIcon from '@iconify-icons/mdi/information-outline';
import { 
  ConnectorConfig, 
  FilterSchemaField, 
  FilterValueData,
  FilterValue,
  DatetimeRange 
} from '../../types/types';
import { 
  convertEpochToISOString,
  normalizeDatetimeValueForDisplay
} from '../../utils/time-utils';
import { FieldRenderer } from '../field-renderers';

interface FiltersSectionProps {
  connectorConfig: ConnectorConfig | null;
  formData: Record<string, FilterValueData>;
  formErrors: Record<string, string>;
  onFieldChange: (section: string, fieldName: string, value: FilterValueData | undefined) => void;
  onRemoveFilter?: (section: string, fieldName: string) => void;
}

const FiltersSection: React.FC<FiltersSectionProps> = ({
  connectorConfig,
  formData,
  formErrors,
  onFieldChange,
  onRemoveFilter,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [addMenuAnchor, setAddMenuAnchor] = useState<{ [key: string]: HTMLElement | null }>({});
  const [expandedAccordions, setExpandedAccordions] = useState<{ [key: string]: boolean }>({});
  const [autocompleteOpen, setAutocompleteOpen] = useState<{ [key: string]: boolean }>({});

  // Initialize indexing filters with default values on mount
  useEffect(() => {
    if (!connectorConfig) return;

    const indexingSchema = connectorConfig.config.filters?.indexing?.schema;
    if (!indexingSchema?.fields) return;

    // Initialize each indexing filter field with its default value if not already set
    indexingSchema.fields.forEach((field) => {
      const existingValue = formData[field.name];
      
      // Only initialize if not already set in formData
      if (existingValue === undefined || existingValue === null) {
        let defaultValue: FilterValue;
        if (field.defaultValue !== undefined) {
          defaultValue = field.defaultValue;
        } else if (field.filterType === 'boolean') {
          defaultValue = true;
        } else if (field.filterType === 'list' || field.filterType === 'multiselect') {
          defaultValue = [];
        } else if (field.filterType === 'datetime') {
          defaultValue = { start: '', end: '' };
        } else {
          defaultValue = '';
        }
        
        const defaultOperator = field.defaultOperator || 
          (field.filterType === 'boolean' ? 'equals' : '');

        onFieldChange('filters', field.name, {
          operator: defaultOperator,
          value: defaultValue,
          type: field.filterType,
        });
      }
    });
  // Only run once when connectorConfig is first available
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectorConfig?.name]);

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

  /**
   * Format operator string to display label
   * Example: "is_before" -> "Is Before"
   */
  const formatOperatorLabel = (operator: string): string =>
    operator
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');

  /**
   * Get filter value with proper defaults and normalization
   */
  const getFilterValue = (
    fieldName: string, 
    filterSchema?: { fields: FilterSchemaField[] }
  ): FilterValueData => {
    const filterData = formData[fieldName];
    const schema = filterSchema || syncFilters?.schema;
    const field = schema?.fields?.find((f) => f.name === fieldName);
    
    if (!field) {
      return { operator: '', value: '' };
    }
    
    // Get operator, defaulting to field default or empty string
    const operator = filterData?.operator ?? field.defaultOperator ?? '';
    
    // Get value with proper defaults based on filter type
    let value: FilterValue = filterData?.value;
    
    if (value === undefined) {
      if (field.defaultValue !== undefined) {
        value = field.defaultValue;
      } else if (field.filterType === 'list' || field.filterType === 'multiselect') {
        value = [];
      } else if (field.filterType === 'datetime') {
        value = { start: '', end: '' };
      } else if (field.filterType === 'boolean') {
        value = false;
      } else {
        value = '';
      }
    }
    
    // Normalize datetime values to {start, end} format for display
    if (field.filterType === 'datetime' && value !== null && !operator.startsWith('last_')) {
      // Handle corrupted values
      if (typeof value === 'string' && value.includes('[object Object]')) {
        value = { start: '', end: '' };
      } else {
        value = normalizeDatetimeValueForDisplay(value, operator);
      }
    }
    
    return { operator, value };
  };

  /**
   * Handle operator change for a filter field
   */
  const handleOperatorChange = (
    fieldName: string, 
    operator: string, 
    filterSchema?: { fields: FilterSchemaField[] }
  ) => {
    const schema = filterSchema || syncFilters?.schema;
    const field = schema?.fields?.find((f) => f.name === fieldName);
    
    if (!field) return;
    
    // Reset value when operator changes, except for relative date operators
    let newValue: FilterValue = null;
    
    if (operator.startsWith('last_')) {
      // For relative date operators, value is not needed
      newValue = null;
    } else if (field.filterType === 'datetime') {
      // For datetime, always use {start, end} format
      if (operator === 'is_between') {
        newValue = { start: '', end: '' };
      } else {
        const currentValue = getFilterValue(fieldName, schema);
        newValue = normalizeDatetimeValueForDisplay(currentValue.value, operator);
      }
    } else if (field.filterType === 'list' || field.filterType === 'multiselect') {
      newValue = [];
    } else {
      const currentValue = getFilterValue(fieldName, schema);
      newValue = currentValue.value;
    }

    onFieldChange('filters', fieldName, { 
      operator, 
      value: newValue, 
      type: field.filterType 
    });
  };

  /**
   * Handle value change for a filter field
   */
  const handleValueChange = (
    fieldName: string, 
    value: FilterValue, 
    filterSchema?: { fields: FilterSchemaField[] }
  ) => {
    const schema = filterSchema || syncFilters?.schema;
    const currentValue = getFilterValue(fieldName, schema);
    const field = schema?.fields?.find((f) => f.name === fieldName);
    
    if (!field) return;
    
    // For boolean filters, use the default operator from the field definition
    if (field.filterType === 'boolean') {
      const operator = field.defaultOperator || currentValue.operator || '';
      onFieldChange('filters', fieldName, { 
        operator, 
        value, 
        type: field.filterType 
      });
      return;
    }

    // For number filters, convert string to number
    if (field.filterType === 'number') {
      let numericValue: number | null = null;
      if (value !== '' && value !== null && value !== undefined) {
        const parsed = parseFloat(String(value));
        numericValue = Number.isNaN(parsed) ? null : parsed;
      }
      onFieldChange('filters', fieldName, { 
        ...currentValue, 
        value: numericValue, 
        type: field.filterType 
      });
      return;
    }
    
    // For datetime fields, normalize the value to {start, end} format
    let normalizedValue: FilterValue = value;
    if (
      field.filterType === 'datetime' && 
      currentValue.operator && 
      !currentValue.operator.startsWith('last_')
    ) {
      if (currentValue.operator === 'is_between') {
        // For is_between, value should already be {start, end}
        normalizedValue = (value && typeof value === 'object' && !Array.isArray(value) && 'start' in value)
          ? value 
          : { start: '', end: '' };
      } else {
        // For single date operators, convert string to {start, end} format
        normalizedValue = normalizeDatetimeValueForDisplay(value, currentValue.operator);
      }
    }
    
    onFieldChange('filters', fieldName, { 
      ...currentValue, 
      value: normalizedValue, 
      type: field.filterType 
    });
  };

  /**
   * Get default value for a filter field based on its type
   */
  const getDefaultFilterValue = (field: FilterSchemaField): FilterValue => {
    if (field.defaultValue !== undefined) {
      return field.defaultValue;
    }
    
    switch (field.filterType) {
      case 'list':
      case 'multiselect':
        return [];
      case 'datetime':
        return { start: '', end: '' };
      case 'boolean':
        return true;
      default:
        return '';
    }
  };

  /**
   * Handle adding a new filter
   */
  const handleAddFilter = (filterType: 'sync' | 'indexing', fieldName: string) => {
    const schema = filterType === 'sync' ? syncFilters?.schema : indexingFilters?.schema;
    const field = schema?.fields?.find((f) => f.name === fieldName);
    
    if (!field) return;
    
    // Expand accordion first if it's closed
    const isCurrentlyExpanded = expandedAccordions[filterType] ?? false;
    if (!isCurrentlyExpanded) {
      setExpandedAccordions((prev) => ({ ...prev, [filterType]: true }));
    }
    
    // Initialize filter with default values
    const defaultValue = getDefaultFilterValue(field);
    
    // Add the filter
    onFieldChange('filters', fieldName, {
      operator: field.defaultOperator || '',
      value: defaultValue,
      type: field.filterType,
    });
    
    // Close menu after a brief delay to allow accordion expansion to be visible
    setTimeout(() => {
      setAddMenuAnchor((prev) => ({ ...prev, [filterType]: null }));
    }, 150);
  };

  const handleRemoveFilter = (fieldName: string) => {
    if (onRemoveFilter) {
      onRemoveFilter('filters', fieldName);
    } else {
      // Fallback: set to undefined
      onFieldChange('filters', fieldName, undefined);
    }
  };

  const handleAddMenuOpen = (filterType: 'sync' | 'indexing', event: React.MouseEvent<HTMLElement> | HTMLElement) => {
    const target = event instanceof HTMLElement ? event : event.currentTarget;
    setAddMenuAnchor((prev) => ({ ...prev, [filterType]: target }));
    setAutocompleteOpen((prev) => ({ ...prev, [filterType]: true }));
  };

  const handleAddMenuClose = (filterType: 'sync' | 'indexing') => {
    setAddMenuAnchor((prev) => ({ ...prev, [filterType]: null }));
    setAutocompleteOpen((prev) => ({ ...prev, [filterType]: false }));
  };

  const handleAutocompleteChange = (filterType: 'sync' | 'indexing', field: FilterSchemaField | null) => {
    if (field) {
      handleAddFilter(filterType, field.name);
      handleAddMenuClose(filterType);
    }
  };

  /**
   * Get available filters (not yet added)
   */
  const getAvailableFilters = (filterType: 'sync' | 'indexing'): FilterSchemaField[] => {
    const schema = filterType === 'sync' ? syncFilters?.schema : indexingFilters?.schema;
    const availableFields = schema?.fields || [];
    const activeFilterNames = Object.keys(formData).filter(
      (key) => formData[key] !== undefined && formData[key] !== null
    );
    
    return availableFields.filter((field) => !activeFilterNames.includes(field.name));
  };

  /**
   * Get active filters (already added)
   */
  const getActiveFilters = (filterType: 'sync' | 'indexing'): Array<FilterSchemaField & { filterId: string }> => {
    const schema = filterType === 'sync' ? syncFilters?.schema : indexingFilters?.schema;
    const availableFields = schema?.fields || [];
    const activeFilterNames = Object.keys(formData).filter(
      (key) => {
        const filterValue = formData[key];
        // Include if filter exists and has an operator (valid filter)
        return !!filterValue?.operator;
      }
    );
    
    // Use field name as stable ID (field names are unique)
    return availableFields
      .filter((field) => activeFilterNames.includes(field.name))
      .map((field) => ({
        ...field,
        filterId: field.name, // Use field name as stable key
      }));
  };

  /**
   * Render a single filter field
   */
  const renderFilterField = (
    field: FilterSchemaField & { filterId?: string }, 
    index: number, 
    totalFields: number, 
    filterSchema?: { fields: FilterSchemaField[] }, 
    showRemove: boolean = false
  ) => {
    const schema = filterSchema || syncFilters?.schema;
    const filterValue = getFilterValue(field.name, schema);
    const error = formErrors[field.name];

    // Convert filter field to standard field format for FieldRenderer
    const createOperatorField = () => {
      const operators = field.operators || [];
      return {
        name: `${field.name}_operator`,
        displayName: 'Operator',
        fieldType: 'SELECT' as const,
        required: false,
        placeholder: 'Select operator',
        options: operators.map((op) => formatOperatorLabel(op)),
        description: '',
        defaultValue: '',
        validation: {},
        isSecret: false,
      };
    };

    const getOperatorValue = () => {
      const rawOperator = filterValue.operator;
      return formatOperatorLabel(rawOperator);
    };

    const handleOperatorFieldChange = (formattedValue: string) => {
      // Convert formatted label back to operator key
      const operators = field.operators || [];
      const operatorEntry = operators.find(
        (op) => formatOperatorLabel(op) === formattedValue
      );
      if (operatorEntry) {
        handleOperatorChange(field.name, operatorEntry, schema);
      }
    };

    if (field.filterType === 'list') {
      const hasValues = filterValue.value && Array.isArray(filterValue.value) && filterValue.value.length > 0;
      const valueField = {
        name: `${field.name}_value`,
        displayName: field.displayName,
        fieldType: 'TAGS' as const,
        required: field.required,
        placeholder: field.description || `Enter ${field.displayName.toLowerCase()}`,
        description: hasValues ? '' : 'Enter a value and press Enter to add. Values are added as tags.',
        defaultValue: [],
        validation: {},
        isSecret: false,
      };

      return (
        <Box key={field.filterId || field.name}>
          <Box sx={{ mb: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  color: theme.palette.text.primary,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              {showRemove && (
                <IconButton
                  size="small"
                  onClick={() => handleRemoveFilter(field.name)}
                  sx={{
                    p: 0.5,
                    color: theme.palette.error.main,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    '&:hover': {
                      bgcolor: alpha(theme.palette.error.main, 0.15),
                      color: theme.palette.error.main,
                    },
                    transition: 'all 0.2s',
                  }}
                >
                  <Iconify icon={removeIcon} width={16} />
                </IconButton>
              )}
            </Box>
            <Grid container spacing={1.5}>
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
                  mt: 0.75,
                  ml: 0.5,
                  fontSize: '0.75rem',
                  lineHeight: 1.4,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
        </Box>
      );
    }

    if (field.filterType === 'multiselect') {
      // Multiselect: dropdown with predefined options from field.options
      const valueField = {
        name: `${field.name}_value`,
        displayName: field.displayName,
        fieldType: 'MULTISELECT' as const,
        required: field.required,
        placeholder: field.description || `Select ${field.displayName.toLowerCase()}`,
        description: '',
        defaultValue: [],
        options: field.options || [],
        validation: {},
        isSecret: false,
      };

      return (
        <Box key={field.filterId || field.name}>
          <Box sx={{ mb: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  color: theme.palette.text.primary,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              {showRemove && (
                <IconButton
                  size="small"
                  onClick={() => handleRemoveFilter(field.name)}
                  sx={{
                    p: 0.5,
                    color: theme.palette.error.main,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    '&:hover': {
                      bgcolor: alpha(theme.palette.error.main, 0.15),
                      color: theme.palette.error.main,
                    },
                    transition: 'all 0.2s',
                  }}
                >
                  <Iconify icon={removeIcon} width={16} />
                </IconButton>
              )}
            </Box>
            <Grid container spacing={1.5}>
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
                  mt: 0.75,
                  ml: 0.5,
                  fontSize: '0.75rem',
                  lineHeight: 1.4,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
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
            <Box sx={{ mb: 0 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.8125rem',
                    color: theme.palette.text.primary,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.5,
                  }}
                >
                  {field.displayName}
                  {field.required && (
                    <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                      *
                    </Typography>
                  )}
                </Typography>
                {showRemove && (
                  <IconButton
                    size="small"
                    onClick={() => handleRemoveFilter(field.name)}
                    sx={{
                      p: 0.5,
                      color: theme.palette.error.main,
                      bgcolor: alpha(theme.palette.error.main, 0.08),
                      '&:hover': {
                        bgcolor: alpha(theme.palette.error.main, 0.15),
                        color: theme.palette.error.main,
                      },
                      transition: 'all 0.2s',
                    }}
                  >
                    <Iconify icon={removeIcon} width={16} />
                  </IconButton>
                )}
              </Box>
              <Grid container spacing={1.5}>
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
                    mt: 0.75,
                    ml: 0.5,
                    fontSize: '0.75rem',
                    lineHeight: 1.4,
                  }}
                >
                  {field.description}
                </Typography>
              )}
            </Box>
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
          // Convert epoch milliseconds to ISO strings if needed
          const range = filterValue.value as DatetimeRange;
          rangeValue = {
            start: range.start != null ? convertEpochToISOString(range.start) : '',
            end: range.end != null ? convertEpochToISOString(range.end) : '',
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
          description: '',
          defaultValue: { start: '', end: '' },
          validation: {},
          isSecret: false,
        };

        return (
          <Box key={field.filterId || field.name}>
            <Box sx={{ mb: 0 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.8125rem',
                    color: theme.palette.text.primary,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.5,
                  }}
                >
                  {field.displayName}
                  {field.required && (
                    <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                      *
                    </Typography>
                  )}
                </Typography>
                {showRemove && (
                  <IconButton
                    size="small"
                    onClick={() => handleRemoveFilter(field.name)}
                    sx={{
                      p: 0.5,
                      color: theme.palette.error.main,
                      bgcolor: alpha(theme.palette.error.main, 0.08),
                      '&:hover': {
                        bgcolor: alpha(theme.palette.error.main, 0.15),
                        color: theme.palette.error.main,
                      },
                      transition: 'all 0.2s',
                    }}
                  >
                    <Iconify icon={removeIcon} width={16} />
                  </IconButton>
                )}
              </Box>
              <Grid container spacing={1.5}>
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
                    mt: 0.75,
                    ml: 0.5,
                    fontSize: '0.75rem',
                    lineHeight: 1.4,
                  }}
                >
                  {field.description}
                </Typography>
              )}
            </Box>
          </Box>
        );
      }

      // For single date operators - extract value from {start, end} format
      let datetimeValue: string;
      if (
        filterValue.value && 
        typeof filterValue.value === 'object' && 
        !Array.isArray(filterValue.value) &&
        'start' in filterValue.value
      ) {
        const range = filterValue.value as DatetimeRange;
        const rawValue = filterValue.operator === 'is_before' ? range.end : range.start;
        datetimeValue = convertEpochToISOString(rawValue);
      } else if (typeof filterValue.value === 'string') {
        datetimeValue = convertEpochToISOString(filterValue.value);
      } else if (typeof filterValue.value === 'number' && filterValue.value > 0) {
        datetimeValue = convertEpochToISOString(filterValue.value);
      } else {
        datetimeValue = '';
      }
      
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
        <Box key={field.filterId || field.name}>
          <Box sx={{ mb: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  color: theme.palette.text.primary,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              {showRemove && (
                <IconButton
                  size="small"
                  onClick={() => handleRemoveFilter(field.name)}
                  sx={{
                    p: 0.5,
                    color: theme.palette.error.main,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    '&:hover': {
                      bgcolor: alpha(theme.palette.error.main, 0.15),
                      color: theme.palette.error.main,
                    },
                    transition: 'all 0.2s',
                  }}
                >
                  <Iconify icon={removeIcon} width={16} />
                </IconButton>
              )}
            </Box>
            <Grid container spacing={1.5}>
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
                  mt: 0.75,
                  ml: 0.5,
                  fontSize: '0.75rem',
                  lineHeight: 1.4,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
        </Box>
      );
    }

    // Handle boolean type filters (for indexing filters - just checkboxes)
    if (field.filterType === 'boolean') {
      const booleanValue = filterValue.value !== undefined && filterValue.value !== null
        ? filterValue.value
        : (field.defaultValue !== undefined ? field.defaultValue : false);

      return (
        <Grid item xs={12} sm={6} md={4} key={field.filterId || field.name}>
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

    // Handle string (text) type filters
    if (field.filterType === 'text' || field.filterType === 'string') {
      const textValue = typeof filterValue.value === 'string' ? filterValue.value : '';
      const valueField = {
        name: `${field.name}_value`,
        displayName: field.displayName,
        fieldType: 'TEXT' as const,
        required: field.required,
        placeholder: field.description || `Enter ${field.displayName.toLowerCase()}`,
        description: '',
        defaultValue: '',
        validation: {},
        isSecret: false,
      };

      return (
        <Box key={field.filterId || field.name}>
          <Box sx={{ mb: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  color: theme.palette.text.primary,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              {showRemove && (
                <IconButton
                  size="small"
                  onClick={() => handleRemoveFilter(field.name)}
                  sx={{
                    p: 0.5,
                    color: theme.palette.error.main,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    '&:hover': {
                      bgcolor: alpha(theme.palette.error.main, 0.15),
                      color: theme.palette.error.main,
                    },
                    transition: 'all 0.2s',
                  }}
                >
                  <Iconify icon={removeIcon} width={16} />
                </IconButton>
              )}
            </Box>
            <Grid container spacing={1.5}>
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
                  value={textValue}
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
                  mt: 0.75,
                  ml: 0.5,
                  fontSize: '0.75rem',
                  lineHeight: 1.4,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
        </Box>
      );
    }

    // Handle number type filters
    if (field.filterType === 'number') {
      // Handle both number and string values (input fields return strings)
      const numberValue = filterValue.value !== null && filterValue.value !== undefined && filterValue.value !== '' 
        ? filterValue.value 
        : '';
      const valueField = {
        name: `${field.name}_value`,
        displayName: field.displayName,
        fieldType: 'NUMBER' as const,
        required: field.required,
        placeholder: field.description || `Enter ${field.displayName.toLowerCase()}`,
        description: '',
        defaultValue: '',
        validation: {},
        isSecret: false,
      };

      return (
        <Box key={field.filterId || field.name}>
          <Box sx={{ mb: 0 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.25 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  color: theme.palette.text.primary,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 0.5,
                }}
              >
                {field.displayName}
                {field.required && (
                  <Typography component="span" sx={{ color: theme.palette.error.main, fontSize: '0.8125rem' }}>
                    *
                  </Typography>
                )}
              </Typography>
              {showRemove && (
                <IconButton
                  size="small"
                  onClick={() => handleRemoveFilter(field.name)}
                  sx={{
                    p: 0.5,
                    color: theme.palette.error.main,
                    bgcolor: alpha(theme.palette.error.main, 0.08),
                    '&:hover': {
                      bgcolor: alpha(theme.palette.error.main, 0.15),
                      color: theme.palette.error.main,
                    },
                    transition: 'all 0.2s',
                  }}
                >
                  <Iconify icon={removeIcon} width={16} />
                </IconButton>
              )}
            </Box>
            <Grid container spacing={1.5}>
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
                  value={numberValue}
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
                  mt: 0.75,
                  ml: 0.5,
                  fontSize: '0.75rem',
                  lineHeight: 1.4,
                }}
              >
                {field.description}
              </Typography>
            )}
          </Box>
        </Box>
      );
    }

    return null;
  };

  /**
   * Render a filter section (sync or indexing) with accordion
   */
  const renderFilterSection = (
    title: string,
    description: string,
    filterSchema: { fields: FilterSchemaField[] },
    filterType: 'sync' | 'indexing'
  ) => {
    if (!filterSchema?.fields || filterSchema.fields.length === 0) {
      return null;
    }

    const activeFilters = getActiveFilters(filterType);
    const availableFilters = getAvailableFilters(filterType);
    const isBooleanOnly = filterSchema.fields.every((f) => f.filterType === 'boolean');

    const isExpanded = expandedAccordions[filterType] ?? false;

    return (
      <Accordion
        expanded={isExpanded}
        onChange={(_, expanded) => {
          setExpandedAccordions((prev) => ({ ...prev, [filterType]: expanded }));
        }}
        TransitionProps={{
          timeout: 300,
        }}
        sx={{
          borderRadius: 1.25,
          border: isExpanded
            ? `1.5px solid ${isDark ? alpha(theme.palette.primary.main, 0.3) : alpha(theme.palette.primary.main, 0.25)}`
            : `1.5px solid ${isDark ? alpha(theme.palette.common.white, 0.2) : alpha(theme.palette.common.black, 0.25)}`,
          boxShadow: isDark
            ? `0 1px 3px ${alpha(theme.palette.common.black, 0.2)}`
            : `0 1px 3px ${alpha(theme.palette.common.black, 0.05)}`,
          bgcolor: isDark
            ? alpha(theme.palette.background.paper, 0.4)
            : theme.palette.background.paper,
          transition: 'border-color 0.2s ease, box-shadow 0.2s ease',
          '&:before': { display: 'none' },
          '&.Mui-expanded': {
            margin: 0,
            border: `1.5px solid ${isDark ? alpha(theme.palette.primary.main, 0.3) : alpha(theme.palette.primary.main, 0.25)}`,
            boxShadow: isDark
              ? `0 2px 6px ${alpha(theme.palette.primary.main, 0.15)}`
              : `0 2px 6px ${alpha(theme.palette.primary.main, 0.1)}`,
          },
          '&:hover': {
            borderColor: isExpanded
              ? (isDark ? alpha(theme.palette.primary.main, 0.4) : alpha(theme.palette.primary.main, 0.35))
              : (isDark ? alpha(theme.palette.common.white, 0.3) : alpha(theme.palette.divider, 0.5)),
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
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
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
                {filterType === 'indexing' && (
                  <Tooltip
                    title={
                      <Box sx={{ p: 0.5 }}>
                        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.75, fontSize: '0.8125rem' }}>
                          What are Indexing Filters?
                        </Typography>
                        <Typography variant="body2" sx={{ mb: 1, fontSize: '0.75rem', lineHeight: 1.5 }}>
                          Indexing filters control which synced data gets processed and made searchable in the AI system.
                        </Typography>
                        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5, fontSize: '0.8125rem' }}>
                          How it works:
                        </Typography>
                        <Typography component="div" variant="body2" sx={{ fontSize: '0.75rem', lineHeight: 1.6 }}>
                          <strong>1. Sync Phase:</strong> Data is downloaded from the source (controlled by Sync Filters)
                          <br />
                          <strong>2. Index Phase:</strong> Downloaded data is processed for AI search (controlled by Indexing Filters)
                          <br />
                          <br />
                          <strong>Example:</strong> You can sync all pages but only index pages and comments, excluding attachments from AI search.
                        </Typography>
                      </Box>
                    }
                    arrow
                    placement="right"
                    enterDelay={200}
                    leaveDelay={200}
                    sx={{
                      '& .MuiTooltip-tooltip': {
                        maxWidth: 400,
                        bgcolor: isDark 
                          ? alpha(theme.palette.grey[900], 0.98)
                          : alpha(theme.palette.grey[800], 0.95),
                        boxShadow: `0 8px 24px ${alpha(theme.palette.common.black, 0.25)}`,
                        p: 1.5,
                      },
                      '& .MuiTooltip-arrow': {
                        color: isDark 
                          ? alpha(theme.palette.grey[900], 0.98)
                          : alpha(theme.palette.grey[800], 0.95),
                      },
                    }}
                  >
                    <Box
                      sx={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        cursor: 'help',
                        color: theme.palette.info.main,
                        transition: 'all 0.2s',
                        '&:hover': {
                          color: theme.palette.info.dark,
                          transform: 'scale(1.1)',
                        },
                      }}
                    >
                      <Iconify icon={infoIcon} width={16} />
                    </Box>
                  </Tooltip>
                )}
              </Box>
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
            {filterType === 'sync' && availableFilters.length > 0 && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<Iconify icon={addIcon} width={16} />}
                onClick={(e) => {
                  e.stopPropagation();
                  const buttonElement = e.currentTarget;
                  
                  // Expand accordion if closed, then open menu after expansion
                  if (!isExpanded) {
                    setExpandedAccordions((prev) => ({ ...prev, [filterType]: true }));
                    // Wait for accordion to expand before opening menu
                    setTimeout(() => {
                      handleAddMenuOpen(filterType, buttonElement);
                    }, 100);
                  } else {
                    // Accordion is already open, open menu immediately
                    handleAddMenuOpen(filterType, e);
                  }
                }}
                sx={{
                  ml: 'auto',
                  mr: 1,
                  textTransform: 'none',
                  fontSize: '0.75rem',
                  fontWeight: 500,
                  px: 1.5,
                  py: 0.5,
                  borderRadius: 0.75,
                }}
              >
                Add Filter
              </Button>
            )}
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 2, pb: 2 }}>
          {isBooleanOnly ? (
            <Grid container spacing={2}>
              {filterSchema.fields.map((field) => renderFilterField(field, 0, 1, filterSchema, false))}
            </Grid>
          ) : (
            <Box>
              {activeFilters.length === 0 ? (
                <Box
                  sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    py: 2,
                    px: 2,
                    borderRadius: 1.25,
                    border: `1.5px dashed ${alpha(theme.palette.divider, isDark ? 0.2 : 0.15)}`,
                    bgcolor: isDark
                      ? alpha(theme.palette.background.paper, 0.3)
                      : alpha(theme.palette.grey[50], 0.5),
                    mb: 2,
                    minHeight: 120,
                  }}
                >
                  <Box
                    sx={{
                      display: 'inline-flex',
                      p: 0.875,
                      borderRadius: '50%',
                      bgcolor: alpha(theme.palette.primary.main, 0.1),
                      mb: 1.25,
                    }}
                  >
                    <Iconify icon={filterIcon} width={18} color={theme.palette.primary.main} />
                  </Box>
                  <Typography
                    variant="body2"
                    sx={{
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      color: theme.palette.text.primary,
                      mb: 0.25,
                    }}
                  >
                    No filters configured
                  </Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ fontSize: '0.75rem', mb: 1.75, lineHeight: 1.4 }}
                  >
                    Add filters to control what data is synchronized
                  </Typography>
                  {filterType === 'sync' && availableFilters.length > 0 && (
                    <Button
                      variant="contained"
                      startIcon={<Iconify icon={addIcon} width={16} />}
                      onClick={(e) => {
                        e.stopPropagation();
                        const buttonElement = e.currentTarget;
                        
                        // Accordion should already be expanded when this is visible, but ensure it is
                        if (!isExpanded) {
                          setExpandedAccordions((prev) => ({ ...prev, [filterType]: true }));
                          // Wait for accordion to expand before opening menu
                          setTimeout(() => {
                            handleAddMenuOpen(filterType, buttonElement);
                          }, 100);
                        } else {
                          // Accordion is already open, open menu immediately
                          handleAddMenuOpen(filterType, e);
                        }
                      }}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.8125rem',
                        fontWeight: 600,
                        px: 2,
                        py: 0.5,
                        borderRadius: 1,
                        boxShadow: isDark
                          ? `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}`
                          : 'none',
                        '&:hover': {
                          boxShadow: isDark
                            ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.4)}`
                            : `0 2px 8px ${alpha(theme.palette.primary.main, 0.2)}`,
                        },
                      }}
                    >
                      Add Filter
                    </Button>
                  )}
                </Box>
              ) : (
                <>
                  {activeFilters.map((field: any, index: number) => (
                    <React.Fragment key={field.filterId || field.name}>
                      {index > 0 && (
                        <Box sx={{ display: 'flex', alignItems: 'center', my: 2 }}>
                          <Divider sx={{ flex: 1, borderColor: alpha(theme.palette.divider, isDark ? 0.12 : 0.08) }} />
                          <Chip
                            label="AND"
                            size="small"
                            sx={{
                              mx: 2,
                              height: 24,
                              fontSize: '0.75rem',
                              fontWeight: 600,
                              bgcolor: isDark
                                ? alpha(theme.palette.common.white, 0.95)
                                : alpha(theme.palette.primary.main, 0.1),
                              color: theme.palette.primary.main,
                              border: `1px solid ${alpha(theme.palette.primary.main, 0.3)}`,
                            }}
                          />
                          <Divider sx={{ flex: 1, borderColor: alpha(theme.palette.divider, isDark ? 0.12 : 0.08) }} />
                        </Box>
                      )}
                      <Paper
                        variant="outlined"
                        sx={{
                          p: 1.5,
                          borderRadius: 1.25,
                          bgcolor: isDark
                            ? alpha(theme.palette.background.paper, 0.5)
                            : theme.palette.background.paper,
                          borderColor: isDark
                            ? alpha(theme.palette.divider, 0.15)
                            : alpha(theme.palette.divider, 0.12),
                          boxShadow: isDark
                            ? `0 1px 2px ${alpha(theme.palette.common.black, 0.15)}`
                            : `0 1px 2px ${alpha(theme.palette.common.black, 0.03)}`,
                        }}
                      >
                        {renderFilterField(field, index, activeFilters.length, filterSchema, filterType === 'sync')}
                      </Paper>
                    </React.Fragment>
                  ))}
                </>
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
      {hasSyncFilters && syncFilters?.schema && renderFilterSection(
        'Sync Filters',
        'Configure filters to control what data is synchronized',
        syncFilters.schema,
        'sync'
      )}

      {hasIndexingFilters && indexingFilters?.schema && renderFilterSection(
        'Indexing Filters',
        'Configure filters to control what data is indexed',
        indexingFilters.schema,
        'indexing'
      )}

      {/* Autocomplete for adding sync filters - rendered at component level */}
      {hasSyncFilters && (() => {
        const syncAvailableFilters = getAvailableFilters('sync');
        const syncMenuAnchor = addMenuAnchor.sync;
        const isOpen = Boolean(syncMenuAnchor) && autocompleteOpen.sync;
        
        if (syncAvailableFilters.length === 0) return null;
        
        return (
          <Popper
            open={isOpen}
            anchorEl={syncMenuAnchor}
            placement="bottom-start"
            style={{ 
              zIndex: 1300, 
              width: syncMenuAnchor?.getBoundingClientRect().width || 320,
              maxWidth: 400,
            }}
            modifiers={[
              {
                name: 'offset',
                options: {
                  offset: [0, 8],
                },
              },
            ]}
          >
            <Paper
              sx={{
                width: '100%',
                minWidth: 320,
                maxWidth: 400,
                bgcolor: isDark
                  ? theme.palette.background.paper
                  : theme.palette.background.paper,
                border: `1px solid ${alpha(theme.palette.divider, isDark ? 0.2 : 0.15)}`,
                boxShadow: isDark
                  ? `0 8px 32px ${alpha(theme.palette.common.black, 0.5)}, 0 0 0 1px ${alpha(theme.palette.common.white, 0.08)}`
                  : `0 8px 32px ${alpha(theme.palette.common.black, 0.15)}, 0 0 0 1px ${alpha(theme.palette.common.black, 0.08)}`,
                borderRadius: 1.5,
                overflow: 'hidden',
              }}
            >
              <Autocomplete
                open={isOpen}
                onOpen={() => setAutocompleteOpen((prev) => ({ ...prev, sync: true }))}
                onClose={() => handleAddMenuClose('sync')}
                options={syncAvailableFilters}
                getOptionLabel={(option) => option.displayName}
                onChange={(_, value) => handleAutocompleteChange('sync', value)}
                filterOptions={(options, { inputValue }) => {
                  const searchTerm = inputValue.toLowerCase().trim();
                  if (!searchTerm) return options;
                  return options.filter(
                    (option) =>
                      option.displayName.toLowerCase().includes(searchTerm) ||
                      (option.description?.toLowerCase().includes(searchTerm) ?? false) ||
                      option.name.toLowerCase().includes(searchTerm)
                  );
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="Search filters..."
                    variant="outlined"
                    size="small"
                    autoFocus
                    sx={{
                      '& .MuiOutlinedInput-root': {
                        bgcolor: isDark
                          ? alpha(theme.palette.background.paper, 0.6)
                          : alpha(theme.palette.grey[50], 0.8),
                        fontSize: '0.875rem',
                        '& fieldset': {
                          border: 'none',
                        },
                        '&:hover fieldset': {
                          border: 'none',
                        },
                        '&.Mui-focused': {
                          bgcolor: isDark
                            ? alpha(theme.palette.background.paper, 0.8)
                            : theme.palette.background.paper,
                        },
                        '&.Mui-focused fieldset': {
                          border: 'none',
                        },
                      },
                      '& .MuiInputBase-input': {
                        py: 1.25,
                        px: 2,
                        fontSize: '0.875rem',
                        color: theme.palette.text.primary,
                      },
                    }}
                  />
                )}
                renderOption={(props, option) => (
                  <Box
                    component="li"
                    {...props}
                    sx={{
                      py: 1.25,
                      px: 2,
                      cursor: 'pointer',
                      transition: 'background-color 0.15s ease',
                      '&:hover': {
                        bgcolor: isDark
                          ? alpha(theme.palette.primary.main, 0.12)
                          : alpha(theme.palette.primary.main, 0.06),
                      },
                      '&[aria-selected="true"]': {
                        bgcolor: isDark
                          ? alpha(theme.palette.primary.main, 0.18)
                          : alpha(theme.palette.primary.main, 0.1),
                        '&:hover': {
                          bgcolor: isDark
                            ? alpha(theme.palette.primary.main, 0.22)
                            : alpha(theme.palette.primary.main, 0.14),
                        },
                      },
                    }}
                  >
                    <Box sx={{ width: '100%' }}>
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: 500,
                          fontSize: '0.875rem',
                          color: theme.palette.text.primary,
                          lineHeight: 1.5,
                          mb: option.description ? 0.25 : 0,
                        }}
                      >
                        {option.displayName}
                      </Typography>
                      {option.description && (
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
                          {option.description}
                        </Typography>
                      )}
                    </Box>
                  </Box>
                )}
                ListboxProps={{
                  sx: {
                    maxHeight: 320,
                    bgcolor: 'transparent',
                    '&::-webkit-scrollbar': {
                      width: '8px',
                    },
                    '&::-webkit-scrollbar-track': {
                      backgroundColor: 'transparent',
                    },
                    '&::-webkit-scrollbar-thumb': {
                      backgroundColor: isDark
                        ? alpha(theme.palette.text.secondary, 0.3)
                        : alpha(theme.palette.text.secondary, 0.2),
                      borderRadius: '4px',
                      '&:hover': {
                        backgroundColor: isDark
                          ? alpha(theme.palette.text.secondary, 0.45)
                          : alpha(theme.palette.text.secondary, 0.3),
                      },
                    },
                  },
                }}
                PaperComponent={({ children, ...other }) => (
                  <Box
                    {...other}
                    sx={{
                      bgcolor: isDark
                        ? theme.palette.background.paper
                        : theme.palette.background.paper,
                      borderTop: `1px solid ${alpha(theme.palette.divider, isDark ? 0.15 : 0.1)}`,
                      mt: 0.5,
                    }}
                  >
                    {children}
                  </Box>
                )}
                componentsProps={{
                  popper: {
                    sx: {
                      '& .MuiAutocomplete-listbox': {
                        p: 0,
                        bgcolor: 'transparent',
                      },
                    },
                  },
                }}
              />
            </Paper>
          </Popper>
        );
      })()}

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