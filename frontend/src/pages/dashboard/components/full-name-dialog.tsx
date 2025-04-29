import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Typography,
  Stack,
  CircularProgress,
  Box,
  Alert,
} from '@mui/material';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as zod from 'zod';

import { useAuthContext } from 'src/auth/hooks';
import { getUserIdFromToken, updateUser } from 'src/sections/accountdetails/utils';
import { Iconify } from 'src/components/iconify';
import { STORAGE_KEY, STORAGE_KEY_REFRESH } from 'src/auth/context/jwt/constant';

// Schema for validation
const ProfileSchema = zod.object({
  fullName: zod.string().min(1, { message: 'Full Name is required' }),
});

interface FullNameDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: (message: string) => void;
  onError?: (message: string) => void;
}

export default function FullNameDialog({ open, onClose, onSuccess, onError }: FullNameDialogProps) {
  const { user } = useAuthContext();
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [showLogoutMessage, setShowLogoutMessage] = useState<boolean>(false);
  const [logoutCountdown, setLogoutCountdown] = useState<number>(5);

  // Form setup
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm({
    resolver: zodResolver(ProfileSchema),
    defaultValues: {
      fullName: '',
    },
  });

  // Update form when user data changes
  useEffect(() => {
    if (user?.fullName) {
      reset({ fullName: user.fullName });
    }
  }, [user, reset]);
  
  // Disable the beforeunload warning when submitting the form
  useEffect(() => {
    // Function to remove any existing beforeunload handlers
    const removeBeforeUnloadWarning = () => {
      window.onbeforeunload = null;
    };
    
    // If submitting, remove the warning
    if (isSubmitting) {
      removeBeforeUnloadWarning();
    }
    
    return () => {
      // Cleanup
      if (isSubmitting) {
        removeBeforeUnloadWarning();
      }
    };
  }, [isSubmitting]);

  // Countdown effect for logout message
  useEffect(() => {
    let timer: NodeJS.Timeout;
    
    if (showLogoutMessage && logoutCountdown > 0) {
      timer = setTimeout(() => {
        setLogoutCountdown(prev => prev - 1);
      }, 1000);
    } else if (showLogoutMessage && logoutCountdown === 0) {
      // When countdown reaches 0, perform the logout
      const refreshToken = localStorage.getItem(STORAGE_KEY_REFRESH);
      localStorage.removeItem(STORAGE_KEY);
      
      // Make sure refresh token is still available
      if (refreshToken) {
        localStorage.setItem(STORAGE_KEY_REFRESH, refreshToken);
      }
      
      // Finally reload the page
      window.location.reload();
    }
    
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [showLogoutMessage, logoutCountdown]);

  // Handle form submission
  const onSubmitFullName = async (data: { fullName: string }) => {
    try {
      setIsSubmitting(true);
      
      // Remove beforeunload event handler immediately
      window.onbeforeunload = null;
      
      const userId = await getUserIdFromToken();

      const userData = {
        fullName: data.fullName.trim(),
        email: user?.email || '', // Include email from auth context
      };

      // Update user with both fullName and email
      await updateUser(userId, userData);
      
      // Notify success before removing token
      if (onSuccess) {
        onSuccess('Your full name has been updated successfully!');
      }
      
      // Close dialog
      onClose();
      
      // Show the logout message with countdown
      setShowLogoutMessage(true);
      
    } catch (error) {
      console.error('Error updating full name:', error);
      if (onError) {
        onError('Failed to update your full name. Please try again.');
      }
      // Reset submitting state if there's an error
      setIsSubmitting(false);
    }
  };

  return (
    <>
      <Dialog
        open={open}
        onClose={(event, reason) => {
          // Prevent closing by backdrop click or escape key
          if (reason === 'backdropClick' || reason === 'escapeKeyDown') {
            return;
          }
          onClose();
        }}
        PaperProps={{
          sx: {
            borderRadius: 2,
            padding: 1,
            maxWidth: 500,
          },
        }}
        disableEscapeKeyDown
      >
        <form onSubmit={handleSubmit(onSubmitFullName)}>
          <DialogTitle sx={{ pb: 1 }}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Iconify
                icon="mdi:account-edit"
                width={24}
                height={24}
                sx={{ color: 'primary.main' }}
              />
              <Typography variant="h6">Complete Your Profile</Typography>
            </Stack>
          </DialogTitle>
          <DialogContent>
            <Typography variant="body2" sx={{ mt: 1, mb: 3 }}>
              Please enter your full name to continue using the application.
            </Typography>
            <TextField
              autoFocus
              fullWidth
              label="Full Name"
              {...register('fullName')}
              error={!!errors.fullName}
              helperText={errors.fullName?.message}
              disabled={isSubmitting}
              sx={{ mt: 1 }}
            />
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button
              type="submit"
              variant="contained"
              disabled={isSubmitting}
              startIcon={isSubmitting ? <CircularProgress size={20} /> : null}
              sx={{ borderRadius: 1 }}
            >
              {isSubmitting ? 'Saving...' : 'Save'}
            </Button>
          </DialogActions>
        </form>
      </Dialog>

      {/* Logout message dialog */}
      <Dialog
        open={showLogoutMessage}
        PaperProps={{
          sx: {
            borderRadius: 2,
            padding: 1,
            maxWidth: 500,
          },
        }}
        disableEscapeKeyDown
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify
              icon="mdi:information-outline"
              width={24}
              height={24}
              sx={{ color: 'info.main' }}
            />
            <Typography variant="h6">Session Update Required</Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Alert severity="info" variant="outlined" sx={{ mb: 2 }}>
            Your profile has been updated successfully!
          </Alert>
          <Typography variant="body2" sx={{ mt: 1, mb: 1 }}>
            To apply these changes, your session needs to be refreshed. 
            You will be automatically redirected in {logoutCountdown} seconds.
          </Typography>
          <Box sx={{ mt: 2, bgcolor: 'background.neutral', p: 2, borderRadius: 1 }}>
            <Typography variant="body2" color="text.secondary">
              <strong>Note:</strong> Your changes are saved and you won&apos;t be logged out.
              The application will simply refresh to update your session.
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            variant="contained"
            color="primary"
            onClick={() => {
              // Skip countdown and proceed immediately
              setLogoutCountdown(0);
            }}
            sx={{ borderRadius: 1 }}
          >
            Refresh Now
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}