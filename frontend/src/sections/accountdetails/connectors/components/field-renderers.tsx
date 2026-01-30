import React from 'react';
import {
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormControlLabel,
  Checkbox,
  FormGroup,
  Chip,
  Box,
  Typography,
  Autocomplete,
  FormHelperText,
  InputAdornment,
  IconButton,
  Button,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import { useTheme, alpha } from '@mui/material/styles';
import eyeIcon from '@iconify-icons/mdi/eye';
import eyeOffIcon from '@iconify-icons/mdi/eye-off';
import dayjs, { Dayjs } from 'dayjs';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs';
import { MobileDateTimePicker } from '@mui/x-date-pickers/MobileDateTimePicker';

interface BaseFieldProps {
  field: any;
  value: any;
  onChange: (value: any) => void;
  error?: string;
  disabled?: boolean;
}

const getBaseFieldStyles = (theme: any) => {
  const isDark = theme.palette.mode === 'dark';
  return {
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
      '&:hover .MuiOutlinedInput-notchedOutline': {
        borderColor: alpha(theme.palette.primary.main, isDark ? 0.4 : 0.3),
      },
      '&.Mui-focused': {
        backgroundColor: isDark
          ? alpha(theme.palette.background.paper, 0.9)
          : theme.palette.background.paper,
      },
      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
        borderWidth: 1.5,
        borderColor: theme.palette.primary.main,
      },
    },
  '& .MuiInputLabel-root': {
    fontSize: '0.875rem',
    fontWeight: 500,
    '&.Mui-focused': {
      fontSize: '0.875rem',
    },
  },
  '& .MuiOutlinedInput-input': {
    fontSize: '0.875rem',
    padding: '10.5px 14px',
    fontWeight: 400,
  },
  '& .MuiFormHelperText-root': {
    fontSize: '0.75rem',
    fontWeight: 400,
    marginTop: 0.75,
    marginLeft: 1,
  },
  };
};

const FieldLabel: React.FC<{ 
  displayName: string; 
  required?: boolean;
  marginBottom?: number;
}> = ({ displayName, required, marginBottom = 1 }) => (
  <Typography 
    variant="body2" 
    sx={{ 
      mb: marginBottom,
      fontWeight: 600, 
      fontSize: '0.875rem',
      color: 'text.primary',
      display: 'flex',
      alignItems: 'center',
      gap: 0.5,
    }}
  >
    {displayName}
    {required && (
      <Typography component="span" sx={{ color: 'error.main', fontSize: '0.875rem' }}>
        *
      </Typography>
    )}
  </Typography>
);

const FieldDescription: React.FC<{ description: string; error?: string }> = ({ 
  description, 
  error,
}) => {
  if (!description || error) return null;
  
  return (
    <Typography 
      variant="caption" 
      color="text.secondary" 
      sx={{ 
        display: 'block', 
        mt: 0.75,
        ml: 1,
        fontSize: '0.8125rem',
        lineHeight: 1.5,
        fontWeight: 400,
      }}
    >
      {description}
    </Typography>
  );
};

