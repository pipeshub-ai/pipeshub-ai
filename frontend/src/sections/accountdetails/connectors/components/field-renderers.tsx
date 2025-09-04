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
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import { useTheme, alpha } from '@mui/material/styles';
import eyeIcon from '@iconify-icons/mdi/eye';
import eyeOffIcon from '@iconify-icons/mdi/eye-off';

interface BaseFieldProps {
  field: any;
  value: any;
  onChange: (value: any) => void;
  error?: string;
  disabled?: boolean;
}

export const TextFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      required={field.required}
      error={!!error}
      helperText={error}
      disabled={disabled}
      variant="outlined"
      size="small"
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder}
      type={showPassword ? 'text' : 'password'}
      value={value || ''}
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
                  backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                },
              }}
            >
              <Iconify icon={showPassword ? eyeOffIcon : eyeIcon} width={18} height={18} />
            </IconButton>
          </InputAdornment>
        ),
      }}
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder}
      type="email"
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      required={field.required}
      error={!!error}
      helperText={error}
      disabled={disabled}
      variant="outlined"
      size="small"
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder}
      type="url"
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      required={field.required}
      error={!!error}
      helperText={error}
      disabled={disabled}
      variant="outlined"
      size="small"
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder}
      multiline
      rows={4}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      required={field.required}
      error={!!error}
      helperText={error}
      disabled={disabled}
      variant="outlined"
      size="small"
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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
    <FormControl fullWidth size="small" error={!!error}>
      <InputLabel sx={{ fontSize: '0.875rem' }}>{field.displayName}</InputLabel>
      <Select
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        label={field.displayName}
        disabled={disabled}
        sx={{
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
          '& .MuiSelect-select': {
            fontSize: '0.875rem',
          },
        }}
      >
        {field.options?.map((option: string) => (
          <MenuItem key={option} value={option} sx={{ fontSize: '0.875rem' }}>
            {option}
          </MenuItem>
        ))}
      </Select>
      {error && <FormHelperText sx={{ fontSize: '0.75rem' }}>{error}</FormHelperText>}
    </FormControl>
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

  const handleChange = (event: any, newValue: string[]) => {
    onChange(newValue);
  };

  return (
    <Autocomplete
      multiple
      options={field.options || []}
      value={selectedValues}
      onChange={handleChange}
      disabled={disabled}
      size="small"
      renderTags={(val, getTagProps) =>
        val.map((option, index) => (
          <Chip
            variant="outlined"
            label={option}
            size="small"
            {...getTagProps({ index })}
            key={option}
            sx={{
              fontSize: '0.75rem',
              height: 24,
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
            '& .MuiOutlinedInput-root': {
              borderRadius: 1,
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: theme.palette.primary.main,
              },
            },
            '& .MuiInputLabel-root': {
              fontSize: '0.875rem',
            },
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
            },
            '& .MuiFormHelperText-root': {
              fontSize: '0.75rem',
            },
          }}
        />
      )}
    />
  );
};

export const CheckboxFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => (
  <FormControlLabel
    control={
      <Checkbox
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
    }
    label={field.displayName}
  />
);

