// Self-Contained ChatInput.tsx - Manages its own state
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Icon } from '@iconify/react';
import sendIcon from '@iconify-icons/mdi/send';
import { Box, Paper, TextField, IconButton, useTheme, alpha } from '@mui/material';

type ChatInputProps = {
  onSubmit: (message: string) => Promise<void>; // Pass message directly
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
};

const ChatInput: React.FC<ChatInputProps> = ({
  onSubmit,
  isLoading,
  disabled = false,
  placeholder = 'Type your message...',
}) => {
  const [localValue, setLocalValue] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Internal change handler - doesn't communicate with parent
  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setLocalValue(e.target.value);
  }, []);

  // Submit handler - only communicates final message to parent
  const handleSubmit = useCallback(async () => {
    const trimmedValue = localValue.trim();
    if (!trimmedValue || isLoading || isSubmitting || disabled) {
      return;
    }

    setIsSubmitting(true);
    
    try {
      // Clear input immediately for better UX
      setLocalValue('');
      
      // Send message to parent (this is the ONLY parent communication)
      await onSubmit(trimmedValue);
    } catch (error) {
      console.error('Failed to send message:', error);
      // Restore message on error
      setLocalValue(trimmedValue);
    } finally {
      setIsSubmitting(false);
      
      // Refocus input
      if (inputRef.current) {
        inputRef.current.focus();
      }
    }
  }, [localValue, isLoading, isSubmitting, disabled, onSubmit]);

  const handleKeyPress = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  // Auto-focus on mount
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
    }
  }, []);

  const canSubmit = localValue.trim() && !isLoading && !isSubmitting && !disabled;

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
          ref={inputRef}
          fullWidth
          placeholder={placeholder}
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
          value={localValue} // Use local state only
          onChange={handleChange} // Use local handler only
          onKeyPress={handleKeyPress}
          disabled={disabled || isSubmitting}
          multiline
          maxRows={4}
        />

        <IconButton
          size="small"
          onClick={handleSubmit}
          disabled={!canSubmit}
          sx={{
            backgroundColor: canSubmit
              ? alpha(theme.palette.primary.main, isDark ? 0.15 : 0.08) 
              : 'transparent',
            width: 34,
            height: 34,
            transition: 'all 0.2s',
            color: canSubmit
              ? theme.palette.primary.main
              : isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.3)',
            '&:hover': {
              backgroundColor: canSubmit
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

// CRITICAL: Wrap in React.memo to prevent unnecessary re-renders
export default React.memo(ChatInput);