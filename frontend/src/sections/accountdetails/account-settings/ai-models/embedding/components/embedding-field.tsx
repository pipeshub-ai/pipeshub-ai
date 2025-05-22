// components/embedding-field.tsx

import React, { useState, memo } from 'react';
import { Controller } from 'react-hook-form';
import { alpha, useTheme } from '@mui/material/styles';
import eyeIcon from '@iconify-icons/eva/eye-fill';
import eyeOffIcon from '@iconify-icons/eva/eye-off-fill';
import keyIcon from '@iconify-icons/mdi/key';
import robotIcon from '@iconify-icons/mdi/robot';
import { IconifyIcon } from '@iconify/react';
import {
  TextField,
  InputAdornment,
  IconButton,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

interface EmbeddingFieldProps {
  name: string;
  label: string;
  control: any;
  required?: boolean;
  isEditing: boolean;
  isDisabled?: boolean;
  type?: 'text' | 'password';
  placeholder?: string;
  icon?: string | IconifyIcon;
  defaultIcon?: string | IconifyIcon;
}

// Using React.memo to prevent unnecessary re-renders
const EmbeddingField = memo(({
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
}: EmbeddingFieldProps) => {
  const theme = useTheme();
  const [showPassword, setShowPassword] = useState(false);
  const isPasswordField = type === 'password';

  // Use provided icon or default based on field name
  const fieldIcon = icon || defaultIcon || (
    name === 'apiKey' ? keyIcon : 
    name === 'model' ? robotIcon : 
    keyIcon
  );

  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState }) => (
        <TextField
          {...field}
          label={label}
          fullWidth
          size="small"
          error={!!fieldState.error}
          helperText={fieldState.error?.message || placeholder}
          required={required}
          disabled={!isEditing || isDisabled}
          type={isPasswordField ? (showPassword ? 'text' : 'password') : 'text'}
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
            // Add a fixed height for the helper text area to prevent jumps
            '& .MuiFormHelperText-root': {
              minHeight: '1.25rem',
              margin: '4px 0 0',
            },
          }}
        />
      )}
    />
  );
});

// Display name for debugging
EmbeddingField.displayName = 'EmbeddingField';

export { EmbeddingField };