export const NumberFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => {
  const theme = useTheme();

  return (
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder}
      type="number"
      value={value || ''}
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
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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

  return (
    <TextField
      fullWidth
      label={field.displayName}
      type="date"
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      required={field.required}
      error={!!error}
      helperText={error}
      disabled={disabled}
      variant="outlined"
      size="small"
      InputLabelProps={{
        shrink: true,
      }}
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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

  const handleStartChange = (startDate: string) => {
    onChange({ ...rangeValue, start: startDate });
  };

  const handleEndChange = (endDate: string) => {
    onChange({ ...rangeValue, end: endDate });
  };

  return (
    <Box>
      <Typography variant="body2" sx={{ mb: 1.5, fontWeight: 500, fontSize: '0.875rem' }}>
        {field.displayName}
      </Typography>
      <Box sx={{ display: 'flex', gap: 2 }}>
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
          InputLabelProps={{
            shrink: true,
          }}
          sx={{
            flex: 1,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1,
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: theme.palette.primary.main,
              },
            },
            '& .MuiInputLabel-root': {
              fontSize: '0.875rem',
            },
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
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
          InputLabelProps={{
            shrink: true,
          }}
          sx={{
            flex: 1,
            '& .MuiOutlinedInput-root': {
              borderRadius: 1,
              '&:hover .MuiOutlinedInput-notchedOutline': {
                borderColor: theme.palette.primary.main,
              },
            },
            '& .MuiInputLabel-root': {
              fontSize: '0.875rem',
            },
            '& .MuiOutlinedInput-input': {
              fontSize: '0.875rem',
            },
          }}
        />
      </Box>
      {error && (
        <FormHelperText error sx={{ mt: 1, fontSize: '0.75rem' }}>
          {error}
        </FormHelperText>
      )}
    </Box>
  );
};

export const BooleanFieldRenderer: React.FC<BaseFieldProps> = ({
  field,
  value,
  onChange,
  error,
  disabled,
}) => (
  <FormControl fullWidth error={!!error}>
    <FormControlLabel
      control={
        <Checkbox
          checked={!!value}
          onChange={(e) => onChange(e.target.checked)}
          disabled={disabled}
          sx={{
            '& .MuiSvgIcon-root': {
              fontSize: '1.25rem',
            },
          }}
        />
      }
      label={
        <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
          {field.displayName}
        </Typography>
      }
    />
    {error && <FormHelperText sx={{ fontSize: '0.75rem' }}>{error}</FormHelperText>}
  </FormControl>
);

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
          mb: 1.5,
          '& .MuiOutlinedInput-root': {
            borderRadius: 1,
            '&:hover .MuiOutlinedInput-notchedOutline': {
              borderColor: theme.palette.primary.main,
            },
          },
          '& .MuiInputLabel-root': {
            fontSize: '0.875rem',
          },
          '& .MuiOutlinedInput-input': {
            fontSize: '0.875rem',
          },
          '& .MuiFormHelperText-root': {
            fontSize: '0.75rem',
          },
        }}
      />
      {tags.length > 0 && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
          {tags.map((tag, index) => (
            <Chip
              key={index}
              label={tag}
              onDelete={() => handleRemoveTag(tag)}
              size="small"
              variant="outlined"
              sx={{
                fontSize: '0.75rem',
                height: 24,
                '& .MuiChip-deleteIcon': {
                  fontSize: '1rem',
                },
              }}
            />
          ))}
        </Box>
      )}
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
    <TextField
      fullWidth
      label={field.displayName}
      placeholder={field.placeholder || 'Enter valid JSON'}
      multiline
      rows={6}
      value={jsonString}
      onChange={(e) => handleChange(e.target.value)}
      required={field.required}
      error={!!error || !!jsonError}
      helperText={error || jsonError}
      disabled={disabled}
      variant="outlined"
      size="small"
      sx={{
        '& .MuiOutlinedInput-root': {
          borderRadius: 1,
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: theme.palette.primary.main,
          },
        },
        '& .MuiInputLabel-root': {
          fontSize: '0.875rem',
        },
        '& .MuiOutlinedInput-input': {
          fontSize: '0.875rem',
          fontFamily: 'monospace',
        },
        '& .MuiFormHelperText-root': {
          fontSize: '0.75rem',
        },
      }}
    />
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
    case 'DATERANGE':
      return <DateRangeFieldRenderer {...props} />;
    case 'BOOLEAN':
      return <BooleanFieldRenderer {...props} />;
    case 'TAGS':
      return <TagsFieldRenderer {...props} />;
    case 'JSON':
      return <JsonFieldRenderer {...props} />;
    default:
      return <TextFieldRenderer {...props} />;
  }
};
