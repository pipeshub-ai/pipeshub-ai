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

interface BaseFieldProps {
  field: any;
  value: any;
  onChange: (value: any) => void;
  error?: string;
  disabled?: boolean;
}

const getBaseFieldStyles = (theme: any) => ({
  '& .MuiOutlinedInput-root': {
    borderRadius: 1.5,
    backgroundColor: alpha(theme.palette.background.paper, 0.8),
    '&:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: theme.palette.primary.main,
    },
    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderWidth: 1.5,
    },
  },
  '& .MuiInputLabel-root': {
    fontSize: '0.8125rem',
    fontWeight: 500,
    '&.Mui-focused': {
      fontSize: '0.8125rem',
    },
  },
  '& .MuiOutlinedInput-input': {
    fontSize: '0.8125rem',
    padding: '12px 14px',
    fontWeight: 400,
  },
  '& .MuiFormHelperText-root': {
    fontSize: '0.75rem',
    fontWeight: 400,
    marginTop: 0.5,
    marginLeft: 1,
  },
});

const FieldDescription: React.FC<{ description: string; error?: string; marginLeft?: number }> = ({ 
  description, 
  error, 
  marginLeft = 0.5 
}) => {
  if (!description || error) return null;
  
  return (
    <Typography 
      variant="caption" 
      color="text.secondary" 
      sx={{ 
        display: 'block', 
        mt: 0.5, 
        ml: marginLeft,
        fontSize: '0.75rem',
        lineHeight: 1.4,
        fontWeight: 400,
        opacity: 0.85,
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
        value={value || ''}
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
                    backgroundColor: alpha(theme.palette.text.secondary, 0.08),
                  },
                }}
              >
                <Iconify icon={showPassword ? eyeOffIcon : eyeIcon} width={16} height={16} />
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
                <Iconify icon={showPassword ? eyeOffIcon : eyeIcon} width={16} height={16} />
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
        value={value || ''}
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
        value={value || ''}
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
        value={value || ''}
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
            ...getBaseFieldStyles(theme)['& .MuiOutlinedInput-input'],
            padding: '12px 14px',
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
        <InputLabel sx={{ fontSize: '0.8125rem', fontWeight: 500 }}>{field.displayName}</InputLabel>
        <Select
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          label={field.displayName}
          disabled={disabled}
          sx={{
            borderRadius: 1.5,
            backgroundColor: alpha(theme.palette.background.paper, 0.8),
            '&:hover .MuiOutlinedInput-notchedOutline': {
              borderColor: theme.palette.primary.main,
            },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
              borderWidth: 1.5,
            },
            '& .MuiSelect-select': {
              fontSize: '0.8125rem',
              padding: '12px 14px',
              fontWeight: 400,
            },
          }}
        >
          {field.options?.map((option: string) => (
            <MenuItem key={option} value={option} sx={{ fontSize: '0.8125rem' }}>
              {option}
            </MenuItem>
          ))}
        </Select>
        {error && <FormHelperText sx={{ fontSize: '0.75rem', mt: 0.5, ml: 1 }}>{error}</FormHelperText>}
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

  const handleChange = (event: any, newValue: string[]) => {
    onChange(newValue);
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
                height: 22,
                borderRadius: 1,
                '& .MuiChip-label': {
                  px: 0.75,
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
                fontSize: '0.8125rem',
                padding: '10px 12px !important',
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
              '& .MuiSvgIcon-root': {
                fontSize: '1.125rem',
              },
            }}
          />
        }
        label={
          <Typography variant="body2" sx={{ fontSize: '0.8125rem', fontWeight: 500 }}>
            {field.displayName}
          </Typography>
        }
        sx={{ mb: error ? 0.5 : 0 }}
      />
      {error && (
        <FormHelperText error sx={{ mt: 0, ml: 4, fontSize: '0.75rem' }}>
          {error}
        </FormHelperText>
      )}
      <FieldDescription description={field.description} error={error} marginLeft={4.25} />
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
        sx={getBaseFieldStyles(theme)}
      />
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

  return (
    <Box>
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
        sx={getBaseFieldStyles(theme)}
      />
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

  const handleStartChange = (startDate: string) => {
    onChange({ ...rangeValue, start: startDate });
  };

  const handleEndChange = (endDate: string) => {
    onChange({ ...rangeValue, end: endDate });
  };

  return (
    <Box>
      <Typography variant="body2" sx={{ mb: 1.5, fontWeight: 600, fontSize: '0.8125rem' }}>
        {field.displayName}
        {field.required && <span style={{ color: 'red', marginLeft: 4 }}>*</span>}
      </Typography>
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
          InputLabelProps={{
            shrink: true,
          }}
          sx={{
            flex: 1,
            ...getBaseFieldStyles(theme),
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
            ...getBaseFieldStyles(theme),
          }}
        />
      </Box>
      {error && (
        <FormHelperText error sx={{ mt: 0.5, ml: 1, fontSize: '0.75rem' }}>
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
}) => {
  const theme = useTheme();

  return (
    <Box>
      <FormControl fullWidth error={!!error}>
        <FormControlLabel
          control={
            <Checkbox
              checked={!!value}
              onChange={(e) => onChange(e.target.checked)}
              disabled={disabled}
              size="small"
              sx={{
                '& .MuiSvgIcon-root': {
                  fontSize: '1.125rem',
                },
              }}
            />
          }
          label={
            <Typography variant="body2" sx={{ fontSize: '0.8125rem', fontWeight: 500 }}>
              {field.displayName}
            </Typography>
          }
          sx={{ mb: error ? 0.5 : 0 }}
        />
        {error && <FormHelperText sx={{ fontSize: '0.75rem', ml: 4 }}>{error}</FormHelperText>}
      </FormControl>
      <FieldDescription description={field.description} error={error} marginLeft={4.25} />
    </Box>
  );
};

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
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, mb: 1 }}>
          {tags.map((tag, index) => (
            <Chip
              key={index}
              label={tag}
              onDelete={() => handleRemoveTag(tag)}
              size="small"
              variant="outlined"
              sx={{
                fontSize: '0.75rem',
                height: 22,
                borderRadius: 1,
                '& .MuiChip-label': {
                  px: 0.75,
                },
                '& .MuiChip-deleteIcon': {
                  fontSize: '0.875rem',
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
            fontFamily: 'Monaco, Consolas, "Roboto Mono", monospace',
            padding: '12px 14px',
            lineHeight: 1.5,
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

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
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
            borderRadius: 1.5,
            border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
            backgroundColor: alpha(theme.palette.background.paper, 0.8),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 1.5,
            transition: 'all 0.15s ease-in-out',
            '&:hover': {
              borderColor: alpha(theme.palette.primary.main, 0.3),
            },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flex: 1 }}>
            <Box
              sx={{
                p: 0.75,
                borderRadius: 1,
                backgroundColor: alpha(theme.palette.primary.main, 0.1),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Iconify
                icon="eva:file-outline"
                width={16}
                height={16}
                color={theme.palette.primary.main}
              />
            </Box>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" sx={{ fontWeight: 500, fontSize: '0.8125rem', wordBreak: 'break-all' }}>
                {value.name}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.75rem' }}>
                {(value.size / 1024).toFixed(1)} KB
              </Typography>
            </Box>
          </Box>
          <IconButton
            size="small"
            onClick={handleRemoveFile}
            disabled={disabled}
            sx={{
              color: theme.palette.text.secondary,
              '&:hover': {
                backgroundColor: alpha(theme.palette.error.main, 0.08),
                color: theme.palette.error.main,
              },
            }}
          >
            <Iconify icon="eva:close-outline" width={16} height={16} />
          </IconButton>
        </Box>
      ) : (
        <Button
          variant="outlined"
          onClick={handleButtonClick}
          disabled={disabled}
          fullWidth
          sx={{
            height: 48,
            borderRadius: 1.5,
            borderStyle: 'dashed',
            borderColor: alpha(theme.palette.divider, 0.3),
            backgroundColor: alpha(theme.palette.background.paper, 0.8),
            '&:hover': {
              borderStyle: 'solid',
              borderColor: theme.palette.primary.main,
              backgroundColor: alpha(theme.palette.primary.main, 0.04),
            },
            transition: 'all 0.15s ease-in-out',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Iconify icon="eva:upload-outline" width={16} height={16} />
            <Typography variant="body2" sx={{ fontSize: '0.8125rem', fontWeight: 500 }}>
              {field.placeholder || 'Click to upload file'}
            </Typography>
          </Box>
        </Button>
      )}
      
      {error && (
        <FormHelperText error sx={{ mt: 0.5, ml: 1, fontSize: '0.75rem' }}>
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
    case 'DATERANGE':
      return <DateRangeFieldRenderer {...props} />;
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