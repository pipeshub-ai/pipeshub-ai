import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Icon } from '@iconify/react';
import sendIcon from '@iconify-icons/mdi/send';
import { Box, Paper, IconButton, useTheme, alpha } from '@mui/material';

type ChatInputProps = {
  onSubmit: (message: string) => Promise<void>;
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
  const [hasText, setHasText] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resizeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  useEffect(() => {
    if (inputRef.current) {
      const actuallyDisabled = inputRef.current.disabled;
      
      if (actuallyDisabled && !isLoading && !disabled && !isSubmitting) {
        inputRef.current.disabled = false;
      }
    }
  });

  // Auto-resize textarea with debounce
  const autoResizeTextarea = useCallback(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      const newHeight = Math.min(Math.max(inputRef.current.scrollHeight, 46), 180);
      inputRef.current.style.height = `${newHeight}px`;
    }
  }, []);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setLocalValue(value);
    setHasText(!!value.trim());

    // Debounce resize to prevent excessive calculations
    if (resizeTimeoutRef.current) {
      clearTimeout(resizeTimeoutRef.current);
    }
    resizeTimeoutRef.current = setTimeout(autoResizeTextarea, 50);
  }, [autoResizeTextarea]);

  const handleSubmit = useCallback(async () => {
    const trimmedValue = localValue.trim();
    if (!trimmedValue || isLoading || isSubmitting || disabled) {
      return;
    }

    setIsSubmitting(true);
    
    try {
      setLocalValue('');
      setHasText(false);
      
      // Reset textarea height
      if (inputRef.current) {
        setTimeout(() => {
          if (inputRef.current) {
            inputRef.current.style.height = '46px';
          }
        }, 50);
      }
      
      await onSubmit(trimmedValue);
    } catch (error) {
      console.error('Failed to send message:', error);
      // Restore message on error
      setLocalValue(trimmedValue);
      setHasText(true);
    } finally {
      setIsSubmitting(false);
      
      if (inputRef.current) {
        inputRef.current.focus();
      }
    }
  }, [localValue, isLoading, isSubmitting, disabled, onSubmit]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
      
      // Add scrollbar styles
      const styleId = 'chat-textarea-style';
      if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = isDark
          ? `
        textarea::-webkit-scrollbar {
          width: 6px;
          background-color: transparent;
        }
        textarea::-webkit-scrollbar-thumb {
          background-color: rgba(255, 255, 255, 0.2);
          border-radius: 10px;
        }
        textarea::-webkit-scrollbar-thumb:hover {
          background-color: rgba(255, 255, 255, 0.3);
        }
      `
          : `
        textarea::-webkit-scrollbar {
          width: 6px;
          background-color: transparent;
        }
        textarea::-webkit-scrollbar-thumb {
          background-color: rgba(0, 0, 0, 0.2);
          border-radius: 10px;
        }
        textarea::-webkit-scrollbar-thumb:hover {
          background-color: rgba(0, 0, 0, 0.3);
        }
      `;
        document.head.appendChild(style);
      }
    }

    return () => {
      if (resizeTimeoutRef.current) {
        clearTimeout(resizeTimeoutRef.current);
      }
    };
  }, [isDark]);

  // Only disable input if THIS conversation is actively loading/submitting
  const isInputDisabled = disabled || isSubmitting || isLoading;
  const canSubmit = hasText && !isInputDisabled;

  return (
    <>
      {/* Add keyframes for spinner animation */}
      <style>
        {`
          @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
          }
        `}
      </style>
      
      <Box sx={{ 
        p: 1, 
        width: { xs: '90%', sm: '80%', md: '70%' }, 
        mx: 'auto',
        position: 'relative',
      }}>
        <Paper
          elevation={0}
          sx={{
            display: 'flex',
            alignItems: 'center',
            p: '9px 14px',
            borderRadius: '10px',
            backgroundColor: isDark ? alpha('#131417', 0.5) : alpha('#f8f9fa', 0.6),
            border: '2px solid',
            borderColor: isDark ? alpha('#fff', 0.06) : alpha('#000', 0.04),
            boxShadow: isDark ? '0 4px 16px rgba(0, 0, 0, 0.2)' : '0 2px 10px rgba(0, 0, 0, 0.03)',
            transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
            position: 'relative',
            overflow: 'hidden',
            '&:hover': {
              borderColor: isDark ? alpha('#fff', 0.1) : alpha('#000', 0.07),
              boxShadow: isDark ? '0 6px 20px rgba(0, 0, 0, 0.25)' : '0 4px 14px rgba(0, 0, 0, 0.05)',
              backgroundColor: isDark ? alpha('#131417', 0.7) : alpha('#fff', 0.9),
            },
          }}
        >
          <textarea
            ref={inputRef}
            placeholder={placeholder}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            value={localValue}
            disabled={isInputDisabled}
            style={{
              width: '100%',
              border: 'none',
              outline: 'none',
              background: 'transparent',
              color: isDark ? alpha('#fff', 0.95).toString() : alpha('#000', 0.85).toString(),
              fontSize: '0.9rem',
              lineHeight: 1.5,
              minHeight: '46px',
              maxHeight: '180px',
              resize: 'none',
              padding: '8px 8px',
              fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
              margin: '0 6px 0 0',
              overflowY: 'auto',
              overflowX: 'hidden',
              transition: 'all 0.2s ease',
              letterSpacing: '0.01em',
              cursor: 'text',
              opacity: isInputDisabled ? 0.6 : 1,
            }}
          />

          <IconButton
            size="medium"
            onClick={handleSubmit}
            disabled={!canSubmit}
            sx={{
              backgroundColor: canSubmit 
                ? alpha(theme.palette.primary.main, 0.9) 
                : 'transparent',
              width: 34,
              height: 34,
              borderRadius: '8px',
              flexShrink: 0,
              alignSelf: 'center',
              transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
              color: canSubmit 
                ? '#fff' 
                : (isDark ? alpha('#fff', 0.4) : alpha('#000', 0.3)),
              opacity: canSubmit ? 1 : 0.6,
              border: canSubmit
                ? 'none'
                : `1px solid ${isDark ? alpha('#fff', 0.1) : alpha('#000', 0.05)}`,
              '&:hover': !isInputDisabled ? {
                backgroundColor: canSubmit
                  ? theme.palette.primary.main
                  : (isDark ? alpha('#fff', 0.05) : alpha('#000', 0.04)),
                transform: canSubmit ? 'translateY(-1px)' : 'none',
                boxShadow: canSubmit ? '0 4px 8px rgba(0, 0, 0, 0.15)' : 'none',
              } : {},
              '&:active': {
                transform: canSubmit ? 'translateY(0)' : 'none',
                boxShadow: 'none',
              },
              '&.Mui-disabled': {
                opacity: 0.6,
                backgroundColor: 'transparent',
                border: `1px solid ${isDark ? alpha('#fff', 0.05) : alpha('#000', 0.03)}`,
              },
            }}
          >
            {isInputDisabled ? (
              <Box
                sx={{
                  width: '16px',
                  height: '16px',
                  border: `2px solid ${isDark ? alpha('#fff', 0.2) : alpha('#000', 0.2)}`,
                  borderTop: `2px solid ${isDark ? alpha('#fff', 0.6) : alpha('#000', 0.6)}`,
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite',
                }}
              />
            ) : (
              <Icon
                icon={sendIcon}
                style={{
                  fontSize: '1rem',
                  transform: 'translateX(1px)',
                  transition: 'transform 0.2s ease',
                  filter: canSubmit ? 'drop-shadow(0 1px 1px rgba(0,0,0,0.1))' : 'none',
                }}
              />
            )}
          </IconButton>
        </Paper>
      </Box>
    </>
  );
};

export default ChatInput;