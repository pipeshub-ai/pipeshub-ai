import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
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
  Switch,
  CircularProgress,
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
  DatetimeRange,
  FilterOption
} from '../../types/types';
import { 
  convertEpochToISOString,
  normalizeDatetimeValueForDisplay
} from '../../utils/time-utils';
import { FieldRenderer } from '../field-renderers';
import { ConnectorApiService } from '../../services/api';

interface FiltersSectionProps {
  connectorConfig: ConnectorConfig | null;
  formData: Record<string, FilterValueData>;
  formErrors: Record<string, string>;
  onFieldChange: (section: string, fieldName: string, value: FilterValueData | undefined) => void;
  onRemoveFilter?: (section: string, fieldName: string) => void;
  connectorId?: string; // Required for fetching dynamic filter options
  readOnly?: boolean; // If true, show read-only view (no editing)
}

const FiltersSection: React.FC<FiltersSectionProps> = ({
  connectorConfig,
  formData,
  formErrors,
  onFieldChange,
  onRemoveFilter,
  connectorId,
  readOnly = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [addMenuAnchor, setAddMenuAnchor] = useState<{ [key: string]: HTMLElement | null }>({});
  const [expandedAccordions, setExpandedAccordions] = useState<{ [key: string]: boolean }>({});
  const [autocompleteOpen, setAutocompleteOpen] = useState<{ [key: string]: boolean }>({});
  // Dynamic options state: stores options, cursors, and search terms per field
  const [dynamicOptions, setDynamicOptions] = useState<Record<string, {
    options: FilterOption[];
    cursor?: string;
    hasMore: boolean;
    searchTerm: string;
    totalLoaded?: number;
  }>>({});
  const [loadingOptions, setLoadingOptions] = useState<Record<string, boolean>>({});
  const fetchingFieldsRef = useRef<Set<string>>(new Set()); // Track fields currently being fetched
  const searchTimeoutRefs = useRef<Record<string, NodeJS.Timeout | null>>({}); // Store timeout refs per field
  const scrollPositionsRef = useRef<Record<string, number>>({}); // Store scroll positions per field
  const listboxRefs = useRef<Record<string, HTMLElement | null>>({}); // Store listbox refs per field
  const MAX_OPTIONS_IN_MEMORY = 5000; // Maximum options to keep in memory for performance
  const INITIAL_LOAD_LIMIT = 100; // Initial load limit
  const PAGINATION_LIMIT = 100; // Options per page

  // Fetch dynamic options when autocomplete opens
  const fetchDynamicOptions = useCallback(async (
    fieldName: string,
    searchTerm: string = '',
    cursor?: string,
    append: boolean = false
  ) => {
    if (!connectorId || fetchingFieldsRef.current.has(fieldName)) {
      return;
    }

    // Save scroll position before loading if appending
    if (append && listboxRefs.current[fieldName]) {
      scrollPositionsRef.current[fieldName] = listboxRefs.current[fieldName]!.scrollTop;
    }

    try {
      fetchingFieldsRef.current.add(fieldName);
      setLoadingOptions((prev) => ({ ...prev, [fieldName]: true }));

      const limit = append ? PAGINATION_LIMIT : INITIAL_LOAD_LIMIT;
      const response = await ConnectorApiService.getFilterFieldOptions(
        connectorId,
        fieldName,
        1,
        limit,
        searchTerm || undefined,
        cursor
      );

      if (response.success && response.options) {
        setDynamicOptions((prev) => {
          const existing = prev[fieldName];
          let newOptions: FilterOption[];
          
          if (append && existing) {
            // Append new options, but limit total to MAX_OPTIONS_IN_MEMORY
            const remainingSlots = MAX_OPTIONS_IN_MEMORY - existing.options.length;
            if (remainingSlots > 0) {
              // Add as many new options as we can fit
              const optionsToAdd = response.options.slice(0, remainingSlots);
              newOptions = [...existing.options, ...optionsToAdd];
            } else {
              // Already at limit, don't add more
              newOptions = existing.options;
            }
          } else {
            // For new search or initial load, limit to MAX_OPTIONS_IN_MEMORY
            newOptions = response.options.slice(0, MAX_OPTIONS_IN_MEMORY);
          }

          // Determine if we can load more
          // We can load more if: server says hasMore AND we haven't hit memory limit
          const canLoadMore = response.hasMore && newOptions.length < MAX_OPTIONS_IN_MEMORY;

          return {
            ...prev,
            [fieldName]: {
              options: newOptions,
              cursor: response.cursor,
              hasMore: canLoadMore,
              searchTerm,
              totalLoaded: newOptions.length,
            },
          };
        });

        // Restore scroll position after state update
        if (append && listboxRefs.current[fieldName] && scrollPositionsRef.current[fieldName] !== undefined) {
          // Use requestAnimationFrame to ensure DOM has updated
          requestAnimationFrame(() => {
            if (listboxRefs.current[fieldName]) {
              listboxRefs.current[fieldName]!.scrollTop = scrollPositionsRef.current[fieldName];
            }
          });
        }
      }
    } catch (err) {
      console.error(`Failed to fetch dynamic options for ${fieldName}:`, err);
      setDynamicOptions((prev) => ({
        ...prev,
        [fieldName]: {
          options: prev[fieldName]?.options || [],
          cursor: prev[fieldName]?.cursor,
          hasMore: false,
          searchTerm,
          totalLoaded: prev[fieldName]?.totalLoaded || 0,
        },
      }));
    } finally {
      setLoadingOptions((prev) => {
        const { [fieldName]: _, ...rest } = prev;
        return rest;
      });
      fetchingFieldsRef.current.delete(fieldName);
    }
  }, [connectorId]);

  // Format operator string to display label
  const formatOperatorLabel = useCallback((operator: string): string =>
    operator
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' '), []);

  // Get filter value with proper defaults and normalization
  const getFilterValue = useCallback((
    fieldName: string, 
    filterSchema?: { fields: FilterSchemaField[] },
    syncFiltersSchema?: { fields: FilterSchemaField[] }
  ): FilterValueData => {
    const filterData = formData[fieldName];
    const schema = filterSchema || syncFiltersSchema;
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
  }, [formData]);

  // Handle operator change for a filter field
  const handleOperatorChange = useCallback((
    fieldName: string, 
    operator: string, 
    filterSchema?: { fields: FilterSchemaField[] },
    syncFiltersSchema?: { fields: FilterSchemaField[] }
  ) => {
    const schema = filterSchema || syncFiltersSchema;
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
        const currentValue = getFilterValue(fieldName, schema, syncFiltersSchema);
        newValue = normalizeDatetimeValueForDisplay(currentValue.value, operator);
      }
    } else if (field.filterType === 'list' || field.filterType === 'multiselect') {
      newValue = [];
    } else {
      const currentValue = getFilterValue(fieldName, schema, syncFiltersSchema);
      newValue = currentValue.value;
    }

    onFieldChange('filters', fieldName, { 
      operator, 
      value: newValue, 
      type: field.filterType 
    });
  }, [getFilterValue, onFieldChange]);

  // Handle value change for a filter field
  const handleValueChange = useCallback((
    fieldName: string, 
    value: FilterValue, 
    filterSchema?: { fields: FilterSchemaField[] },
    syncFiltersSchema?: { fields: FilterSchemaField[] }
  ) => {
    const schema = filterSchema || syncFiltersSchema;
    const currentValue = getFilterValue(fieldName, schema, syncFiltersSchema);
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
  }, [getFilterValue, onFieldChange]);

  // Handle removing a filter
  const handleRemoveFilter = useCallback((fieldName: string) => {
    if (onRemoveFilter) {
      onRemoveFilter('filters', fieldName);
    } else {
      // Fallback: set to undefined
      onFieldChange('filters', fieldName, undefined);
    }
    // Clear dynamic options when filter is removed
    setDynamicOptions((prev) => {
      const { [fieldName]: _, ...rest } = prev;
      return rest;
    });
    fetchingFieldsRef.current.delete(fieldName);
    // Clear search timeout
    if (searchTimeoutRefs.current[fieldName]) {
      clearTimeout(searchTimeoutRefs.current[fieldName]!);
      delete searchTimeoutRefs.current[fieldName];
    }
    // Clear scroll position and listbox ref
    delete scrollPositionsRef.current[fieldName];
    delete listboxRefs.current[fieldName];
  }, [onRemoveFilter, onFieldChange]);

  // Render dynamic multiselect field with custom autocomplete
  const renderDynamicMultiselectField = useCallback((
    field: FilterSchemaField & { filterId?: string },
    filterValue: FilterValueData,
    errorParam: string | undefined,
    showRemove: boolean,
    filterSchema?: { fields: FilterSchemaField[] },
    syncFiltersSchema?: { fields: FilterSchemaField[] }
  ) => {
    const fieldOptions = dynamicOptions[field.name];
    const options = fieldOptions?.options || [];
    const isLoading = loadingOptions[field.name] || false;
    // Handle both old format (string[]) and new format ({id, label}[])
    const selectedValues: Array<string | { id: string; label: string }> = Array.isArray(filterValue.value) ? filterValue.value : [];
    
    // Convert API response format to autocomplete format
    // Options now only have id and label (no key)
    const autocompleteOptions = options.map((opt) => ({
      id: opt.id,
      label: opt.label,
    }));

    const handleOpen = () => {
      setAutocompleteOpen((prev) => ({ ...prev, [field.name]: true }));
      // Fetch options when opening if:
      // 1. No options loaded yet, OR
      // 2. There are selected values but they're not in the current options (might need to fetch to display them)
      const needsFetch = !fieldOptions || 
        fieldOptions.options.length === 0 ||
        (selectedValues.length > 0 && selectedValues.some(val => {
          // Handle both old format (string id) and new format ({id, label} object)
          const valId = typeof val === 'string' ? val : ((val && typeof val === 'object' && 'id' in val) ? val.id : String(val));
          return !autocompleteOptions.find(opt => opt.id === valId);
        }));
      
      if (needsFetch) {
        // Reset scroll position when opening fresh
        scrollPositionsRef.current[field.name] = 0;
        const searchTerm = fieldOptions?.searchTerm || '';
        fetchDynamicOptions(field.name, searchTerm);
      }
    };

    const handleClose = () => {
      setAutocompleteOpen((prev) => ({ ...prev, [field.name]: false }));
    };
    
    const handleInputChange = (event: any, newInputValue: string, reason: string) => {
      // Only fetch on input change, not on selection
      if (reason === 'input') {
        // Clear previous timeout for this field
        if (searchTimeoutRefs.current[field.name]) {
          clearTimeout(searchTimeoutRefs.current[field.name]!);
        }
        
        // Debounce search - fetch after user stops typing
        searchTimeoutRefs.current[field.name] = setTimeout(() => {
          if (newInputValue !== fieldOptions?.searchTerm) {
            // Reset options when search term changes (new search)
            setDynamicOptions((prev) => {
              const { [field.name]: _, ...rest } = prev;
              return rest;
            });
            // Reset scroll position for new search
            scrollPositionsRef.current[field.name] = 0;
            fetchDynamicOptions(field.name, newInputValue);
          }
          searchTimeoutRefs.current[field.name] = null;
        }, 300);
      }
    };

    const handleChange = (event: any, newValue: Array<{ id: string; label: string } | string>) => {
      try {
        // Store {id, label} objects instead of just ids
        const values: Array<{ id: string; label: string }> = newValue.map((item) => {
          if (typeof item === 'string') {
            // Legacy format: find the option to get label
            const found = autocompleteOptions.find(opt => opt.id === item);
            return found ? { id: found.id, label: found.label } : { id: item, label: item };
          }
          // New format: ensure we have {id, label} object
          if (item && typeof item === 'object' && 'id' in item && typeof item.id === 'string') {
            return {
              id: item.id,
              label: (item.label && typeof item.label === 'string') ? item.label : item.id, // Fallback to id if label missing
            };
          }
          // Fallback for unexpected formats
          const itemStr = String(item);
          return { id: itemStr, label: itemStr };
        });
        handleValueChange(field.name, values as any, filterSchema, syncFiltersSchema);
      } catch (err) {
        console.error('Error handling dynamic multiselect change:', err);
      }
    };

    const handleLoadMore = () => {
      if (fieldOptions?.hasMore && fieldOptions.cursor && !isLoading) {
        // Check if we've hit the memory limit
        if (fieldOptions.totalLoaded && fieldOptions.totalLoaded >= MAX_OPTIONS_IN_MEMORY) {
          // Show message that search is required for more options
          return;
        }
        fetchDynamicOptions(field.name, fieldOptions.searchTerm, fieldOptions.cursor, true);
      }
    };

    // Store listbox ref for scroll position management
    const handleListboxRef = (element: HTMLElement | null) => {
      if (element) {
        listboxRefs.current[field.name] = element;
      }
    };

    // Find selected options from current value
    // Handle both old format (string id) and new format ({id, label} object)
    const selectedOptions = selectedValues
      .map((val): { id: string; label: string } | null => {
        // Handle new format: {id, label} object
        if (val && typeof val === 'object' && 'id' in val && typeof val.id === 'string') {
          const valObj = val as { id: string; label?: string };
          const found = autocompleteOptions.find((opt) => opt.id === valObj.id);
          if (found) return found;
          // If not found in options, use the value as-is (it already has id and label)
          return { id: valObj.id, label: valObj.label || valObj.id };
        }
        // Handle old format: string id (backward compatibility)
        const valId = typeof val === 'string' ? val : String(val);
        const found = autocompleteOptions.find((opt) => opt.id === valId);
        if (found) return found;
        // If not found in options, create a temporary option (for values loaded from saved config)
        return { id: valId, label: valId };
      })
      .filter((opt): opt is { id: string; label: string } => opt !== null);

    // Create operator field helper
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
      const operators = field.operators || [];
      const operatorEntry = operators.find(
        (op) => formatOperatorLabel(op) === formattedValue
      );
      if (operatorEntry) {
        handleOperatorChange(field.name, operatorEntry, filterSchema, syncFiltersSchema);
      }
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
            {showRemove && !readOnly && (
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
                disabled={readOnly}
              />
            </Grid>
            <Grid item xs={12} sm={8}>
              <Autocomplete
                disabled={readOnly}
                multiple
                open={autocompleteOpen[field.name] || false}
                onOpen={handleOpen}
                onClose={handleClose}
                options={autocompleteOptions}
                value={selectedOptions}
                onChange={handleChange}
                onInputChange={handleInputChange}
                loading={isLoading}
                getOptionLabel={(option) => {
                  try {
                    if (typeof option === 'string') {
                      // Fallback for string values (legacy format)
                      const found = autocompleteOptions.find((opt) => opt.id === option);
                      return found?.label || option;
                    }
                    if (option && typeof option === 'object' && 'label' in option) {
                      return option.label || option.id || '';
                    }
                    return String(option);
                  } catch (err) {
                    console.error('Error getting option label:', err);
                    return '';
                  }
                }}
                isOptionEqualToValue={(option, value) => {
                  // Compare by id
                  const optionId = typeof option === 'string' ? option : (option?.id || '');
                  const valueId = typeof value === 'string' ? value : (value?.id || '');
                  return optionId === valueId;
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={field.displayName}
                    placeholder={field.description || `Select ${field.displayName.toLowerCase()}`}
                    error={!!errorParam}
                    helperText={errorParam}
                    variant="outlined"
                    size="small"
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <>
                          {isLoading ? <CircularProgress size={20} /> : null}
                          {params.InputProps.endAdornment}
                        </>
                      ),
                    }}
                    sx={{
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1.25,
                        backgroundColor: isDark
                          ? alpha(theme.palette.background.paper, 0.6)
                          : alpha(theme.palette.background.paper, 0.8),
                        transition: 'all 0.2s',
                        '&:hover': {
                          backgroundColor: isDark
                            ? alpha(theme.palette.background.paper, 0.8)
                            : alpha(theme.palette.background.paper, 1),
                        },
                        '&.Mui-focused': {
                          backgroundColor: isDark
                            ? alpha(theme.palette.background.paper, 0.9)
                            : theme.palette.background.paper,
                        },
                      },
                      '& .MuiOutlinedInput-input': {
                        fontSize: '0.875rem',
                        padding: '6px 10px !important',
                        fontWeight: 400,
                      },
                    }}
                  />
                )}
                renderTags={(val, getTagProps) =>
                  val.map((option, index) => {
                    const label = typeof option === 'string' 
                      ? autocompleteOptions.find((opt) => opt.id === option)?.label || option
                      : (option.label || option.id || '');
                    const optionId = typeof option === 'string' ? option : (option.id || '');
                    return (
                      <Chip
                        variant="outlined"
                        label={label}
                        size="small"
                        {...getTagProps({ index })}
                        key={optionId}
                        sx={{
                          fontSize: '0.8125rem',
                          height: 24,
                          borderRadius: 1,
                          '& .MuiChip-label': {
                            px: 1,
                            fontWeight: 500,
                          },
                          borderColor: alpha(theme.palette.primary.main, 0.2),
                          '&:hover': {
                            borderColor: alpha(theme.palette.primary.main, 0.4),
                            bgcolor: alpha(theme.palette.primary.main, 0.04),
                          },
                        }}
                      />
                    );
                  })
                }
                ListboxProps={{
                  ref: handleListboxRef,
                  onScroll: (event: React.SyntheticEvent) => {
                    const listboxNode = event.currentTarget;
                    // Only auto-load if we haven't hit the memory limit
                    const canLoadMore = fieldOptions?.totalLoaded 
                      ? fieldOptions.totalLoaded < MAX_OPTIONS_IN_MEMORY 
                      : true;
                    
                    if (
                      listboxNode.scrollTop + listboxNode.clientHeight >= listboxNode.scrollHeight - 50 &&
                      fieldOptions?.hasMore &&
                      !isLoading &&
                      canLoadMore
                    ) {
                      handleLoadMore();
                    }
                  },
                }}
                PaperComponent={({ children, ...other }) => {
                  const canLoadMore = fieldOptions?.totalLoaded 
                    ? fieldOptions.totalLoaded < MAX_OPTIONS_IN_MEMORY 
                    : true;
                  const showLoadMore = fieldOptions?.hasMore && canLoadMore;
                  const showSearchMessage = fieldOptions?.totalLoaded && fieldOptions.totalLoaded >= MAX_OPTIONS_IN_MEMORY && fieldOptions.hasMore;
                  
                  return (
                    <Paper {...other}>
                      {children}
                      {showLoadMore && (
                        <Box sx={{ p: 1, textAlign: 'center', borderTop: `1px solid ${alpha(theme.palette.divider, 0.1)}` }}>
                          <Button
                            size="small"
                            onClick={handleLoadMore}
                            disabled={isLoading}
                            sx={{ textTransform: 'none' }}
                          >
                            {isLoading ? 'Loading...' : `Load More (${fieldOptions?.totalLoaded || 0} loaded)`}
                          </Button>
                        </Box>
                      )}
                      {showSearchMessage && (
                        <Box sx={{ 
                          p: 1.5, 
                          textAlign: 'center', 
                          borderTop: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                          bgcolor: alpha(theme.palette.info.main, 0.08),
                        }}>
                          <Typography variant="caption" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
                            {fieldOptions.totalLoaded}+ options loaded. Use search to find specific items.
                          </Typography>
                        </Box>
                      )}
                    </Paper>
                  );
                }}
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
  }, [dynamicOptions, loadingOptions, autocompleteOpen, fetchDynamicOptions, theme, handleRemoveFilter, handleValueChange, handleOperatorChange, formatOperatorLabel, isDark, readOnly]);

  // Cleanup timeouts on unmount
  useEffect(() => {
    const timeouts = searchTimeoutRefs.current;
    return () => {
      // Clear all search timeouts
      Object.values(timeouts).forEach((timeout) => {
        if (timeout) clearTimeout(timeout);
      });
    };
  }, []);

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

  // Get the manual sync field from indexing filters
  const manualSyncField = indexingFilters?.schema?.fields?.find(
    (field) => field.name === 'enable_manual_sync'
  );
  const manualSyncValue = typeof formData.enable_manual_sync?.value === 'boolean' 
    ? formData.enable_manual_sync.value 
    : false;

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
    const availableFields = (schema?.fields || []).filter(
      // Exclude enable_manual_sync from indexing filters accordion
      (field) => filterType !== 'indexing' || field.name !== 'enable_manual_sync'
    );
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
    const availableFields = (schema?.fields || []).filter(
      // Exclude enable_manual_sync from indexing filters accordion
      (field) => filterType !== 'indexing' || field.name !== 'enable_manual_sync'
    );
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
    const filterValue = getFilterValue(field.name, schema, syncFilters?.schema);
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
        handleOperatorChange(field.name, operatorEntry, schema, syncFilters?.schema);
      }
    };

    if (field.filterType === 'list') {
      // List can also support dynamic options - if dynamic, use dynamic multiselect renderer
      if (field.optionSourceType === 'dynamic' && connectorId) {
        return renderDynamicMultiselectField(field, filterValue, error, showRemove, schema, syncFilters?.schema);
      }

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
                  disabled={readOnly}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={valueField}
                  value={Array.isArray(filterValue.value) ? filterValue.value : []}
                  onChange={(value) => handleValueChange(field.name, value, schema, syncFilters?.schema)}
                  error={error}
                  disabled={readOnly}
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
      // Handle dynamic multiselect with custom autocomplete
      if (field.optionSourceType === 'dynamic' && connectorId) {
        return renderDynamicMultiselectField(field, filterValue, error, showRemove, schema, syncFilters?.schema);
      }

      // Static multiselect: dropdown with predefined options from field.options
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
                  disabled={readOnly}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={valueField}
                  value={Array.isArray(filterValue.value) ? filterValue.value : []}
                  onChange={(value) => handleValueChange(field.name, value, schema, syncFilters?.schema)}
                  error={error}
                  disabled={readOnly}
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
                    disabled={readOnly}
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
                    disabled={readOnly}
                  />
                </Grid>
                <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={dateRangeField}
                  value={rangeValue}
                  onChange={(value) => handleValueChange(field.name, value, schema, syncFilters?.schema)}
                  error={error}
                  disabled={readOnly}
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
                  disabled={readOnly}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={dateField}
                  value={datetimeValue}
                  onChange={(value) => handleValueChange(field.name, value, schema, syncFilters?.schema)}
                  error={error}
                  disabled={readOnly}
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
                onChange={(e) => handleValueChange(field.name, e.target.checked, schema, syncFilters?.schema)}
                size="small"
                disabled={readOnly}
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
                  disabled={readOnly}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={valueField}
                  value={textValue}
                  onChange={(value) => handleValueChange(field.name, value, schema, syncFilters?.schema)}
                  error={error}
                  disabled={readOnly}
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
                  disabled={readOnly}
                />
              </Grid>
              <Grid item xs={12} sm={8}>
                <FieldRenderer
                  field={valueField}
                  value={numberValue}
                  onChange={(value) => handleValueChange(field.name, value, schema, syncFilters?.schema)}
                  error={error}
                  disabled={readOnly}
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

    // Filter out enable_manual_sync from indexing filters accordion (shown separately above)
    const filteredSchema = filterType === 'indexing'
      ? { ...filterSchema, fields: filterSchema.fields.filter(f => f.name !== 'enable_manual_sync') }
      : filterSchema;

    if (filteredSchema.fields.length === 0) {
      return null;
    }

    const activeFilters = getActiveFilters(filterType);
    const availableFilters = getAvailableFilters(filterType);
    const isBooleanOnly = filteredSchema.fields.every((f) => f.filterType === 'boolean');

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
            '&.Mui-expanded': {
              minHeight: 48,
              my: 0,
            },
            '& .MuiAccordionSummary-content': {
              my: 0,
              '&.Mui-expanded': {
                my: 0,
              },
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
                <Tooltip
                  title={
                    <Box sx={{ p: 0.5 }}>
                      <Typography variant="body2" sx={{ fontSize: '0.75rem', lineHeight: 1.5 }}>
                        {filterType === 'sync' 
                          ? 'Control what data is downloaded from the source. Use these filters to limit which data gets synced to your knowledge base.'
                          : 'Choose what gets made searchable for AI search. All synced data is available, but only checked items are searchable in AI chat.'
                        }
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
            {filterType === 'sync' && availableFilters.length > 0 && !readOnly && (
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
              {filteredSchema.fields.map((field) => renderFilterField(field, 0, 1, filteredSchema, false))}
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
                  {filterType === 'sync' && availableFilters.length > 0 && !readOnly && (
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
                        {renderFilterField(field, index, activeFilters.length, filteredSchema, filterType === 'sync')}
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
      {/* Manual Sync Control - Only show if the connector has indexing filters */}
      {hasIndexingFilters && manualSyncField && (
        <Paper
          elevation={0}
          sx={{
            px: 2,
            py: 1.6,
            borderRadius: 1.25,
            border: `1.5px solid ${isDark ? alpha(theme.palette.common.white, 0.2) : alpha(theme.palette.common.black, 0.25)}`,
            bgcolor: isDark
              ? alpha(theme.palette.background.paper, 0.4)
              : theme.palette.background.paper,
            boxShadow: isDark
              ? `0 1px 3px ${alpha(theme.palette.common.black, 0.2)}`
              : `0 1px 3px ${alpha(theme.palette.common.black, 0.05)}`,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5 }}>
            <Box
              sx={{
                p: 0.625,
                borderRadius: 1,
                bgcolor: alpha(theme.palette.primary.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <Iconify icon="mdi:car-transmission" width={16} color={theme.palette.primary.main} />
            </Box>
            <Box sx={{ flex: 1 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.25 }}>
                <Typography
                  variant="subtitle2"
                  sx={{
                    fontWeight: 600,
                    fontSize: '0.875rem',
                    color: theme.palette.text.primary,
                  }}
                >
                  {manualSyncField.displayName}
                </Typography>
                <Tooltip
                  title={
                    <Box sx={{ p: 0.5 }}>
                      <Typography variant="body2" sx={{ mb: 1, fontSize: '0.75rem', lineHeight: 1.5 }}>
                        <strong>OFF (Default):</strong> Records are automatically indexed based on the indexing filters below.
                        <br />
                        <br />
                        <strong>ON:</strong> No records are automatically indexed. You manually select which records to index from the knowledge base.
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
              </Box>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{
                  fontSize: '0.75rem',
                  lineHeight: 1.5,
                }}
              >
                {manualSyncField.description}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
              <Switch
                checked={manualSyncValue}
                onChange={(e) => handleValueChange('enable_manual_sync', e.target.checked, indexingFilters?.schema)}
                disabled={readOnly}
                sx={{
                  '& .MuiSwitch-switchBase.Mui-checked': {
                    color: theme.palette.primary.main,
                  },
                  '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
                    backgroundColor: theme.palette.primary.main,
                  },
                }}
              />
            </Box>
          </Box>
        </Paper>
      )}

      {hasSyncFilters && syncFilters?.schema && renderFilterSection(
        'Sync Filters',
        'Configure filters to control what data is synchronized',
        syncFilters.schema,
        'sync'
      )}

      {hasIndexingFilters && indexingFilters?.schema && renderFilterSection(
        'Indexing Filters',
        'Configure filters to control what data is searchable by AI',
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
          py: 0.5,
          px: 1.25,
          flexShrink: 0,
          bgcolor: isDark
            ? alpha(theme.palette.info.main, 0.08)
            : alpha(theme.palette.info.main, 0.02),
          borderColor: isDark
            ? alpha(theme.palette.info.main, 0.25)
            : alpha(theme.palette.info.main, 0.1),
          '& .MuiAlert-icon': { fontSize: '1.25rem', py: 0.5 },
          '& .MuiAlert-message': { py: 0.25 },
          alignItems: 'center',
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