export const TextFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const [showPassword, setShowPassword] = React.useState(false);

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder}
        type={field.isSecret ? (showPassword ? 'text' : 'password') : 'text'}
        value={value !== undefined && value !== null ? String(value) : ''}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        InputProps={{
          endAdornment: field.isSecret ? (
            <InputAdornment position="end">
              <IconButton
                onClick={() => setShowPassword(!showPassword)}
                edge="end"
                size="small"
                sx={{
                  color: theme.palette.text.secondary,
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.primary.main, 0.08),
                    color: theme.palette.primary.main,
                  },
                }}
              >
                <Iconify icon={showPassword ? eyeOffIcon : eyeIcon} width={18} />
              </IconButton>
            </InputAdornment>
          ) : undefined,
        }}
        sx={getBaseFieldStyles(theme)}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const PasswordFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const [showPassword, setShowPassword] = React.useState(false);

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder}
        type={showPassword ? 'text' : 'password'}
        value={value !== undefined && value !== null ? String(value) : ''}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton
                onClick={() => setShowPassword(!showPassword)}
                edge="end"
                size="small"
                sx={{
                  color: theme.palette.text.secondary,
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.primary.main, 0.08),
                    color: theme.palette.primary.main,
                  },
                }}
              >
                <Iconify icon={showPassword ? eyeOffIcon : eyeIcon} width={18} />
              </IconButton>
            </InputAdornment>
          ),
        }}
        sx={getBaseFieldStyles(theme)}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const EmailFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder}
        type="email"
        value={value !== undefined && value !== null ? String(value) : ''}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        sx={getBaseFieldStyles(theme)}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const UrlFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder}
        type="url"
        value={value !== undefined && value !== null ? String(value) : ''}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        sx={getBaseFieldStyles(theme)}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const TextareaFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder}
        multiline
        rows={3}
        value={value !== undefined && value !== null ? String(value) : ''}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        sx={{
          ...getBaseFieldStyles(theme),
          '& .MuiOutlinedInput-input': {
            fontSize: '0.875rem',
            padding: '10.5px 14px',
          },
        }}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const SelectFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <Box>
      <FormControl fullWidth size="small" error={!!error}>
        <InputLabel sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
          {field.displayName}
        </InputLabel>
        <Select
          value={value !== undefined && value !== null ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          label={field.displayName}
          disabled={disabled}
          MenuProps={{
            PaperProps: {
              sx: {
                maxHeight: 300,
                '& .MuiMenuItem-root': {
                  fontSize: '0.875rem',
                  transition: 'all 0.15s',
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.primary.main, 0.08),
                  },
                  '&.Mui-selected': {
                    backgroundColor: alpha(theme.palette.primary.main, 0.12),
                    fontWeight: 600,
                    '&:hover': {
                      backgroundColor: alpha(theme.palette.primary.main, 0.16),
                    },
                  },
                },
              },
            },
          }}
          sx={{
            borderRadius: 1.25,
            backgroundColor: theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : alpha(theme.palette.background.paper, 0.8),
            transition: 'all 0.2s',
            '&:hover': {
              backgroundColor: theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.8)
                : alpha(theme.palette.background.paper, 1),
            },
            '&:hover .MuiOutlinedInput-notchedOutline': {
              borderColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.4 : 0.3),
            },
            '&.Mui-focused': {
              backgroundColor: theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.9)
                : theme.palette.background.paper,
            },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
              borderWidth: 1.5,
              borderColor: theme.palette.primary.main,
            },
            '& .MuiSelect-select': {
              fontSize: '0.875rem',
              padding: '10.5px 14px',
              fontWeight: 500,
            },
          }}
        >
          {field.options?.map((option: string) => (
            <MenuItem key={option} value={option}>
              {option}
            </MenuItem>
          ))}
        </Select>
        {error && (
          <FormHelperText sx={{ fontSize: '0.75rem', mt: 0.75, ml: 1 }}>
            {error}
          </FormHelperText>
        )}
      </FormControl>
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const MultiSelectFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const selectedValues = Array.isArray(value) ? value : [];

  const handleChange = (event: any, newValue: (string | { id: string; label: string })[]) => {
    onChange(newValue);
  };

  // Helper to get label from option (handles both string and {id, label} formats)
  const getOptionLabel = (option: string | { id: string; label: string }): string => {
    if (typeof option === 'string') return option;
    return option?.label || option?.id || '';
  };

  // Helper to get key from option
  const getOptionKey = (option: string | { id: string; label: string }): string => {
    if (typeof option === 'string') return option;
    return option?.id || '';
  };

  return (
    <Box>
      <Autocomplete
        multiple
        options={field.options || []}
        value={selectedValues}
        onChange={handleChange}
        disabled={disabled}
        size="small"
        getOptionLabel={getOptionLabel}
        isOptionEqualToValue={(option, val) => getOptionKey(option) === getOptionKey(val)}
        renderTags={(val, getTagProps) =>
          val.map((option, index) => (
            <Chip
              variant="outlined"
              label={getOptionLabel(option)}
              size="small"
              {...getTagProps({ index })}
              key={getOptionKey(option)}
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
          ))
        }
        renderInput={(params) => (
          <TextField
            {...params}
            label={field.displayName}
            placeholder={field.placeholder}
            error={!!error}
            helperText={error}
            variant="outlined"
            sx={{
              ...getBaseFieldStyles(theme),
              '& .MuiOutlinedInput-input': {
                fontSize: '0.875rem',
                padding: '6px 10px !important',
                fontWeight: 400,
              },
            }}
          />
        )}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const CheckboxFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <Box>
      <FormControlLabel
        control={
          <Checkbox
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
            disabled={disabled}
            size="small"
            sx={{
              p: 1,
              '& .MuiSvgIcon-root': {
                fontSize: '1.25rem',
              },
            }}
          />
        }
        label={
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
              {field.displayName}
            </Typography>
            {field.required && (
              <Typography component="span" sx={{ color: 'error.main', fontSize: '0.875rem' }}>
                *
              </Typography>
            )}
          </Box>
        }
        sx={{ 
          mb: error ? 0.75 : 0,
          ml: 0,
        }}
      />
      {error && (
        <FormHelperText error sx={{ mt: 0, ml: 5, fontSize: '0.75rem' }}>
          {error}
        </FormHelperText>
      )}
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const NumberFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder}
        type="number"
        value={value !== undefined && value !== null ? String(value) : ''}
        onChange={(e) => onChange(e.target.value)}
        required={field.required}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        inputProps={{
          min: field.validation?.minLength,
          max: field.validation?.maxLength,
        }}
        sx={getBaseFieldStyles(theme)}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const DateTimeFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  
  // Convert string value to Dayjs object
  const dateValue = React.useMemo(() => value ? dayjs(value) : null, [value]);

  const handleChange = (newValue: Dayjs | null) => {
    // Convert Dayjs back to string format (YYYY-MM-DDTHH:mm)
    const formattedValue = newValue ? newValue.format('YYYY-MM-DDTHH:mm') : '';
    onChange(formattedValue);
  };

  return (
    <Box>
      <LocalizationProvider dateAdapter={AdapterDayjs}>
        <MobileDateTimePicker
          label={field.displayName || ''}
          value={dateValue}
          onChange={handleChange}
          disabled={disabled}
          slotProps={{
            textField: {
              fullWidth: true,
              size: 'small',
              variant: 'outlined',
              error: !!error,
              helperText: error,
              required: field.required,
              sx: {
                ...getBaseFieldStyles(theme),
                '& .MuiOutlinedInput-input': {
                  fontSize: '0.875rem',
                  padding: '10.5px 14px',
                  fontWeight: 500,
                },
              },
            },
          }}
        />
      </LocalizationProvider>
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const DateFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const inputRef = React.useRef<HTMLInputElement>(null);

  const handleContainerClick = () => {
    if (!disabled && inputRef.current) {
      inputRef.current.showPicker?.();
    }
  };

  return (
    <Box>
        <TextField
          fullWidth
          label={field.displayName}
          type="date"
          value={value !== undefined && value !== null ? String(value) : ''}
          onChange={(e) => onChange(e.target.value)}
          required={field.required}
          error={!!error}
          helperText={error}
          disabled={disabled}
          variant="outlined"
          size="small"
          inputRef={inputRef}
          onClick={handleContainerClick}
        InputLabelProps={{
          shrink: true,
          sx: { fontSize: '0.875rem', fontWeight: 500 },
        }}
        sx={{
          ...getBaseFieldStyles(theme),
          '& .MuiOutlinedInput-input': {
            fontSize: '0.875rem',
            padding: '10.5px 14px',
            fontWeight: 500,
            cursor: 'pointer',
            '&::-webkit-calendar-picker-indicator': {
              cursor: 'pointer',
              fontSize: '1.125rem',
              padding: '4px',
              borderRadius: '4px',
              transition: 'all 0.2s',
              '&:hover': {
                backgroundColor: alpha(theme.palette.primary.main, 0.08),
              },
            },
          },
        }}
      />
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const DateTimeRangeFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const rangeValue = value || { start: '', end: '' };
  
  // Convert {start, end} format to Dayjs objects
  const startDate = React.useMemo(() => rangeValue.start ? dayjs(rangeValue.start) : null, [rangeValue.start]);

  const endDate = React.useMemo(() => rangeValue.end ? dayjs(rangeValue.end) : null, [rangeValue.end]);

  const handleStartChange = (newValue: Dayjs | null) => {
    const start = newValue ? newValue.format('YYYY-MM-DDTHH:mm') : '';
    onChange({ ...rangeValue, start });
  };

  const handleEndChange = (newValue: Dayjs | null) => {
    const end = newValue ? newValue.format('YYYY-MM-DDTHH:mm') : '';
    onChange({ ...rangeValue, end });
  };

  return (
    <Box>
      <LocalizationProvider dateAdapter={AdapterDayjs}>
        <Box sx={{ display: 'flex', gap: 1.5 }}>
          <MobileDateTimePicker
            label="Start Date & Time"
            value={startDate}
            onChange={handleStartChange}
            disabled={disabled}
            slotProps={{
              textField: {
                size: 'small',
                variant: 'outlined',
                error: !!error,
                required: field.required,
                sx: {
                  flex: 1,
                  ...getBaseFieldStyles(theme),
                  '& .MuiOutlinedInput-input': {
                    fontSize: '0.875rem',
                    padding: '10.5px 14px',
                    fontWeight: 500,
                  },
                },
              },
            }}
          />
          <MobileDateTimePicker
            label="End Date & Time"
            value={endDate}
            onChange={handleEndChange}
            disabled={disabled}
            slotProps={{
              textField: {
                size: 'small',
                variant: 'outlined',
                error: !!error,
                required: field.required,
                sx: {
                  flex: 1,
                  ...getBaseFieldStyles(theme),
                  '& .MuiOutlinedInput-input': {
                    fontSize: '0.875rem',
                    padding: '10.5px 14px',
                    fontWeight: 500,
                  },
                },
              },
            }}
          />
        </Box>
      </LocalizationProvider>
      {error && (
        <FormHelperText error sx={{ mt: 0.5, mx: 1.75 }}>
          {error}
        </FormHelperText>
      )}
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const DateRangeFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const rangeValue = value || { start: '', end: '' };
  const startInputRef = React.useRef<HTMLInputElement>(null);
  const endInputRef = React.useRef<HTMLInputElement>(null);

  const handleStartChange = (startDate: string) => {
    onChange({ ...rangeValue, start: startDate });
  };

  const handleEndChange = (endDate: string) => {
    onChange({ ...rangeValue, end: endDate });
  };

  const handleStartClick = () => {
    if (!disabled && startInputRef.current) {
      startInputRef.current.showPicker?.();
    }
  };

  const handleEndClick = () => {
    if (!disabled && endInputRef.current) {
      endInputRef.current.showPicker?.();
    }
  };

  return (
    <Box>
      <FieldLabel displayName={field.displayName} required={field.required} />
      <Box sx={{ display: 'flex', gap: 1.5 }}>
        <TextField
          label="Start Date"
          type="date"
          value={rangeValue.start}
          onChange={(e) => handleStartChange(e.target.value)}
          required={field.required}
          error={!!error}
          disabled={disabled}
          variant="outlined"
          size="small"
          inputRef={startInputRef}
          onClick={handleStartClick}
          InputLabelProps={{
            shrink: true,
            sx: { fontSize: '0.875rem', fontWeight: 500 },
          }}
          sx={{
            flex: 1,
            ...getBaseFieldStyles(theme),
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
              padding: '10.5px 14px',
              fontWeight: 500,
              cursor: 'pointer',
              '&::-webkit-calendar-picker-indicator': {
                cursor: 'pointer',
                fontSize: '1.125rem',
                padding: '4px',
                borderRadius: '4px',
                transition: 'all 0.2s',
                '&:hover': {
                  backgroundColor: alpha(theme.palette.primary.main, 0.08),
                },
              },
            },
          }}
        />
        <TextField
          label="End Date"
          type="date"
          value={rangeValue.end}
          onChange={(e) => handleEndChange(e.target.value)}
          required={field.required}
          error={!!error}
          disabled={disabled}
          variant="outlined"
          size="small"
          inputRef={endInputRef}
          onClick={handleEndClick}
          InputLabelProps={{
            shrink: true,
            sx: { fontSize: '0.875rem', fontWeight: 500 },
          }}
          sx={{
            flex: 1,
            ...getBaseFieldStyles(theme),
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
              padding: '10.5px 14px',
              fontWeight: 500,
              cursor: 'pointer',
              '&::-webkit-calendar-picker-indicator': {
                cursor: 'pointer',
                fontSize: '1.125rem',
                padding: '4px',
                borderRadius: '4px',
                transition: 'all 0.2s',
                '&:hover': {
                  backgroundColor: alpha(theme.palette.primary.main, 0.08),
                },
              },
            },
          }}
        />
      </Box>
      {error && (
        <FormHelperText error sx={{ mt: 0.75, ml: 1, fontSize: '0.75rem' }}>
          {error}
        </FormHelperText>
      )}
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const BooleanFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => <CheckboxFieldRenderer field={field} value={value} onChange={onChange} error={error} disabled={disabled} />;

export const TagsFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const [inputValue, setInputValue] = React.useState('');
  const tags = Array.isArray(value) ? value : [];

  const handleAddTag = () => {
    if (inputValue.trim() && !tags.includes(inputValue.trim())) {
      onChange([...tags, inputValue.trim()]);
      setInputValue('');
    }
  };

  const handleRemoveTag = (tagToRemove: string) => {
    onChange(tags.filter((tag) => tag !== tagToRemove));
  };

  const handleKeyPress = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      handleAddTag();
    }
  };

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder || 'Type and press Enter to add tags'}
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyPress={handleKeyPress}
        error={!!error}
        helperText={error}
        disabled={disabled}
        variant="outlined"
        size="small"
        sx={{
          mb: tags.length > 0 ? 1.5 : 0,
          ...getBaseFieldStyles(theme),
        }}
      />
      {tags.length > 0 && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, mb: 0.75 }}>
          {tags.map((tag, index) => (
            <Chip
              key={index}
              label={tag}
              onDelete={() => handleRemoveTag(tag)}
              size="small"
              variant="outlined"
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
                '& .MuiChip-deleteIcon': {
                  fontSize: '1rem',
                  '&:hover': {
                    color: theme.palette.error.main,
                  },
                },
              }}
            />
          ))}
        </Box>
      )}
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

