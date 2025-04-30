import { z as zod } from 'zod';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import lockIcon from '@iconify-icons/mdi/lock-outline';
import visibilityIcon from '@iconify-icons/mdi/eye-outline';
import visibilityOffIcon from '@iconify-icons/mdi/eye-off-outline';
import React, { useState, useEffect, useCallback } from 'react';

import { LoadingButton } from '@mui/lab';
import {
  Box,
  Grid,
  Alert,
  Paper,
  alpha,
  Button,
  Dialog,
  Divider,
  Snackbar,
  useTheme,
  Container,
  Typography,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  InputAdornment,
  IconButton,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';
import { Form, Field } from 'src/components/hook-form';

import {
  logout, // Import the logout function
  updateUser,
  getUserById,
  deleteUserLogo,
  uploadUserLogo,
  changePassword,
  getUserIdFromToken,
} from './utils';

import type { SnackbarState } from './types/organization-data';

const ProfileSchema = zod.object({
  fullName: zod.string().min(1, { message: 'Full Name is required' }),
  firstName: zod.string().optional(),
  lastName: zod.string().optional(),
  email: zod.string().email({ message: 'Invalid email' }).min(1, { message: 'Email is required' }),
  designation: zod.string().optional(),
});

const PasswordSchema = zod
  .object({
    currentPassword: zod.string().min(1, { message: 'Current password is required' }),
    newPassword: zod
      .string()
      .min(8, { message: 'Password must be at least 8 characters long' })
      .regex(/[a-z]/, { message: 'Password must contain at least one lowercase letter' })
      .regex(/[A-Z]/, { message: 'Password must contain at least one uppercase letter' })
      .regex(/[0-9]/, { message: 'Password must contain at least one number' })
      .regex(/[^a-zA-Z0-9]/, { message: 'Password must contain at least one symbol' }),
    repeatNewPassword: zod.string().min(1, { message: 'Please repeat your new password' }),
  })
  .refine((data) => data.newPassword === data.repeatNewPassword, {
    message: "Passwords don't match",
    path: ['repeatNewPassword'],
  });

type ProfileFormData = zod.infer<typeof ProfileSchema>;
type PasswordFormData = zod.infer<typeof PasswordSchema>;

export default function PersonalProfile() {
  const theme = useTheme();
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [logo, setLogo] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: undefined,
  });
  const [isChangePasswordOpen, setIsChangePasswordOpen] = useState<boolean>(false);
  const [saveChanges, setSaveChanges] = useState<boolean>(false);
  const [currentEmail, setCurrentEmail] = useState<string>(''); // Store the current email
  const { isAdmin } = useAdmin();
  const [passwordVisibility, setPasswordVisibility] = useState({
    current: false,
    new: false,
  });

  const methods = useForm<ProfileFormData>({
    resolver: zodResolver(ProfileSchema),
    mode: 'onChange',
  });

  const passwordMethods = useForm<PasswordFormData>({
    resolver: zodResolver(PasswordSchema),
    mode: 'onChange',
  });

  const {
    handleSubmit,
    reset,
    formState: { isValid, isDirty },
  } = methods;

  const handleCloseSnackbar = useCallback(() => {
    setSnackbar({ open: false, message: '', severity: undefined });
  }, []);

  useEffect(() => {
    const fetchUserData = async () => {
      try {
        setLoading(true);
        const userId = await getUserIdFromToken();
        const userData = await getUserById(userId);
        const { fullName, firstName, email, lastName, designation } = userData;

        // Store the current email to check if it changes later
        setCurrentEmail(email);

        reset({
          fullName,
          firstName,
          email,
          lastName,
          designation,
        });

        setLoading(false);
      } catch (err) {
        setError('Failed to fetch organization data');
        // setSnackbar({
        //   open: true,
        //   message: err.errorMessage,
        //   severity: 'error',
        // });
        setLoading(false);
      }
    };

    fetchUserData();
  }, [reset]);

  // useEffect(() => {
  //   const fetchLogo = async (): Promise<void> => {
  //     try {
  //       const userId = await getUserIdFromToken();
  //       const logoUrl = await getUserLogo(userId);
  //       setLogo(logoUrl);
  //     } catch (err) {
  //       setError('Failed to fetch User logo');
  //       setSnackbar({ open: true, message: err.errorMessage, severity: 'error' });
  //     }
  //   };

  //   fetchLogo();
  // }, []);

  const onSubmit = async (data: ProfileFormData): Promise<void> => {
    try {
      setSaveChanges(true);
      const userId = await getUserIdFromToken();
      await updateUser(userId, data);

      // Check if email was changed
      const emailChanged = data.email !== currentEmail;

      setSnackbar({
        open: true,
        message: 'Profile updated successfully',
        severity: 'success',
      });

      if (emailChanged) {
        // Show a message about logout
        setSnackbar({
          open: true,
          message: 'Email updated. You will be logged out.',
          severity: 'info',
        });

        // Add a slight delay to show the message before logout
        setTimeout(() => {
          logout(); // Call the logout function
        }, 2000);
      }

      setLoading(false);
    } catch (err) {
      setError('Failed to update user');
      // setSnackbar({ open: true, message: err.errorMessage, severity: 'error' });
      setLoading(false);
    } finally {
      setSaveChanges(false);
    }
  };

  const handleDelete = async (): Promise<void> => {
    try {
      setDeleting(true);
      const userId = await getUserIdFromToken();
      await deleteUserLogo(userId);
      setSnackbar({ open: true, message: 'Photo removed successfully', severity: 'success' });
      setDeleting(false);
      setLogo(null);
    } catch (err) {
      // setSnackbar({ open: true, message: err.errorMessage, severity: 'error' });
      setDeleting(false);
    }
  };

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = event.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('logo', file);

    try {
      setUploading(true);
      const userId = await getUserIdFromToken();
      await uploadUserLogo(userId, formData);
      setSnackbar({ open: true, message: 'Photo updated successfully', severity: 'success' });
      setUploading(false);
      setLogo(URL.createObjectURL(file));
    } catch (err) {
      setError('Failed to upload photo');
      // setSnackbar({ open: true, message: 'Failed to upload photo', severity: 'error' });
      setUploading(false);
    }
  };

  const handleChangePassword = async (data: PasswordFormData): Promise<void> => {
    try {
      await changePassword({
        currentPassword: data.currentPassword,
        newPassword: data.newPassword,
      });
      setSnackbar({
        open: true,
        message: 'Password changed successfully',
        severity: 'success',
      });
      setIsChangePasswordOpen(false);
      passwordMethods.reset();
    } catch (err) {
      // setSnackbar({ open: true, message: err.errorMessage, severity: 'error' });
    }
  };

  const handleClosePasswordDialog = () => {
    setIsChangePasswordOpen(false);
    setPasswordVisibility({ current: false, new: false }); // Reset all visibilities
    passwordMethods.reset(); // Clear all form fields
  };

  if (loading) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '80vh',
        }}
      >
        <CircularProgress size={36} />
      </Box>
    );
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Paper
        elevation={1}
        sx={{
          borderRadius: 2,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <Box
          sx={{
            py: 2.5,
            px: { xs: 3, md: 4 },
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
            bgcolor: alpha(theme.palette.primary.main, 0.02),
          }}
        >
          <Typography variant="h6" fontWeight={500} fontSize="1.25rem">
            Personal Profile
          </Typography>

          <Button
            variant="outlined"
            size="medium"
            onClick={() => setIsChangePasswordOpen(true)}
            startIcon={<Iconify icon={lockIcon} width={18} height={18} />}
            sx={{
              textTransform: 'none',
              fontWeight: 500,
              borderRadius: 2,
              px: 2,
              py: 0.8,
            }}
          >
            Change Password
          </Button>
        </Box>

        {/* Content */}
        <Box sx={{ p: { xs: 3, md: 4 } }}>
          <Grid container spacing={{ xs: 3, md: 5 }}>
            {/* Form Section */}
            <Grid item xs={12} md={8}>
              <Form
                methods={methods}
                onSubmit={handleSubmit(onSubmit)}
                {...({ noValidate: true } as any)}
              >
                <Grid container spacing={3}>
                  <Grid item xs={12} sm={6}>
                    <Field.Text
                      name="firstName"
                      label="First name"
                      fullWidth
                      variant="outlined"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          height: 50,
                        },
                      }}
                    />
                  </Grid>
                  <Grid item xs={12} sm={6}>
                    <Field.Text
                      name="lastName"
                      label="Last name"
                      fullWidth
                      variant="outlined"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          height: 50,
                        },
                      }}
                    />
                  </Grid>
                  <Grid item xs={12}>
                    <Field.Text
                      name="fullName"
                      label="Full name"
                      fullWidth
                      variant="outlined"
                      required
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          height: 50,
                        },
                      }}
                    />
                  </Grid>
                  <Grid item xs={12}>
                    <Field.Text
                      name="designation"
                      label="Designation"
                      fullWidth
                      variant="outlined"
                      placeholder="e.g. Software Engineer"
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          height: 50,
                        },
                      }}
                    />
                  </Grid>
                  <Grid item xs={12}>
                    <Field.Text
                      name="email"
                      label="Email address"
                      fullWidth
                      variant="outlined"
                      required
                      sx={{
                        '& .MuiOutlinedInput-root': {
                          height: 50,
                        },
                      }}
                    />
                    {methods.getValues('email') !== currentEmail && (
                      <Alert severity="warning" sx={{ mt: 1, borderRadius: 1 }}>
                        Changing your email will log you out of the system
                      </Alert>
                    )}
                  </Grid>
                  <Grid item xs={12}>
                    <Divider sx={{ mt: 1, mb: 2 }} />
                  </Grid>
                  <Grid item xs={12}>
                    <LoadingButton
                      color="primary"
                      type="submit"
                      variant="contained"
                      loading={saveChanges}
                      loadingIndicator="Saving..."
                      disabled={!isValid || !isDirty}
                      sx={{
                        height: 40,
                        px: 2,
                        borderRadius: 2,
                        textTransform: 'none',
                        fontWeight: 500,
                        fontSize: '0.93rem',
                        boxShadow: theme.shadows[1],
                        '&:hover': {
                          boxShadow: theme.shadows[2],
                        },
                      }}
                    >
                      Save changes
                    </LoadingButton>
                  </Grid>
                </Grid>
              </Form>
            </Grid>
          </Grid>
        </Box>
      </Paper>

      {/* Password Dialog */}
      <Dialog
        open={isChangePasswordOpen}
        onClose={handleClosePasswordDialog}
        PaperProps={{
          sx: {
            borderRadius: 1,
            maxWidth: 400,
          },
        }}
      >
        <DialogTitle sx={{ pb: 1, pt: 2 }}>
          <Typography variant="h6" fontWeight={500}>
            Change Password
          </Typography>
        </DialogTitle>
        <Form
          methods={passwordMethods}
          onSubmit={passwordMethods.handleSubmit(handleChangePassword)}
        >
          <DialogContent sx={{ pt: 1, pb: 1 }}>
            {/* Current Password Field with View Toggle */}
            <Field.Text
              name="currentPassword"
              label="Current password"
              type={passwordVisibility.current ? 'text' : 'password'}
              fullWidth
              margin="normal"
              variant="outlined"
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      aria-label="toggle current password visibility"
                      onClick={() =>
                        setPasswordVisibility({
                          ...passwordVisibility,
                          current: !passwordVisibility.current,
                        })
                      }
                      edge="end"
                    >
                      <Iconify
                        icon={passwordVisibility.current ? visibilityIcon : visibilityOffIcon}
                        width={20}
                        height={20}
                      />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  height: 56,
                },
              }}
            />

            <Box sx={{ position: 'relative', mt: 2 }}>
              <Typography variant="subtitle2" fontWeight={500} color="text.primary" sx={{ mb: 1 }}>
                New Password
              </Typography>

              {/* Show/Hide toggle for both new password fields */}
              <Box sx={{ position: 'absolute', top: 0, right: 0 }}>
                <Button
                  startIcon={
                    <Iconify
                      icon={passwordVisibility.new ? visibilityIcon : visibilityOffIcon}
                      width={18}
                      height={18}
                    />
                  }
                  onClick={() =>
                    setPasswordVisibility({
                      ...passwordVisibility,
                      new: !passwordVisibility.new,
                    })
                  }
                  size="small"
                  sx={{
                    textTransform: 'none',
                    color: 'text.secondary',
                    fontWeight: 400,
                    fontSize: '0.75rem',
                  }}
                >
                  {passwordVisibility.new ? 'Hide' : 'Show'}
                </Button>
              </Box>

              {/* New Password Field */}
              <Field.Text
                name="newPassword"
                label="New password"
                type={passwordVisibility.new ? 'text' : 'password'}
                fullWidth
                margin="normal"
                variant="outlined"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    height: 56,
                  },
                  mt: 0,
                }}
              />

              {/* Password requirements */}
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  display: 'block',
                  mt: 0.5,
                  mb: 1.5,
                  fontSize: '0.7rem',
                  lineHeight: 1.4,
                }}
              >
                Password must have at least 8 characters with lowercase, uppercase, number and
                symbol
              </Typography>

              {/* Confirm New Password Field - uses same visibility state */}
              <Field.Text
                name="repeatNewPassword"
                label="Confirm new password"
                type={passwordVisibility.new ? 'text' : 'password'}
                fullWidth
                margin="normal"
                variant="outlined"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    height: 56,
                  },
                }}
              />
            </Box>
          </DialogContent>
          <DialogActions sx={{ px: 2.5, pb: 2, pt: 1 }}>
            <Button
              onClick={handleClosePasswordDialog}
              size="small"
              sx={{
                textTransform: 'none',
                color: 'text.secondary',
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="contained"
              color="primary"
              size="small"
              disabled={!passwordMethods.formState.isValid}
              sx={{
                textTransform: 'none',
                borderRadius: 0.75,
                px: 2,
                height: 36,
                fontWeight: 500,
                boxShadow: 'none',
                '&:hover': {
                  boxShadow: 'none',
                },
              }}
            >
              Update password
            </Button>
          </DialogActions>
        </Form>
      </Dialog>

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={handleCloseSnackbar}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        sx={{ mt: 6 }}
      >
        <Alert
          onClose={handleCloseSnackbar}
          severity={snackbar.severity}
          variant="filled"
          sx={{
            width: '100%',
            borderRadius: 0.75,
            boxShadow: theme.shadows[3],
            '& .MuiAlert-icon': {
              fontSize: '1.2rem',
            },
          }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Container>
  );
}
