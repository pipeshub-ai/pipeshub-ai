import { z as zod } from 'zod';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import lockIcon from '@iconify-icons/mdi/lock-outline';
import visibilityIcon from '@iconify-icons/mdi/eye-outline';
import infoIcon from '@iconify-icons/solar/info-circle-bold';
import React, { useState, useEffect, useCallback } from 'react';
import visibilityOffIcon from '@iconify-icons/mdi/eye-off-outline';

import { LoadingButton } from '@mui/lab';
import {
  Box,
  Grid,
  Alert,
  Paper,
  alpha,
  Button,
  Dialog,
  Switch,
  Divider,
  Snackbar,
  useTheme,
  Container,
  Typography,
  IconButton,
  DialogTitle,
  DialogContent,
  DialogActions,
  InputAdornment,
  CircularProgress,
  FormControlLabel,
} from '@mui/material';

import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';
import { Form, Field } from 'src/components/hook-form';

import { useAuthContext } from 'src/auth/hooks';

import {
  logout,
  updateUser,
  getUserById,
  deleteUserLogo,
  uploadUserLogo,
  changePassword,
  getUserIdFromToken,
  getDataCollectionConsent,
  updateDataCollectionConsent,
} from './utils';

import type { SnackbarState } from './types/organization-data';

const ProfileSchema = zod.object({
  fullName: zod.string().min(1, { message: 'Full Name is required' }),
  firstName: zod.string().optional(),
  lastName: zod.string().optional(),
  email: zod.string().email({ message: 'Invalid email' }).min(1, { message: 'Email is required' }),
  designation: zod.string().optional(),
  dataCollectionConsent: zod.boolean().optional(),
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
  const [currentEmail, setCurrentEmail] = useState<string>('');
  const [isEmailConfirmOpen, setIsEmailConfirmOpen] = useState<boolean>(false);
  const [pendingFormData, setPendingFormData] = useState<ProfileFormData | null>(null);
  const [consentLoading, setConsentLoading] = useState<boolean>(false);
  const [showMorePrivacy, setShowMorePrivacy] = useState<boolean>(false);
  const { isAdmin } = useAdmin();
  const { user } = useAuthContext();
  const accountType = user?.accountType || 'individual';
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
    watch,
    control,
    setValue,
    formState: { isValid, isDirty },
  } = methods;

  // Watch for email changes
  const watchEmail = watch('email');

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

        // Only fetch data collection consent for individual accounts
        let consentStatus = false;
        if (accountType === 'individual') {
          consentStatus = Boolean(await getDataCollectionConsent());
        }

        reset({
          fullName,
          firstName,
          email,
          lastName,
          designation,
          dataCollectionConsent: consentStatus,
        });

        setLoading(false);
      } catch (err) {
        setError('Failed to fetch organization data');
        setLoading(false);
      }
    };

    fetchUserData();
  }, [reset, accountType]);

  const handleConsentChange = async (checked: boolean): Promise<void> => {
    try {
      setConsentLoading(true);
      await updateDataCollectionConsent(checked);
      setValue('dataCollectionConsent', checked, { shouldDirty: false });
      setSnackbar({
        open: true,
        message: `Data collection ${checked ? 'enabled' : 'disabled'} successfully!`,
        severity: 'success',
      });
    } catch (err) {
      setError(`Failed to ${checked ? 'enable' : 'disable'} data collection consent`);
      setSnackbar({
        open: true,
        message: `Failed to update data collection settings`,
        severity: 'error',
      });
      // Reset the switch to its previous state
      setValue('dataCollectionConsent', !checked, { shouldDirty: false });
    } finally {
      setConsentLoading(false);
    }
  };

  const handleFormSubmit = (data: ProfileFormData): void => {
    const emailChanged = data.email !== currentEmail;

    if (emailChanged) {
      // Store form data and show confirmation dialog
      setPendingFormData(data);
      setIsEmailConfirmOpen(true);
    } else {
      // Process the form directly if email hasn't changed
      processFormSubmission(data);
    }
  };

  const processFormSubmission = async (data: ProfileFormData): Promise<void> => {
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
      setLoading(false);
    } finally {
      setSaveChanges(false);
      setPendingFormData(null);
    }
  };

  const handleConfirmEmailChange = () => {
    if (pendingFormData) {
      processFormSubmission(pendingFormData);
    }
    setIsEmailConfirmOpen(false);
  };

  const handleCancelEmailChange = () => {
    setIsEmailConfirmOpen(false);
    setPendingFormData(null);
    // Optionally reset the email field back to current email
    methods.setValue('email', currentEmail);
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
      // Error handling
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
          minHeight: '80vh',
          mx: 'auto',
          my: 'auto',
        }}
      >
        <Paper
          sx={{
            p: 4,
            borderRadius: 2,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            maxWidth: '300px',
            width: '100%',
          }}
        >
          <CircularProgress size={40} thickness={4} color="primary" />
          <Box sx={{ mt: 2, display: 'flex', alignItems: 'center' }}>
            <Typography
              variant="body1"
              sx={{
                ml: 1,
                color: 'text.secondary',
                fontWeight: 500,
              }}
            >
              Loading your profile...
            </Typography>
          </Box>
        </Paper>
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
          backgroundColor:
            theme.palette.mode === 'dark'
              ? alpha(theme.palette.background.paper, 0.6)
              : theme.palette.background.paper,
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
              fontWeight: 500,
              textTransform: 'none',
              boxShadow: 'none',
              padding: '6px 14px',
              fontSize: '0.85rem',
              transition: 'all 0.2s',
              letterSpacing: '0.01em',
              '&:hover': {
                boxShadow:
                  theme.palette.mode === 'dark'
                    ? '0 5px 15px rgba(0, 0, 0, 0.3)'
                    : '0 5px 15px rgba(0, 0, 0, 0.05)',
                transform: 'translateY(-1px)',
              },
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
                onSubmit={handleSubmit(handleFormSubmit)}
                {...({ noValidate: true } as any)}
              >
                <Grid container spacing={3}>
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
                    {watchEmail !== currentEmail && (
                      <Alert severity="warning" sx={{ mt: 1, borderRadius: 1 }}>
                        Changing your email will log you out of the system
                      </Alert>
                    )}
                  </Grid>

                  {/* Data Collection Consent Section - Only for individual accounts */}
                  {accountType === 'individual' && (
                    <Grid item xs={12}>
                      <Paper
                        elevation={4}
                        sx={{
                          p: 3,
                          borderRadius: 2,
                          border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                          mb: 3,
                        }}
                      >
                        <Box
                          sx={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            mb: 2,
                          }}
                        >
                          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                            Data Collection Settings
                          </Typography>

                          <Controller
                            name="dataCollectionConsent"
                            control={control}
                            render={({ field, fieldState }) => (
                              <Box sx={{ display: 'flex', alignItems: 'center' }}>
                                <FormControlLabel
                                  control={
                                    <Switch
                                      checked={field.value === true}
                                      onChange={(e) => {
                                        const { checked } = e.target;
                                        handleConsentChange(checked);
                                      }}
                                      disabled={consentLoading}
                                      color="primary"
                                    />
                                  }
                                  label={
                                    <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                      {field.value === true ? 'Enabled' : 'Disabled'}
                                    </Typography>
                                  }
                                />
                                {consentLoading && (
                                  <CircularProgress size={20} thickness={5} sx={{ ml: 1 }} />
                                )}
                              </Box>
                            )}
                          />
                        </Box>

                        <Box>
                          <Typography
                            variant="body2"
                            sx={{ color: theme.palette.text.secondary, mb: 1 }}
                          >
                            PipesHub collects and processes personal information for a variety of
                            business purposes.
                          </Typography>

                          {showMorePrivacy && (
                            <>
                              <Box component="ul" sx={{ pl: 2, m: 0, listStyleType: 'square' }}>
                                <Typography
                                  component="li"
                                  variant="body2"
                                  sx={{ color: theme.palette.text.secondary }}
                                >
                                  To provide customer service and support for our products
                                </Typography>
                                <Typography
                                  component="li"
                                  variant="body2"
                                  sx={{ color: theme.palette.text.secondary }}
                                >
                                  To send marketing communications
                                </Typography>
                                <Typography
                                  component="li"
                                  variant="body2"
                                  sx={{ color: theme.palette.text.secondary }}
                                >
                                  To manage your subscription to newsletters or other updates
                                </Typography>
                                <Typography
                                  component="li"
                                  variant="body2"
                                  sx={{ color: theme.palette.text.secondary }}
                                >
                                  For security and fraud prevention purposes
                                </Typography>
                                <Typography
                                  component="li"
                                  variant="body2"
                                  sx={{ color: theme.palette.text.secondary }}
                                >
                                  To personalize your user experience
                                </Typography>
                                <Typography
                                  component="li"
                                  variant="body2"
                                  sx={{ color: theme.palette.text.secondary }}
                                >
                                  To enhance and improve our products and services
                                </Typography>
                              </Box>

                              <Box
                                sx={{
                                  mt: 2.5,
                                  p: 1.5,
                                  borderRadius: 1,
                                  bgcolor: alpha(theme.palette.info.main, 0.08),
                                  border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: 1.5,
                                }}
                              >
                                <Box sx={{ color: theme.palette.info.main, flexShrink: 0 }}>
                                  <Iconify icon={infoIcon} width={20} height={20} />
                                </Box>
                                <Typography
                                  variant="body2"
                                  color="info.dark"
                                  sx={{ fontWeight: 500 }}
                                >
                                  Disclaimer: We do not sell, trade, or otherwise transfer your
                                  personal information to third parties
                                </Typography>
                              </Box>
                            </>
                          )}

                          <Button
                            onClick={() => setShowMorePrivacy(!showMorePrivacy)}
                            sx={{
                              mt: 1,
                              textTransform: 'none',
                              color: theme.palette.primary.main,
                              fontWeight: 500,
                              p: 0,
                              '&:hover': {
                                backgroundColor: 'transparent',
                                textDecoration: 'underline',
                              },
                            }}
                            disableRipple
                          >
                            {showMorePrivacy ? 'Show Less' : 'Show More'}
                          </Button>
                        </Box>
                      </Paper>
                    </Grid>
                  )}

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

      {/* Email Change Confirmation Dialog */}
      <Dialog
        open={isEmailConfirmOpen}
        onClose={handleCancelEmailChange}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
        PaperProps={{
          sx: {
            borderRadius: 1,
            maxWidth: 450,
          },
        }}
      >
        <DialogTitle sx={{ pb: 1, pt: 2.5 }}>
          <Typography variant="h6" fontWeight={600}>
            Confirm Email Change
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ pt: 1, pb: 1 }}>
          <Alert severity="warning" sx={{ mb: 2, borderRadius: 1 }}>
            Changing your email will log you out of the system
          </Alert>

          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Your email will be changed from:
          </Typography>

          <Box
            sx={{
              bgcolor: alpha(theme.palette.background.default, 0.5),
              p: 1.5,
              borderRadius: 1,
              mb: 2,
              fontFamily: "'SF Mono', 'Roboto Mono', monospace",
            }}
          >
            <Typography variant="body2" fontWeight={500} color="text.primary" gutterBottom>
              <span style={{ color: theme.palette.warning.main }}>- </span>
              {currentEmail}
            </Typography>
            <Typography variant="body2" fontWeight={500} color="text.primary">
              <span style={{ color: theme.palette.success.main }}>+ </span>
              {watchEmail}
            </Typography>
          </Box>

          <Typography variant="body2" color="text.secondary">
            You will need to log in again with your new email address. All your data and settings
            will remain intact.
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 2.5, pb: 2.5, pt: 1 }}>
          <Button
            onClick={handleCancelEmailChange}
            size="medium"
            sx={{
              textTransform: 'none',
              color: 'text.secondary',
              fontWeight: 500,
            }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleConfirmEmailChange}
            variant="contained"
            color="primary"
            size="medium"
            sx={{
              textTransform: 'none',
              borderRadius: 1,
              px: 2,
              height: 38,
              fontWeight: 500,
              boxShadow: theme.shadows[1],
              '&:hover': {
                boxShadow: theme.shadows[2],
              },
            }}
          >
            Confirm & Log Out
          </Button>
        </DialogActions>
      </Dialog>

      {/* Password Dialog */}
      <Dialog
        open={isChangePasswordOpen}
        onClose={handleClosePasswordDialog}
        BackdropProps={{
          sx: {
            backdropFilter: 'blur(1px)',
            backgroundColor: alpha(theme.palette.common.black, 0.3),
          },
        }}
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
