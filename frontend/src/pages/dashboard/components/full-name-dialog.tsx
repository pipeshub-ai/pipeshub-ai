import * as zod from 'zod';
import { useForm } from 'react-hook-form';
import { useState, useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';

import {
  Box,
  Stack,
  Alert,
  alpha,
  Dialog,
  Button,
  Snackbar,
  useTheme,
  TextField,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress
} from '@mui/material';

import axios from 'src/utils/axios';

import { Iconify } from 'src/components/iconify';

import { updateUser, getUserIdFromToken } from 'src/sections/accountdetails/utils';

import { useAuthContext } from 'src/auth/hooks';
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
  const [showRefreshMessage, setShowRefreshMessage] = useState<boolean>(false);
  const [refreshCountdown, setRefreshCountdown] = useState<number>(5);
  const [snackbarOpen, setSnackbarOpen] = useState<boolean>(false);
  const [snackbarMessage, setSnackbarMessage] = useState<string>('');
  const theme = useTheme();
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

  // Handle snackbar close
  const handleSnackbarClose = () => {
    setSnackbarOpen(false);
  };

  // Countdown effect for refresh message
  useEffect(() => {
    let timer: NodeJS.Timeout;

    if (showRefreshMessage && refreshCountdown > 0) {
      timer = setTimeout(() => {
        setRefreshCountdown((prev) => prev - 1);
      }, 1000);
    } else if (showRefreshMessage && refreshCountdown === 0) {
      // When countdown reaches 0, refresh the access token and reload
      refreshAndReload();
    }

    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [showRefreshMessage, refreshCountdown]);

  // Function to refresh the token and reload the page
  const refreshAndReload = async () => {
    try {
      const refreshToken = localStorage.getItem(STORAGE_KEY_REFRESH);

      if (!refreshToken) {
        // If no refresh token, show error and don't proceed
        setSnackbarMessage('Session information missing. Please log in again.');
        setSnackbarOpen(true);
        return;
      }

      // Get a new access token using the refresh token
      const response = await axios.post(
        `/api/v1/userAccount/refresh/token`,
        {},
        {
          headers: {
            Authorization: `Bearer ${refreshToken}`,
          },
        }
      );

      // Update the access token in localStorage
      if (response.data && response.data.accessToken) {
        localStorage.setItem(STORAGE_KEY, response.data.accessToken);
        // Keep the same refresh token

        // Update axios default headers
        axios.defaults.headers.common.Authorization = `Bearer ${response.data.accessToken}`;

        // Reload the page to refresh the application state
        window.location.reload();
      } else {
        throw new Error('Invalid response from server');
      }
    } catch (error) {
      console.error('Error refreshing token:', error);
      setSnackbarMessage('Failed to refresh your session. Please log in again.');
      setSnackbarOpen(true);
    }
  };

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

      // Notify success
      if (onSuccess) {
        onSuccess('Your full name has been updated successfully!');
      }

      // Close dialog
      onClose();

      // Show the refresh message with countdown
      setShowRefreshMessage(true);
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
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
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

      {/* Session refresh message dialog */}
      <Dialog
        open={showRefreshMessage}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
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
        <DialogTitle sx={{ pb: 1 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify
              icon="mdi:information-outline"
              width={24}
              height={24}
              sx={{ color: 'info.main' }}
            />
            <Typography variant="h6">Profile Updated Successfully</Typography>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Alert severity="success" variant="outlined" sx={{ mb: 2 }}>
            Your profile has been updated successfully!
          </Alert>
          <Typography variant="body2" sx={{ mt: 1, mb: 1 }}>
            To see your changes, your session needs to be refreshed. The page will automatically
            refresh in {refreshCountdown} seconds.
          </Typography>
          <Box sx={{ mt: 2, bgcolor: 'background.neutral', p: 2, borderRadius: 1 }}>
            <Typography variant="body2" color="text.secondary">
              <strong>Note:</strong> You will remain logged in. The application will simply refresh
              to update your profile information.
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            variant="contained"
            color="primary"
            onClick={() => {
              // Skip countdown and proceed immediately
              setRefreshCountdown(0);
            }}
            sx={{ borderRadius: 1 }}
          >
            Refresh Now
          </Button>
        </DialogActions>
      </Dialog>

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbarOpen}
        autoHideDuration={6000}
        onClose={handleSnackbarClose}
        message={snackbarMessage}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      />
    </>
  );
}
