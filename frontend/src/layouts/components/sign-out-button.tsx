import type { ButtonProps } from '@mui/material/Button';
import type { Theme, SxProps } from '@mui/material/styles';

import { Icon } from '@iconify/react';
import { useState, useCallback } from 'react';
import logoutIcon from '@iconify-icons/mdi/logout-variant';

import Button from '@mui/material/Button';

import { useRouter } from 'src/routes/hooks';

import { toast } from 'src/components/snackbar';

import { useAuthContext } from 'src/auth/hooks';
import { signOut as jwtSignOut } from 'src/auth/context/jwt/action';

// ----------------------------------------------------------------------

const signOut = jwtSignOut;

type Props = ButtonProps & {
  sx?: SxProps<Theme>;
  onClose?: () => void;
};

export function SignOutButton({ onClose, sx, ...other }: Props) {
  const router = useRouter();
  const { checkUserSession } = useAuthContext();
  const [isProcessing, setIsProcessing] = useState(false);

  const handleLogout = useCallback(async () => {
    try {
      setIsProcessing(true);
      await signOut();
      await checkUserSession?.();

      onClose?.();
      router.refresh();
    } catch (error) {
      toast.error('Unable to logout!');
    } finally {
      setIsProcessing(false);
    }
  }, [checkUserSession, onClose, router]);

  return (
    <Button
      fullWidth
      variant="contained"
      color="error"
      onClick={handleLogout}
      startIcon={<Icon icon={logoutIcon} width={20} height={20} />}
      disabled={isProcessing}
      sx={{
        height: 48,
        borderRadius: 1.5,
        fontWeight: 600,
        textTransform: 'none',
        ...sx,
      }}
      {...other}
    >
      Sign Out
    </Button>
  );
}
