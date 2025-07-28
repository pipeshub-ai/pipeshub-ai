import type { ButtonProps } from '@mui/material/Button';
import type { Theme, SxProps } from '@mui/material/styles';

import { Icon } from '@iconify/react';
import { useState, useCallback } from 'react';
import logoutIcon from '@iconify-icons/mdi/logout-variant';

import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import { alpha, styled, useTheme } from '@mui/material/styles';

import { useRouter } from 'src/routes/hooks';

import { toast } from 'src/components/snackbar';

import { useAuthContext } from 'src/auth/hooks';
import { signOut as jwtSignOut } from 'src/auth/context/jwt/action';

// ----------------------------------------------------------------------

const signOut = jwtSignOut;

// Styled components to improve code organization and readability
const StyledSignOutButton = styled(Button, {
  shouldForwardProp: (prop) => prop !== 'isHovered',
})<{ isHovered: boolean }>(({ theme, isHovered }) => {
  const isDark = theme.palette.mode === 'dark';
  
  return {
    position: 'relative',
    height: 48,
    color: isDark 
      ? alpha(theme.palette.error.main, 0.9) 
      : theme.palette.error.main,
    backgroundColor: isDark 
      ? alpha(theme.palette.error.main, 0.08)
      : alpha(theme.palette.error.main, 0.06),
    borderRadius: 2,
    fontWeight: 600,
    fontSize: '0.875rem',
    letterSpacing: '0.02em',
    textTransform: 'none',
    border: isDark 
      ? `1px solid ${alpha(theme.palette.error.main, isHovered ? 0.3 : 0.15)}`
      : 'none',
    boxShadow: isDark && isHovered 
      ? `0 0 15px ${alpha(theme.palette.error.main, 0.15)}, inset 0 0 4px ${alpha(theme.palette.error.main, 0.1)}`
      : 'none',
    overflow: 'hidden',
    '&:before': {
      content: '""',
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      height: isDark ? 1 : 0,
      background: isDark 
        ? `linear-gradient(90deg, transparent, ${alpha(theme.palette.error.main, 0.2)}, transparent)`
        : 'none',
      opacity: isHovered ? 1 : 0,
      transition: 'opacity 0.6s ease',
    },
    '&:hover': {
      backgroundColor: isDark 
        ? alpha(theme.palette.error.main, 0.12)
        : alpha(theme.palette.error.main, 0.12),
      transform: 'translateY(-1px)',
    },
    '&:active': {
      backgroundColor: isDark 
        ? alpha(theme.palette.error.main, 0.18)
        : alpha(theme.palette.error.main, 0.18),
      transform: 'translateY(0)',
    },
    transition: theme.transitions.create(
      ['background-color', 'box-shadow', 'transform', 'border-color'],
      { duration: 200 }
    ),
  };
});

const IconContainer = styled(Box, {
  shouldForwardProp: (prop) => prop !== 'isHovered',
})<{ isHovered: boolean }>(({ theme, isHovered }) => {
  const isDark = theme.palette.mode === 'dark';
  
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.3s ease',
    transform: isHovered ? 'translateX(-3px)' : 'translateX(0)',
    color: isDark 
      ? alpha(theme.palette.error.main, isDark ? 0.9 : 1)
      : theme.palette.error.main,
  };
});

const AnimatedText = styled('span')<{ isHovered: boolean }>(({ isHovered }) => ({
  position: 'relative',
  display: 'inline-block',
  transition: 'all 0.3s ease',
  transform: isHovered ? 'translateX(-3px)' : 'translateX(0)',
}));

type Props = ButtonProps & {
  sx?: SxProps<Theme>;
  onClose?: () => void;
};

export function SignOutButton({ onClose, ...other }: Props) {
  const router = useRouter();
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { checkUserSession } = useAuthContext();
  const [isHovered, setIsHovered] = useState(false);

  const handleLogout = useCallback(async () => {
    try {
      await signOut();
      await checkUserSession?.();

      onClose?.();
      router.refresh();
    } catch (error) {
      toast.error('Unable to logout!');
    }
  }, [checkUserSession, onClose, router]);

  return (
    <StyledSignOutButton
      fullWidth
      onClick={handleLogout}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      isHovered={isHovered}
      sx={other.sx}
      {...other}
    >
      <Box 
        sx={{ 
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
        }}
      >
        <IconContainer isHovered={isHovered}>
          <Icon 
            icon={logoutIcon} 
            width={20} 
            height={20}
            style={{
              filter: isDark && isHovered 
                ? `drop-shadow(0 0 3px ${alpha(theme.palette.error.main, 0.6)})`
                : 'none',
            }}
          />
        </IconContainer>
        <AnimatedText isHovered={isHovered}>
          Sign Out
        </AnimatedText>
      </Box>
    </StyledSignOutButton>
  );
}