export const JsonFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const [jsonString, setJsonString] = React.useState('');
  const [jsonError, setJsonError] = React.useState('');

  React.useEffect(() => {
    if (value) {
      try {
        setJsonString(JSON.stringify(value, null, 2));
      } catch (e) {
        setJsonString(String(value));
      }
    }
  }, [value]);

  const handleChange = (newValue: string) => {
    setJsonString(newValue);
    setJsonError('');

    if (newValue.trim()) {
      try {
        const parsed = JSON.parse(newValue);
        onChange(parsed);
      } catch (e) {
        setJsonError('Invalid JSON format');
      }
    } else {
      onChange(null);
    }
  };

  return (
    <Box>
      <TextField
        fullWidth
        label={field.displayName}
        placeholder={field.placeholder || 'Enter valid JSON'}
        multiline
        rows={4}
        value={jsonString}
        onChange={(e) => handleChange(e.target.value)}
        required={field.required}
        error={!!error || !!jsonError}
        helperText={error || jsonError}
        disabled={disabled}
        variant="outlined"
        size="small"
        sx={{
          ...getBaseFieldStyles(theme),
          '& .MuiOutlinedInput-input': {
            fontSize: '0.8125rem',
            fontFamily: '"SF Mono", "Roboto Mono", Monaco, Consolas, monospace',
            padding: '10.5px 14px',
            lineHeight: 1.6,
          },
        }}
      />
      <FieldDescription description={field.description} error={error || jsonError} />
    </Box>
  );
};

