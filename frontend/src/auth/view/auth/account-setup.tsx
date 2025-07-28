import type { Control } from 'react-hook-form';

import { z } from 'zod';
import { Icon } from '@iconify/react';
import { useDispatch } from 'react-redux';
import { useNavigate } from 'react-router';
import eyeIcon from '@iconify-icons/mdi/eye';
import tagIcon from '@iconify-icons/mdi/tag';
import lockIcon from '@iconify-icons/mdi/lock';
import emailIcon from '@iconify-icons/mdi/email';
import checkIcon from '@iconify-icons/mdi/check';
import React, { useState, useEffect } from 'react';
import domainIcon from '@iconify-icons/mdi/domain';
import eyeOffIcon from '@iconify-icons/mdi/eye-off';
import accountIcon from '@iconify-icons/mdi/account';
import refreshIcon from '@iconify-icons/mdi/refresh';
import { zodResolver } from '@hookform/resolvers/zod';
import buildingIcon from '@iconify-icons/mdi/building';
import lockCheckIcon from '@iconify-icons/mdi/lock-check';
import mapMarkerIcon from '@iconify-icons/mdi/map-marker';
import accountCircleIcon from '@iconify-icons/mdi/account-circle';
import { useForm, Controller, FormProvider } from 'react-hook-form';

import {
  Box,
  Grid,
  alpha,
  Alert,
  Paper,
  Button,
  styled,
  Snackbar,
  useTheme,
  Container,
  TextField,
  IconButton,
  Typography,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { setEmail } from 'src/store/auth-slice';

import { OrgExists, AccountSetUp } from 'src/auth/context/jwt';

// Import the AccountType type
export type AccountType = 'individual' | 'business';

// Styled components using the theme
const StyledTextField = styled(TextField)(({ theme }) => ({
  '& .MuiOutlinedInput-root': {
    borderRadius: theme.shape.borderRadius,
    '&:hover fieldset': {
      borderColor: theme.palette.primary.light,
    },
    '&.Mui-focused fieldset': {
      borderColor: theme.palette.primary.light,
    },
  },
}));

const StyledButton = styled(Button)(({ theme }) => ({
  height: '44px',
  borderRadius: theme.shape.borderRadius,
  textTransform: 'none',
  fontWeight: 500,
}));

// Validation Schemas

const permanentAddressSchema = z.object({
  addressLine1: z.string().max(100).optional(),
  city: z.string().max(50).optional(),
  state: z.string().max(50).optional(),
  postCode: z.string().max(20).optional(),
  country: z.string().max(20).optional(),
});

// Base schema with fields needed for both individual and business
const baseSchema = z.object({
  adminFullName: z
    .string()
    .min(1, 'Full name is required')
    .max(100, 'Name must be less than 100 characters'),
  contactEmail: z.string().min(1, 'Email is required').email('Invalid email'),
  password: z
    .string()
    .min(8, 'Password must be at least 8 characters')
    .regex(/[A-Z]/, 'Password must contain at least one uppercase letter')
    .regex(/[a-z]/, 'Password must contain at least one lowercase letter')
    .regex(/[0-9]/, 'Password must contain at least one number')
    .regex(/[^A-Za-z0-9]/, 'Password must contain at least one special character'),
  confirmPassword: z.string(),
});

// business-specific schema
const organizationSchema = baseSchema.extend({
  registeredName: z.string().min(1, 'Organization name is required').max(100),
  shortName: z.string().max(50).optional(),
  permanentAddress: permanentAddressSchema.optional(),
});

// Individual schema (without organization fields)
const individualSchema = baseSchema.extend({});

// Function to choose the appropriate schema based on account type
const getValidationSchema = (accountType: AccountType) => {
  const schema = accountType === 'business' ? organizationSchema : individualSchema;

  return schema.refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ['confirmPassword'],
  });
};

// Update the styled components for better UI
const StyledContainer = styled(Container)(({ theme }) => ({
  maxWidth: '800px !important',
  margin: '0 auto',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  [theme.breakpoints.down('sm')]: {
    padding: theme.spacing(2),
  },
}));

const StyledPaper = styled(Paper)(({ theme }) => ({
  padding: theme.spacing(3),
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius * 1.5,
  boxShadow: 'none',
  border: `1px solid ${theme.palette.divider}`,
  [theme.breakpoints.down('sm')]: {
    padding: theme.spacing(3),
  },
}));

