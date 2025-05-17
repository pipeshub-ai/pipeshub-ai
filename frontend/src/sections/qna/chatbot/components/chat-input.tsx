// ChatInput.tsx
import React from 'react';
import { Icon } from '@iconify/react';
import sendIcon from '@iconify-icons/mdi/send';
import { Box, Paper, TextField, IconButton, useTheme, alpha } from '@mui/material';

type ChatInputProps = {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSubmit: () => Promise<void>;
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
};

const ChatInput: React.FC<ChatInputProps> = ({
  value,
  onChange,
  onSubmit,
  isLoading,
  disabled = false,
  placeholder = 'Type your message...',
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() && !isLoading) {
        onSubmit();
      }
    }
  };

  return (
    <Box sx={{ p: 1, width: { xs: '90%', sm: '80%', md: '70%' }, mx: 'auto' }}>
      <Paper
        elevation={0}
        sx={{
          display: 'flex',
          alignItems: 'center',
          p: '6px 12px',
          borderRadius: '10px',
          backgroundColor: isDark ? 'rgba(30, 30, 35, 0.7)' : '#f8f9fa',
          border: '1px solid',
          borderColor: isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.06)',
          boxShadow: isDark 
            ? '0 4px 12px rgba(0, 0, 0, 0.3)' 
            : '0 2px 8px rgba(0, 0, 0, 0.05)',
          transition: 'all 0.2s',
          '&:hover': {
            borderColor: isDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.09)',
          },
          '&:focus-within': {
            borderColor: alpha(theme.palette.primary.main, isDark ? 0.5 : 0.3),
            boxShadow: `0 0 0 1px ${alpha(theme.palette.primary.main, 0.15)}`,
          },
        }}
      >
        <TextField
          fullWidth
          placeholder={placeholder || "Type your message..."}
          variant="standard"
          InputProps={{
            disableUnderline: true,
            sx: {
              color: isDark ? 'rgba(255, 255, 255, 0.9)' : 'rgba(0, 0, 0, 0.8)',
              fontSize: '0.925rem',
              '::placeholder': {
                color: isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.4)',
                opacity: 1,
              },
              padding: '2px 0',
            }
          }}
          sx={{ 
            mx: 1.5,
            '& .MuiInputBase-multiline': {
              padding: '6px 0',
            }
          }}
          value={value}
          onChange={onChange}
          onKeyPress={handleKeyPress}
          disabled={disabled}
          multiline
          maxRows={4}
        />

        <IconButton
          size="small"
          onClick={onSubmit}
          disabled={!value.trim() || isLoading || disabled}
          sx={{
            backgroundColor: (value.trim() && !isLoading && !disabled) 
              ? alpha(theme.palette.primary.main, isDark ? 0.15 : 0.08) 
              : 'transparent',
            width: 34,
            height: 34,
            transition: 'all 0.2s',
            color: (value.trim() && !isLoading && !disabled)
              ? theme.palette.primary.main
              : isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.3)',
            '&:hover': {
              backgroundColor: (value.trim() && !isLoading && !disabled)
                ? alpha(theme.palette.primary.main, isDark ? 0.25 : 0.12) 
                : isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.03)',
            },
            '&.Mui-disabled': {
              opacity: 0.5,
            }
          }}
        >
          <Icon icon={sendIcon} style={{ fontSize: '1.125rem' }} />
        </IconButton>
      </Paper>
    </Box>
  );
};

export default ChatInput;