export const FileFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  // Check if this is a JSON file field (service account JSON, etc.)
  const isJsonFile = field.validation?.format?.includes('json') || field.name?.toLowerCase().includes('json');

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // If this is a JSON file field, read and parse the JSON
    if (isJsonFile) {
      try {
        const text = await file.text();
        const parsed = JSON.parse(text);

        // Validate required fields for Google Cloud Service Account JSON
        if (field.name === 'serviceAccountJson') {
          const requiredFields = ['type', 'project_id', 'private_key', 'client_email'];
          const missingFields = requiredFields.filter((fieldName) => !parsed[fieldName]);

          if (missingFields.length > 0) {
            return;
          }

          // Validate that it's a service account JSON
          if (parsed.type !== 'service_account') {
            return;
          }
        }

        // Store the parsed JSON as stringified JSON string to match backend expectations
        onChange(JSON.stringify(parsed));
      } catch (parseError) {
        // Silently handle parse errors
      }
    } else {
      // For non-JSON files, store the File object directly
      onChange(file);
    }
  };

  const handleButtonClick = () => {
    fileInputRef.current?.click();
  };

  const handleRemoveFile = () => {
    onChange(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  return (
    <Box>
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        style={{ display: 'none' }}
        accept={field.validation?.format || '*'}
        disabled={disabled}
      />
      
      {value ? (
        <Box
          sx={{
            p: 1.5,
            borderRadius: 1.25,
            border: `1px solid ${alpha(theme.palette.divider, theme.palette.mode === 'dark' ? 0.2 : 0.15)}`,
            backgroundColor: theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : alpha(theme.palette.background.paper, 0.8),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 1.5,
            transition: 'all 0.2s',
            '&:hover': {
              borderColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.4 : 0.3),
              backgroundColor: theme.palette.mode === 'dark'
                ? alpha(theme.palette.background.paper, 0.8)
                : alpha(theme.palette.background.paper, 1),
            },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, flex: 1, minWidth: 0 }}>
            <Box
              sx={{
                p: 0.75,
                borderRadius: 1,
                backgroundColor: alpha(theme.palette.primary.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}
            >
              <Iconify
                icon="eva:file-outline"
                width={18}
                color={theme.palette.primary.main}
              />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography 
                variant="body2" 
                sx={{ 
                  fontWeight: 500, 
                  fontSize: '0.875rem', 
                  wordBreak: 'break-all',
                  lineHeight: 1.4,
                }}
              >
                {value instanceof File ? value.name : 'File uploaded'}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                {value instanceof File ? `${(value.size / 1024).toFixed(1)} KB` : ''}
              </Typography>
            </Box>
          </Box>
          <IconButton
            size="small"
            onClick={handleRemoveFile}
            disabled={disabled}
            sx={{
              color: theme.palette.text.secondary,
              flexShrink: 0,
              '&:hover': {
                backgroundColor: alpha(theme.palette.error.main, 0.08),
                color: theme.palette.error.main,
              },
            }}
          >
            <Iconify icon="eva:close-outline" width={18} />
          </IconButton>
        </Box>
      ) : (
        <Button
          variant="outlined"
          onClick={handleButtonClick}
          disabled={disabled}
          fullWidth
          sx={{
            height: 46,
            borderRadius: 1.25,
            borderStyle: 'dashed',
            borderWidth: 1.5,
            borderColor: alpha(theme.palette.divider, theme.palette.mode === 'dark' ? 0.3 : 0.3),
            backgroundColor: theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : alpha(theme.palette.background.paper, 0.8),
            transition: 'all 0.2s',
            '&:hover': {
              borderStyle: 'solid',
              borderColor: theme.palette.primary.main,
              backgroundColor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.12 : 0.04),
            },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Iconify icon="eva:upload-outline" width={18} />
            <Typography variant="body2" sx={{ fontSize: '0.875rem', fontWeight: 500 }}>
              {field.placeholder || 'Click to upload file'}
            </Typography>
          </Box>
        </Button>
      )}
      
      {error && (
        <FormHelperText error sx={{ mt: 0.75, ml: 1, fontSize: '0.75rem' }}>
          {error}
        </FormHelperText>
      )}
      
      <FieldDescription description={field.description} error={error} />
    </Box>
  );
};

// Main field renderer that determines which component to use
export const FieldRenderer: React.FC<BaseFieldProps> = (props) => {
  const { field } = props;

  switch (field.fieldType) {
    case 'TEXT':
      return <TextFieldRenderer {...props} />;
    case 'PASSWORD':
      return <PasswordFieldRenderer {...props} />;
    case 'EMAIL':
      return <EmailFieldRenderer {...props} />;
    case 'URL':
      return <UrlFieldRenderer {...props} />;
    case 'TEXTAREA':
      return <TextareaFieldRenderer {...props} />;
    case 'SELECT':
      return <SelectFieldRenderer {...props} />;
    case 'MULTISELECT':
      return <MultiSelectFieldRenderer {...props} />;
    case 'CHECKBOX':
      return <CheckboxFieldRenderer {...props} />;
    case 'NUMBER':
      return <NumberFieldRenderer {...props} />;
    case 'DATE':
      return <DateFieldRenderer {...props} />;
    case 'DATETIME':
      return <DateTimeFieldRenderer {...props} />;
    case 'DATERANGE':
      return <DateRangeFieldRenderer {...props} />;
    case 'DATETIMERANGE':
      return <DateTimeRangeFieldRenderer {...props} />;
    case 'BOOLEAN':
      return <BooleanFieldRenderer {...props} />;
    case 'TAGS':
      return <TagsFieldRenderer {...props} />;
    case 'JSON':
      return <JsonFieldRenderer {...props} />;
    case 'FILE':
      return <FileFieldRenderer {...props} />;
    default:
      return <TextFieldRenderer {...props} />;
  }
};