const SectionTitle = styled(Typography)(({ theme }) => ({
  fontWeight: 600,
  marginBottom: theme.spacing(0),
  color: theme.palette.text.primary,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
}));

interface PasswordSectionProps {
  control: Control<any>;
}

const PasswordSection = ({ control }: PasswordSectionProps) => {
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  return (
    <>
      <Grid item xs={12} sm={6}>
        <Controller
          name="password"
          control={control}
          render={({ field, fieldState }) => (
            <StyledTextField
              {...field}
              fullWidth
              type={showPassword ? 'text' : 'password'}
              label="Password *"
              error={!!fieldState.error}
              helperText={fieldState.error?.message as React.ReactNode}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Icon icon={lockIcon} />
                  </InputAdornment>
                ),
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton onClick={() => setShowPassword(!showPassword)} edge="end">
                      <Icon icon={showPassword ? eyeIcon : eyeOffIcon} />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          )}
        />
      </Grid>
      <Grid item xs={12} sm={6}>
        <Controller
          name="confirmPassword"
          control={control}
          render={({ field, fieldState }) => (
            <StyledTextField
              {...field}
              fullWidth
              type={showConfirmPassword ? 'text' : 'password'}
              label="Confirm Password *"
              error={!!fieldState.error}
              helperText={fieldState.error?.message as React.ReactNode}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Icon icon={lockCheckIcon} />
                  </InputAdornment>
                ),
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                      edge="end"
                    >
                      <Icon icon={showConfirmPassword ? eyeIcon : eyeOffIcon} />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
          )}
        />
      </Grid>
    </>
  );
};

// Props interface for AccountSetupForm
interface AccountSetupFormProps {
  accountType: AccountType;
}

