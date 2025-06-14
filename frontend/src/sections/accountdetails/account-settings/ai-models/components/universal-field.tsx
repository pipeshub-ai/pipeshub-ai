// ====================================
// 4. components/UniversalField.tsx
// ====================================

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
  Typography,
  Box,
} from '@mui/material';

import { Iconify } from 'src/components/iconify';

interface UniversalFieldProps {
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
  modelPlaceholder?: string; 
}

const UniversalField = memo(({
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
}: UniversalFieldProps) => {
  const theme = useTheme();
  const [showPassword, setShowPassword] = useState(false);
  const isPasswordField = type === 'password';
  const isModelField = name === 'model';

  const fieldIcon = icon || defaultIcon || (
    name === 'apiKey' ? keyIcon : 
    name === 'model' ? robotIcon : 
    keyIcon
  );

  return (
    <Box>
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
            helperText={fieldState.error?.message || (!isModelField ? placeholder : undefined)}
            placeholder={placeholder}
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
              '& .MuiFormHelperText-root': {
                minHeight: '1.25rem',
                margin: '4px 0 0',
              },
            }}
          />
        )}
      />
      
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

UniversalField.displayName = 'UniversalField';

export { UniversalField };