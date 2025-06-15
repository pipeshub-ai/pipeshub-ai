import React, { useState, memo, useCallback } from 'react';
import { Control, Controller, FieldValues } from 'react-hook-form';
import { alpha, useTheme } from '@mui/material/styles';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import keyIcon from '@iconify-icons/mdi/key';
import robotIcon from '@iconify-icons/mdi/robot';
import fileUploadIcon from '@iconify-icons/ri/file-upload-fill';
import uploadCloudIcon from '@iconify-icons/ri/upload-cloud-2-line';
import checkCircleIcon from '@iconify-icons/mdi/check-circle-outline';
import deleteIcon from '@iconify-icons/ri/delete-bin-line';
import fileTextIcon from '@iconify-icons/ri/file-text-line';
import infoOutlineIcon from '@iconify-icons/eva/info-outline';
import { IconifyIcon } from '@iconify/react';
import {
  TextField,
  InputAdornment,
  IconButton,
  Typography,
  Box,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  FormHelperText,
  Stack,
  Button,
  Fade,
  CircularProgress,
  Tooltip,
  Checkbox,
  FormControlLabel,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

interface DynamicFieldProps {
  name: string;
  label: string;
 control: Control<FieldValues>;
   required?: boolean;
  isEditing: boolean;
  isDisabled?: boolean;
  type?: 'text' | 'password' | 'email' | 'number' | 'url' | 'select' | 'file' | 'checkbox';
  placeholder?: string;
  icon?: string | IconifyIcon;
  defaultIcon?: string | IconifyIcon;
  modelPlaceholder?: string;
  options?: Array<{ value: string; label: string }>;
  multiline?: boolean;
  rows?: number;
  // File upload specific props
  acceptedFileTypes?: string[];
  maxFileSize?: number;
  onFileProcessed?: (data: Record<string, unknown>, fileName: string) => void;
  fileProcessor?: (data: Record<string, unknown>) => Record<string, unknown>;
}

const DynamicField = memo(({
  name,
  label,
  control,
  required = true,
  isEditing,
  isDisabled = false,
  type = 'text',
  placeholder = '',
  icon,
  defaultIcon,
  modelPlaceholder,
  options,
  multiline = false,
  rows = 4,
  acceptedFileTypes = ['.json'],
  maxFileSize = 5 * 1024 * 1024, // 5MB
  onFileProcessed,
  fileProcessor,
}: DynamicFieldProps) => {
  const theme = useTheme();
  const [showPassword, setShowPassword] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string>('');

  // Field type detection
  const isPasswordField = type === 'password';
  const isSelectField = type === 'select';
  const isFileField = type === 'file';
  const isCheckboxField = type === 'checkbox';
  const isModelField = name === 'model';
  const isNumberField = type === 'number';

  // Icon selection logic
  const fieldIcon = icon || defaultIcon || (
    name === 'apiKey' ? keyIcon : 
    name === 'model' ? robotIcon : 
    isFileField ? fileUploadIcon :
    keyIcon
  );

  // File upload handlers
  const validateFileType = useCallback((file: File): boolean => {
    const fileExtension = file.name.toLowerCase().substring(file.name.lastIndexOf('.'));
    return acceptedFileTypes.includes(fileExtension);
  }, [acceptedFileTypes]);

  const processFile = useCallback((file: File) => {
    setIsProcessing(true);
    setUploadError('');

    // Check file size
    if (file.size > maxFileSize) {
      setUploadError(`File is too large. Maximum size is ${maxFileSize / (1024 * 1024)} MB.`);
      setIsProcessing(false);
      return;
    }

    // Check file type
    if (!validateFileType(file)) {
      setUploadError(`Only ${acceptedFileTypes.join(', ')} files are supported.`);
      setIsProcessing(false);
      return;
    }

    setUploadedFile(file);
    const reader = new FileReader();

    reader.onload = (e: ProgressEvent<FileReader>) => {
      if (e.target && typeof e.target.result === 'string') {
        try {
          const jsonData = JSON.parse(e.target.result);
          
          // Use custom file processor if provided
          if (fileProcessor) {
            try {
              const extractedData = fileProcessor(jsonData);
              if (onFileProcessed) {
                onFileProcessed(extractedData, file.name);
              }
            } catch (processorError: any) {
              setUploadError(processorError.message || 'Error processing file data');
              setUploadedFile(null);
              setIsProcessing(false);
              return;
            }
          } else if (onFileProcessed) {
            // Fallback to basic processing
            onFileProcessed(jsonData, file.name);
          }
          
          setIsProcessing(false);
        } catch (error: any) {
          setUploadError(`Invalid JSON format: ${error.message || 'The file does not contain valid JSON data.'}`);
          setUploadedFile(null);
          setIsProcessing(false);
        }
      }
    };

    reader.onerror = () => {
      setUploadError('Error reading file. Please try again.');
      setUploadedFile(null);
      setIsProcessing(false);
    };

    reader.readAsText(file);
  }, [validateFileType, maxFileSize, acceptedFileTypes, onFileProcessed, fileProcessor]);

  const handleFileChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    setUploadError('');
    const { files } = event.target;

    if (files && files[0]) {
      processFile(files[0]);
    }
    // Reset input
    event.target.value = '';
  }, [processFile]);

  const handleDragEnter = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) {
      e.dataTransfer.dropEffect = 'copy';
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const { files } = e.dataTransfer;
    if (files.length > 1) {
      setUploadError('Please drop only one file.');
      return;
    }

    if (files && files[0]) {
      processFile(files[0]);
    }
  }, [processFile]);

  const handleRemoveFile = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setUploadedFile(null);
    setUploadError('');
  }, []);

  // Checkbox field renderer
  if (isCheckboxField) {
    return (
      <Box>
        <Controller
          name={name}
          control={control}
          render={({ field, fieldState }) => (
            <FormControlLabel
              control={
                <Checkbox
                  {...field}
                  checked={!!field.value}
                  onChange={(e) => field.onChange(e.target.checked)}
                  disabled={!isEditing || isDisabled}
                  sx={{
                    color: theme.palette.primary.main,
                    '&.Mui-checked': {
                      color: theme.palette.primary.main,
                    },
                  }}
                />
              }
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Iconify icon={fieldIcon} width={18} height={18} />
                  <Typography variant="body2">
                    {label}{required ? ' *' : ''}
                  </Typography>
                </Box>
              }
              sx={{
                margin: 0,
                '& .MuiFormControlLabel-label': {
                  fontSize: '0.875rem',
                },
              }}
            />
          )}
        />
        {placeholder && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block', ml: 4 }}>
            {placeholder}
          </Typography>
        )}
      </Box>
    );
  }

  // File upload field renderer
  if (isFileField) {
    const uploadAreaStyles = {
      border: `2px dashed ${
        isDragging
          ? theme.palette.primary.main
          : uploadedFile
            ? theme.palette.success.main
            : alpha(theme.palette.text.primary, 0.15)
      }`,
      borderRadius: '12px',
      height: 180,
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      textAlign: 'center',
      bgcolor: isDragging
        ? alpha(theme.palette.primary.main, 0.04)
        : uploadedFile
          ? alpha(theme.palette.success.main, 0.04)
          : alpha(theme.palette.background.default, 0.6),
      cursor: isProcessing ? 'wait' : 'pointer',
      transition: theme.transitions.create(
        ['border-color', 'background-color', 'box-shadow', 'transform'],
        { duration: theme.transitions.duration.shorter }
      ),
      '&:hover': {
        borderColor: !isProcessing ? theme.palette.primary.main : undefined,
        bgcolor: !isProcessing ? alpha(theme.palette.primary.main, 0.04) : undefined,
        boxShadow: !isProcessing
          ? `0 4px 12px ${alpha(theme.palette.primary.main, 0.08)}`
          : undefined,
        transform: !isProcessing ? 'translateY(-2px)' : undefined,
      },
      position: 'relative',
      px: 2,
      py: 3,
    };

    return (
      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
          {label}{required ? ' *' : ''}
        </Typography>
        
        <Tooltip
          title={
            isProcessing
              ? 'Processing file...'
              : 'Drag and drop your file or click to browse'
          }
          placement="top"
          arrow
        >
          <Box
            component="div"
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            role="button"
            tabIndex={0}
            onClick={() => !isProcessing && document.getElementById(`file-upload-${name}`)?.click()}
            onKeyDown={(e) => {
              if (!isProcessing && (e.key === 'Enter' || e.key === ' ')) {
                document.getElementById(`file-upload-${name}`)?.click();
              }
            }}
            aria-disabled={isProcessing || isDisabled || !isEditing}
            sx={uploadAreaStyles}
          >
            {isProcessing ? (
              <Stack spacing={1.5} alignItems="center">
                <CircularProgress
                  size={32}
                  thickness={4}
                  sx={{ color: theme.palette.primary.main }}
                />
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  Processing file...
                </Typography>
              </Stack>
            ) : !uploadedFile ? (
              <>
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 60,
                    height: 60,
                    borderRadius: '50%',
                    background: `linear-gradient(135deg, ${alpha(theme.palette.primary.light, 0.12)}, ${alpha(theme.palette.primary.main, 0.15)})`,
                    mb: 2,
                    transition: theme.transitions.create(['transform', 'background-color'], {
                      duration: theme.transitions.duration.shorter,
                    }),
                    ...(isDragging && {
                      transform: 'scale(1.1)',
                      bgcolor: alpha(theme.palette.primary.main, 0.12),
                    }),
                  }}
                >
                  <Iconify
                    icon={isDragging ? fileUploadIcon : uploadCloudIcon}
                    width={32}
                    height={32}
                    sx={{
                      color: theme.palette.primary.main,
                      transition: theme.transitions.create('transform', {
                        duration: theme.transitions.duration.shortest,
                      }),
                      ...(isDragging && {
                        transform: 'scale(1.1)',
                      }),
                    }}
                  />
                </Box>
                <Typography
                  variant="body1"
                  sx={{
                    fontWeight: 600,
                    color: isDragging ? theme.palette.primary.main : 'text.primary',
                  }}
                >
                  {isDragging ? 'Drop file here' : 'Upload file'}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                  or click to browse files
                </Typography>
                <Box
                  sx={{
                    mt: 1,
                    px: 1.5,
                    py: 0.5,
                    borderRadius: '6px',
                    bgcolor: alpha(theme.palette.info.main, 0.08),
                    display: 'flex',
                    alignItems: 'center',
                  }}
                >
                  <Iconify
                    icon={infoOutlineIcon}
                    width={14}
                    height={14}
                    sx={{ color: theme.palette.info.main, mr: 0.5 }}
                  />
                  <Typography
                    variant="caption"
                    sx={{ fontWeight: 500, color: theme.palette.info.main, fontSize: '0.65rem' }}
                  >
                    {acceptedFileTypes.join(', ')} files supported (max {Math.round(maxFileSize / (1024 * 1024))}MB)
                  </Typography>
                </Box>
              </>
            ) : (
              <Fade in={!!uploadedFile}>
                <Stack spacing={1.5} alignItems="center" width="100%">
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: 52,
                      height: 52,
                      borderRadius: '50%',
                      background: `linear-gradient(135deg, ${alpha(theme.palette.success.light, 0.2)}, ${alpha(
                        theme.palette.success.main,
                        0.2
                      )})`,
                      boxShadow: `0 4px 12px ${alpha(theme.palette.success.main, 0.15)}`,
                      mb: 1,
                    }}
                  >
                    <Iconify
                      icon={checkCircleIcon}
                      width={28}
                      height={28}
                      sx={{ color: theme.palette.success.main }}
                    />
                  </Box>

                  <Box
                    sx={{
                      px: 2,
                      py: 0.75,
                      borderRadius: '8px',
                      bgcolor: alpha(theme.palette.background.paper, 0.5),
                      border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                      boxShadow: `0 2px 8px ${alpha(theme.palette.common.black, 0.04)}`,
                      display: 'flex',
                      alignItems: 'center',
                      width: 'auto',
                      maxWidth: '100%',
                    }}
                  >
                    <Iconify
                      icon={fileTextIcon}
                      width={20}
                      height={20}
                      sx={{ color: theme.palette.primary.main, flexShrink: 0, mr: 1 }}
                    />
                    <Typography
                      variant="body2"
                      fontWeight={500}
                      sx={{
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {uploadedFile.name}
                    </Typography>
                  </Box>

                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ fontSize: '0.7rem' }}
                  >
                    {(uploadedFile.size / 1024).toFixed(1)} KB
                  </Typography>

                  <Button
                    size="small"
                    color="error"
                    variant="outlined"
                    startIcon={<Iconify icon={deleteIcon} width={18} height={18} />}
                    sx={{
                      borderRadius: '10px',
                      textTransform: 'none',
                      px: 2,
                      py: 0.75,
                      fontSize: '0.75rem',
                      fontWeight: 600,
                      boxShadow: `0 2px 8px ${alpha(theme.palette.error.main, 0.15)}`,
                      background: `linear-gradient(135deg, ${alpha(theme.palette.error.light, 0.2)}, ${alpha(
                        theme.palette.error.main,
                        0.2
                      )})`,
                      '&:hover': {
                        background: `linear-gradient(135deg, ${alpha(theme.palette.error.light, 0.3)}, ${alpha(
                          theme.palette.error.main,
                          0.3
                        )})`,
                      },
                    }}
                    onClick={handleRemoveFile}
                  >
                    Remove
                  </Button>
                </Stack>
              </Fade>
            )}
          </Box>
        </Tooltip>

        {/* Error message */}
        {uploadError && (
          <Typography variant="caption" color="error" sx={{ mt: 1, display: 'block' }}>
            {uploadError}
          </Typography>
        )}

        {/* Helper text */}
        {placeholder && !uploadError && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
            {placeholder}
          </Typography>
        )}

        {/* Hidden file input */}
        <input
          id={`file-upload-${name}`}
          type="file"
          accept={acceptedFileTypes.join(',')}
          hidden
          onChange={handleFileChange}
          disabled={isProcessing || isDisabled || !isEditing}
        />
      </Box>
    );
  }

  // Select field renderer
  if (isSelectField && options) {
    return (
      <Box>
        <Controller
          name={name}
          control={control}
          render={({ field, fieldState }) => (
            <FormControl 
              fullWidth 
              size="small" 
              error={!!fieldState.error}
              disabled={!isEditing || isDisabled}
            >
              <InputLabel>{label}{required ? ' *' : ''}</InputLabel>
              <Select
                {...field}
                label={`${label}${required ? ' *' : ''}`}
                startAdornment={
                  <InputAdornment position="start">
                    <Iconify icon={fieldIcon} width={18} height={18} />
                  </InputAdornment>
                }
                sx={{
                  '& .MuiOutlinedInput-root': {
                    '& fieldset': {
                      borderColor: alpha(theme.palette.text.primary, 0.15),
                    },
                    '&:hover fieldset': {
                      borderColor: alpha(theme.palette.primary.main, 0.5),
                    },
                  },
                }}
              >
                {options.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </Select>
              {fieldState.error && (
                <FormHelperText>{fieldState.error.message}</FormHelperText>
              )}
            </FormControl>
          )}
        />
      </Box>
    );
  }

  // Regular input field renderer (text, password, email, url, number, multiline)
  return (
    <Box>
      <Controller
        name={name}
        control={control}
        render={({ field, fieldState }) => (
          <TextField
            {...field}
            label={`${label}${required ? ' *' : ''}`}
            fullWidth
            size="small"
            multiline={multiline}
            rows={multiline ? rows : 1}
            error={!!fieldState.error}
            helperText={fieldState.error?.message || (!isModelField ? placeholder : undefined)}
            placeholder={placeholder}
            required={required}
            disabled={!isEditing || isDisabled}
            type={isPasswordField ? (showPassword ? 'text' : 'password') : 
                  isNumberField ? 'number' : 
                  type}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon={fieldIcon} width={18} height={18} />
                </InputAdornment>
              ),
              endAdornment: isPasswordField ? (
                <InputAdornment position="end">
                  <IconButton
                    onClick={() => setShowPassword(!showPassword)}
                    edge="end"
                    size="small"
                    disabled={!isEditing || isDisabled}
                  >
                    <Iconify
                      icon={showPassword ? eyeOffIcon : eyeIcon}
                      width={16}
                      height={16}
                    />
                  </IconButton>
                </InputAdornment>
              ) : null,
            }}
            sx={{
              '& .MuiOutlinedInput-root': {
                '& fieldset': {
                  borderColor: alpha(theme.palette.text.primary, 0.15),
                },
                '&:hover fieldset': {
                  borderColor: alpha(theme.palette.primary.main, 0.5),
                },
              },
              '& .MuiFormHelperText-root': {
                minHeight: '1.25rem',
                margin: '4px 0 0',
              },
            }}
            // Handle number field conversion
            onChange={(e) => {
              if (isNumberField) {
                const value = e.target.value;
                field.onChange(value === '' ? undefined : Number(value));
              } else {
                field.onChange(e);
              }
            }}
            // Convert number values back to string for display
            value={isNumberField && field.value !== undefined ? String(field.value) : field.value || ''}
          />
        )}
      />
      
      {/* Show model placeholder below model field */}
      {isModelField && modelPlaceholder && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            display: 'block',
            mt: 0.5,
            ml: 0,
            fontStyle: 'italic',
            opacity: 0.8,
          }}
        >
          {modelPlaceholder}
        </Typography>
      )}
    </Box>
  );
});

DynamicField.displayName = 'DynamicField';

// 🔥 FIX: Export the component directly, not as an object
export default DynamicField;