export const AccountSetupForm: React.FC<AccountSetupFormProps> = ({ accountType }) => {
  const theme = useTheme();
  // Get the correct validation schema based on account type
  const schema = getValidationSchema(accountType);

  // Set up initial form data based on account type
  const initialFormData: any = {
    adminFullName: '',
    contactEmail: '',
    password: '',
    confirmPassword: '',
  };

  // Add organization-specific fields if needed
  if (accountType === 'business') {
    initialFormData.registeredName = '';
    initialFormData.shortName = '';
    initialFormData.permanentAddress = {
      addressLine1: '',
      city: '',
      state: '',
      postCode: '',
      country: '',
    };
  }

  const methods = useForm({
    resolver: zodResolver(schema),
    defaultValues: initialFormData,
  });

  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = methods;

  const dispatch = useDispatch();
  const navigate = useNavigate();
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error' | 'warning',
  });

  useEffect(() => {
    const checkOrgExists = async () => {
      try {
        const response = await OrgExists();
        if (response.exists === false) {
          setSnackbar({
            open: true,
            message: `Set up account to continue`,
            severity: 'warning',
          });
          navigate('/auth/sign-up');
        } else {
          navigate('/auth/sign-in');
        }
      } catch (err) {
        console.error('Error checking if organization exists:', err);
        // Default to false if there's an error
      }
    };

    checkOrgExists();
    // eslint-disable-next-line
  }, []);

  const onSubmit = async (data: any) => {
    try {
      // Add accountType to the data being sent to the API
      await AccountSetUp({ ...data, accountType });

      dispatch(setEmail(data.contactEmail));
      setSnackbar({
        open: true,
        message:
          accountType === 'business'
            ? 'Organization created successfully!'
            : 'Account created successfully!',
        severity: 'success',
      });
      reset(initialFormData);
      navigate('/auth/sign-in');
    } catch (error) {
      // setSnackbar({
      //   open: true,
      //   message: `Failed to create ${accountType === 'business' ? 'business' : 'account'}`,
      //   severity: 'error',
      // });
    }
  };

  const getNestedErrorMessage = (errs: any, field: string): string | undefined => {
    if (!errs.permanentAddress) return undefined;

    // Cast to Record<string, any> to avoid TypeScript errors
    const addressErrors = errors.permanentAddress as Record<string, any>;

    // Check if the field exists in the error object
    if (typeof addressErrors === 'object' && field in addressErrors) {
      return addressErrors[field]?.message;
    }

    return undefined;
  };

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: theme.palette.background.default,
        zIndex: 2,
      }}
    >
      <StyledContainer>
        <Box
          sx={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            mt: accountType === 'individual' ? 16 : 0,
            py: { xs: 2, sm: 3 },
          }}
        >
          <Box sx={{ mb: 2, textAlign: 'center' }}>
            <Typography
              variant="h3"
              sx={{
                color: theme.palette.primary.main,
                fontWeight: 700,
                mb: 1,
              }}
            >
              {accountType === 'business' ? 'Set Up Your Organization' : 'Create Your Account'}
            </Typography>
            <Typography variant="body2" sx={{ color: theme.palette.text.secondary }}>
              {accountType === 'business'
                ? 'Create your organization profile to get started with PIPESHUB'
                : 'Create your individual account to get started with PIPESHUB'}
            </Typography>
          </Box>

          <StyledPaper>
            <FormProvider {...methods}>
              <form onSubmit={handleSubmit(onSubmit)}>
                <Grid container spacing={2}>
                  {/* Organization Details Section - Only show for organization account type */}
                  {accountType === 'business' && (
                    <>
                      <Grid item xs={12}>
                        <SectionTitle>
                          <Icon icon={buildingIcon} />
                          Organization Details
                        </SectionTitle>
                      </Grid>

                      <Grid item xs={12} sm={6}>
                        <Controller
                          name="registeredName"
                          control={control}
                          render={({ field, fieldState }) => (
                            <StyledTextField
                              {...field}
                              fullWidth
                              label="Organization Name *"
                              error={!!fieldState.error}
                              helperText={fieldState.error?.message as React.ReactNode}
                              InputProps={{
                                startAdornment: (
                                  <InputAdornment position="start">
                                    <Icon icon={domainIcon} />
                                  </InputAdornment>
                                ),
                              }}
                            />
                          )}
                        />
                      </Grid>

                      <Grid item xs={12} sm={6}>
                        <Controller
                          name="shortName"
                          control={control}
                          render={({ field, fieldState }) => (
                            <StyledTextField
                              {...field}
                              fullWidth
                              label="Short Name"
                              error={!!fieldState.error}
                              helperText={fieldState.error?.message as React.ReactNode}
                              InputProps={{
                                startAdornment: (
                                  <InputAdornment position="start">
                                    <Icon icon={tagIcon} />
                                  </InputAdornment>
                                ),
                              }}
                            />
                          )}
                        />
                      </Grid>
                    </>
                  )}

                  {/* Admin/User Details Section */}
                  <Grid item xs={12}>
                    <SectionTitle>
                      <Icon icon={accountCircleIcon} />
                      {accountType === 'business' ? 'Admin Details' : 'User Details'}
                    </SectionTitle>
                  </Grid>

                  <Grid item xs={12} sm={6}>
                    <Controller
                      name="adminFullName"
                      control={control}
                      render={({ field, fieldState }) => (
                        <StyledTextField
                          {...field}
                          fullWidth
                          label={accountType === 'business' ? 'Admin Name *' : 'Full Name *'}
                          error={!!fieldState.error}
                          helperText={fieldState.error?.message as React.ReactNode}
                          InputProps={{
                            startAdornment: (
                              <InputAdornment position="start">
                                <Icon icon={accountIcon} />
                              </InputAdornment>
                            ),
                          }}
                        />
                      )}
                    />
                  </Grid>

                  <Grid item xs={12} sm={6}>
                    <Controller
                      name="contactEmail"
                      control={control}
                      render={({ field, fieldState }) => (
                        <StyledTextField
                          {...field}
                          fullWidth
                          label="Email *"
                          type="email"
                          error={!!fieldState.error}
                          helperText={fieldState.error?.message as React.ReactNode}
                          InputProps={{
                            startAdornment: (
                              <InputAdornment position="start">
                                <Icon icon={emailIcon} />
                              </InputAdornment>
                            ),
                          }}
                        />
                      )}
                    />
                  </Grid>

                  <PasswordSection control={control} />

                  {/* Address Section - Only show for organization account type */}
                  {accountType === 'business' && (
                    <>
                      <Grid item xs={12}>
                        <SectionTitle>
                          <Icon icon={mapMarkerIcon} />
                          Address Details
                        </SectionTitle>
                      </Grid>

                      <Grid item xs={12}>
                        <Grid container spacing={2}>
                          <Grid item xs={12}>
                            <Controller
                              name="permanentAddress.addressLine1"
                              control={control}
                              render={({ field, fieldState }) => (
                                <StyledTextField
                                  {...field}
                                  fullWidth
                                  label="Street Address"
                                  error={!!getNestedErrorMessage(errors, 'addressLine1')}
                                  helperText={getNestedErrorMessage(errors, 'addressLine1')}
                                />
                              )}
                            />
                          </Grid>

                          <Grid item xs={12} sm={6}>
                            <Controller
                              name="permanentAddress.city"
                              control={control}
                              render={({ field, fieldState }) => (
                                <StyledTextField
                                  {...field}
                                  fullWidth
                                  label="City"
                                  error={!!getNestedErrorMessage(errors, 'city')}
                                  helperText={getNestedErrorMessage(errors, 'city')}
                                />
                              )}
                            />
                          </Grid>

                          <Grid item xs={12} sm={6}>
                            <Controller
                              name="permanentAddress.state"
                              control={control}
                              render={({ field, fieldState }) => (
                                <StyledTextField
                                  {...field}
                                  fullWidth
                                  label="State / Province"
                                  error={!!getNestedErrorMessage(errors, 'state')}
                                  helperText={getNestedErrorMessage(errors, 'state')}
                                />
                              )}
                            />
                          </Grid>

                          <Grid item xs={12} sm={6}>
                            <Controller
                              name="permanentAddress.postCode"
                              control={control}
                              render={({ field, fieldState }) => (
                                <StyledTextField
                                  {...field}
                                  fullWidth
                                  label="ZIP / Postal Code"
                                  error={!!getNestedErrorMessage(errors, 'postCode')}
                                  helperText={getNestedErrorMessage(errors, 'postCode')}
                                />
                              )}
                            />
                          </Grid>

                          <Grid item xs={12} sm={6}>
                            <Controller
                              name="permanentAddress.country"
                              control={control}
                              render={({ field, fieldState }) => (
                                <StyledTextField
                                  {...field}
                                  fullWidth
                                  label="Country"
                                  error={!!getNestedErrorMessage(errors, 'country')}
                                  helperText={getNestedErrorMessage(errors, 'country')}
                                />
                              )}
                            />
                          </Grid>
                        </Grid>
                      </Grid>
                    </>
                  )}

                  {/* Form Actions */}
                  <Grid item xs={12}>
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'flex-end',
                        gap: 2,
                        mt: 2,
                      }}
                    >
                      <StyledButton
                        variant="outlined"
                        onClick={() => reset(initialFormData)}
                        startIcon={<Icon icon={refreshIcon} />}
                        sx={{
                          borderColor: theme.palette.divider,
                          color: theme.palette.text.secondary,
                          '&:hover': {
                            borderColor: theme.palette.text.secondary,
                            bgcolor: alpha(theme.palette.text.secondary, 0.08),
                          },
                        }}
                      >
                        Reset
                      </StyledButton>
                      <StyledButton
                        type="submit"
                        variant="contained"
                        disabled={isSubmitting}
                        sx={{
                          bgcolor: theme.palette.primary.main,
                          '&:hover': {
                            bgcolor: theme.palette.primary.dark,
                          },
                        }}
                      >
                        {isSubmitting ? (
                          <>
                            <CircularProgress size={20} sx={{ mr: 1, color: 'white' }} />
                            Creating...
                          </>
                        ) : (
                          <>
                            <Icon icon={checkIcon} style={{ marginRight: 8 }} />
                            {accountType === 'business' ? 'Create Organization' : 'Create Account'}
                          </>
                        )}
                      </StyledButton>
                    </Box>
                  </Grid>
                </Grid>
              </form>
            </FormProvider>
          </StyledPaper>
          <Snackbar
            open={snackbar.open}
            autoHideDuration={6000}
            onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
            anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
          >
            <Alert
              severity={snackbar.severity}
              sx={{
                width: '100%',
                ...(snackbar.severity === 'success' && {
                  bgcolor: theme.palette.success.main,
                  color: theme.palette.success.contrastText,
                }),
              }}
              onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
            >
              {snackbar.message}
            </Alert>
          </Snackbar>
        </Box>
      </StyledContainer>
    </Box>
  );
};

export default AccountSetupForm;
