// Updated scrollableContainerStyle.js with dark mode support
import { useTheme } from '@mui/material/styles';

const scrollbarStyleCont = {
  '&::-webkit-scrollbar': {
    width: '14px',
    height: '6px',
    display: 'block',
  },
  '&::-webkit-scrollbar-track': {
    background: '#f5f7fa',
  },
  '&::-webkit-scrollbar-thumb': {
    background: 'rgba(209, 213, 219, 0.8)',
    borderRadius: '100px',

    backgroundClip: 'padding-box',
    minHeight: '40px',
    '&:hover': {
      background: 'rgba(156, 163, 175, 0.4)',
    },
  },
  '&::-webkit-scrollbar-corner': {
    background: 'transparent',
  },
};
export const scrollableContainerStyle = {
  ...scrollbarStyleCont,
  overflowY: 'auto',
  position: 'relative',
};

// This function returns the scrollbar style based on the current theme
export const getScrollbarStyle = (theme) => {
  const isDark = theme.palette.mode === 'dark';

  return {
    '&::-webkit-scrollbar': {
      width: '14px',
      height: '6px',
      display: 'block',
    },
    '&::-webkit-scrollbar-track': {
      background: isDark ? '#1a1a1a' : '#f5f7fa',
    },
    '&::-webkit-scrollbar-thumb': {
      background: isDark ? 'rgba(255, 255, 255, 0.16)' : 'rgba(209, 213, 219, 0.8)',
      borderRadius: '100px',
      border: isDark ? '4px solid #1a1a1a' : '4px solid #f5f7fa',
      backgroundClip: 'padding-box',
      minHeight: '40px',
      '&:hover': {
        background: isDark ? 'rgba(255, 255, 255, 0.24)' : 'rgba(156, 163, 175, 0.4)',
      },
    },
    '&::-webkit-scrollbar-corner': {
      background: 'transparent',
    },
  };
};

// Hook to get scrollable container style with current theme
export const useScrollableContainerStyle = () => {
  const theme = useTheme();
  const scrollbarStyle = getScrollbarStyle(theme);

  return {
    ...scrollbarStyle,
    overflowY: 'auto',
    position: 'relative',
  };
};

// For direct usage when theme is available
export const createScrollableContainerStyle = (theme) => {
  const scrollbarStyle = getScrollbarStyle(theme);

  return {
    ...scrollbarStyle,
    overflowY: 'auto',
    position: 'relative',
  };
};

export default createScrollableContainerStyle;
