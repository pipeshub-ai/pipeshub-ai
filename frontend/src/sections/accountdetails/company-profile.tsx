import { z as zod } from 'zod';
import React, { useState, useEffect } from 'react';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import infoIcon from '@iconify-icons/solar/info-circle-bold';
import disketteBoldIcon from '@iconify-icons/solar/diskette-bold';
import officeBuildingIcon from '@iconify-icons/mdi/office-building';
import galleryAddBoldIcon from '@iconify-icons/solar/gallery-add-bold';
import trashBinBoldIcon from '@iconify-icons/solar/trash-bin-trash-bold';

import { LoadingButton } from '@mui/lab';
import {
  Box,
  Grid,
  Alert,
  Paper,
  alpha,
  Switch,
  Button,
  Tooltip,
  Snackbar,
  MenuItem,
  useTheme,
  Container,
  Typography,
  CircularProgress,
  FormControlLabel,
} from '@mui/material';

import { countries } from 'src/assets/data';
import { useAdmin } from 'src/context/AdminContext';

import { Iconify } from 'src/components/iconify';
import { Form, Field } from 'src/components/hook-form';

import {
  updateOrg,
  getOrgById,
  getOrgLogo,
  uploadOrgLogo,
  deleteOrgLogo,
  getOrgIdFromToken,
  getDataCollectionConsent,
  updateDataCollectionConsent,
} from './utils';

import type { SnackbarState } from './types/organization-data';

const ProfileSchema = zod.object({
  registeredName: zod.string().min(1, { message: 'Name is required' }),
  shortName: zod.string().optional(),
  contactEmail: zod
    .string()
    .email({ message: 'Invalid email' })
    .min(1, { message: 'Email is required' }),
  permanentAddress: zod.object({
    addressLine1: zod.string().optional(),
    city: zod
      .string()
      .optional()
      .refine(
        (val) => !val || /^[A-Za-z\s]+$/.test(val),
        'City must contain only letters'
      ),
    state: zod
      .string()
      .optional()
      .refine(
        (val) => !val || /^[A-Za-z\s]+$/.test(val),
        'State must contain only letters'
      ),
    postCode: zod.string().optional(),
    country: zod.string().optional(),
  }),
  dataCollectionConsent: zod.boolean().optional(),
});

type ProfileFormData = zod.infer<typeof ProfileSchema>;

export default function CompanyProfile() {
  const theme = useTheme();
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [logo, setLogo] = useState<string | null>(null);
  const [uploading, setUploading] = useState<boolean>(false);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [saveChanges, setSaveChanges] = useState<boolean>(false);
  const [consentLoading, setConsentLoading] = useState<boolean>(false);
  const [showMorePrivacy, setShowMorePrivacy] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState<SnackbarState>({
    open: false,
    message: '',
    severity: undefined,
  });

  const { isAdmin } = useAdmin();

  const methods = useForm<ProfileFormData>({
    resolver: zodResolver(ProfileSchema),
    mode: 'onChange',
  });

  const {
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { isValid, isDirty },
  } = methods;

  const handleCloseSnackbar = (): void => {
    setSnackbar({ open: false, message: '', severity: undefined });
  };

  useEffect(() => {
    const fetchOrgData = async (): Promise<void> => {
      try {
        setLoading(true);
        const orgId = await getOrgIdFromToken();
        const orgData = await getOrgById(orgId);
        const { registeredName, shortName, contactEmail, permanentAddress } = orgData;

        // Fetch data collection consent status
        const consentStatus = isAdmin ? await getDataCollectionConsent() : false;

        reset({
          registeredName,
          shortName,
          contactEmail,
          permanentAddress: {
            addressLine1: permanentAddress?.addressLine1 || '',
            city: permanentAddress?.city || '',
            state: permanentAddress?.state || '',
            postCode: permanentAddress?.postCode || '',
            country: permanentAddress?.country || '',
          },
          dataCollectionConsent: consentStatus,
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

    fetchOrgData();
  }, [reset, isAdmin]);

  // Modify the fetchLogo function in company-profile.tsx to handle errors gracefully
  useEffect(() => {
    const fetchLogo = async () => {
      try {
        const orgId = await getOrgIdFromToken();
        const logoUrl = await getOrgLogo(orgId);
        setLogo(logoUrl);
      } catch (err) {
        // Don't show error message for 404 errors - this is expected when no logo exists
        if (err.response && err.response.status === 404) {
          console.log('No logo found for organization - this is normal for new organizations');
          // Just set logo to null and continue
          setLogo(null);
        } else {
          // For other errors, show the error message
          setError('Failed to fetch logo');
          // setSnackbar({
          //   open: true,
          //   message: err.errorMessage || 'Error loading logo',
          //   severity: 'error',
          // });
          console.error(err, 'error in fetching logo');
        }
      }
    };

    fetchLogo();
  }, []);

  const onSubmit = async (data: ProfileFormData): Promise<void> => {
    try {
      setSaveChanges(true);
      const orgId = await getOrgIdFromToken();
      const msg = await updateOrg(orgId, data);
      setSnackbar({
        open: true,
        message: msg,
        severity: 'success',
      });
    } catch (err) {
      setError('Failed to update organization');
      // setSnackbar({ open: true, message: err.errorMessage, severity: 'error' });
    } finally {
      setSaveChanges(false);
    }
  };

  const handleConsentChange = async (checked: boolean): Promise<void> => {
    try {
      setConsentLoading(true);
      const orgId = await getOrgIdFromToken();
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

  const handleDelete = async (): Promise<void> => {
    try {
      setDeleting(true);
      const orgId = await getOrgIdFromToken();
      await deleteOrgLogo(orgId);
      setSnackbar({ open: true, message: 'Logo removed successfully!', severity: 'success' });
      setDeleting(false);
      setLogo(null);
    } catch (err) {
      setError('Failed to remove logo');
      // setSnackbar({ open: true, message: 'Failed to remove logo', severity: 'error' });
      setDeleting(false);
    }
  };

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = event.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
      setUploading(true);
      const orgId = await getOrgIdFromToken();
      await uploadOrgLogo(formData);
      setSnackbar({ open: true, message: 'Logo updated successfully!', severity: 'success' });
      setUploading(false);
      setLogo(URL.createObjectURL(file));
    } catch (err) {
      setError('Failed to upload logo');
      // setSnackbar({ open: true, message: 'Failed to upload logo', severity: 'error' });
      setUploading(false);
    }
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
              Loading company profile...
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
            px: { xs: 3, md: 4 },
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
            bgcolor: alpha(theme.palette.primary.main, 0.02),
            pt: 2,
          }}
        >
          <Typography variant="h6" fontWeight={500} fontSize="1.25rem">
            Company Profile
          </Typography>
        </Box>

        {/* Content */}
        <Box sx={{ p: { xs: 3, md: 2 } }}>
          <Grid container spacing={{ xs: 3, md: 5 }}>
            {/* Form Section - Now on the LEFT */}
            <Grid item xs={12} md={8}>
              <Form
                methods={methods}
                onSubmit={handleSubmit(onSubmit)}
                {...({ noValidate: true } as any)}
              >
                <Paper
                  elevation={0}
                  sx={{
                    p: 3,
                    borderRadius: 2,
                    border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                    mb: 3,
                  }}
                >
                  <Typography variant="subtitle1" sx={{ mb: 2, fontWeight: 600 }}>
                    Basic Information
                  </Typography>

                  <Grid container spacing={2.5}>
                    <Grid item xs={12}>
                      <Tooltip
                        title="The legal name the company was incorporated under"
                        arrow
                        placement="top-start"
                      >
                        <Box>
                          <Field.Text
                            name="registeredName"
                            label="Registered Name"
                            fullWidth
                            required
                            disabled={!isAdmin}
                            variant="outlined"
                            placeholder="Enter your company's registered name"
                            sx={{
                              '& .MuiOutlinedInput-root': {
                                height: 48,
                              },
                              '& .MuiInputBase-input.Mui-disabled': {
                                cursor: 'not-allowed',
                              },
                            }}
                          />
                        </Box>
                      </Tooltip>
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      <Tooltip
                        title="The name of the company to display within Pipeshub"
                        arrow
                        placement="top-start"
                      >
                        <Box>
                          <Field.Text
                            name="shortName"
                            label="Display Name"
                            fullWidth
                            disabled={!isAdmin}
                            variant="outlined"
                            placeholder="Short name for display"
                            sx={{
                              '& .MuiOutlinedInput-root': {
                                height: 48,
                              },
                              '& .MuiInputBase-input.Mui-disabled': {
                                cursor: 'not-allowed',
                              },
                            }}
                          />
                        </Box>
                      </Tooltip>
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      <Field.Text
                        disabled={!isAdmin}
                        name="contactEmail"
                        label="Contact Email"
                        fullWidth
                        required
                        variant="outlined"
                        placeholder="company@example.com"
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            height: 48,
                          },
                          '& .MuiInputBase-input.Mui-disabled': {
                            cursor: 'not-allowed',
                          },
                        }}
                      />
                    </Grid>
                  </Grid>
                </Paper>

                <Paper
                  elevation={0}
                  sx={{
                    p: 3,
                    borderRadius: 2,
                    border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                    mb: 3,
                  }}
                >
                  <Typography variant="subtitle1" sx={{ mb: 2, fontWeight: 600 }}>
                    Address Information
                  </Typography>

                  <Grid container spacing={2.5}>
                    <Grid item xs={12}>
                      <Field.Text
                        disabled={!isAdmin}
                        name="permanentAddress.addressLine1"
                        label="Street Address"
                        fullWidth
                        variant="outlined"
                        placeholder="Enter street address"
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            height: 45,
                          },
                          '& .MuiInputBase-input.Mui-disabled': {
                            cursor: 'not-allowed',
                          },
                        }}
                      />
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      <Field.Text
                        disabled={!isAdmin}
                        name="permanentAddress.city"
                        label="City"
                        fullWidth
                        variant="outlined"
                        placeholder="Enter city"
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            height: 45,
                          },
                          '& .MuiInputBase-input.Mui-disabled': {
                            cursor: 'not-allowed',
                          },
                        }}
                      />
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      <Field.Text
                        disabled={!isAdmin}
                        name="permanentAddress.state"
                        label="State/Province"
                        fullWidth
                        variant="outlined"
                        placeholder="Enter state or province"
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            height: 45,
                          },
                          '& .MuiInputBase-input.Mui-disabled': {
                            cursor: 'not-allowed',
                          },
                        }}
                      />
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      <Field.Text
                        name="permanentAddress.postCode"
                        label="Zip/Postal Code"
                        fullWidth
                        disabled={!isAdmin}
                        variant="outlined"
                        placeholder="Enter postal code"
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            height: 45,
                          },
                          '& .MuiInputBase-input.Mui-disabled': {
                            cursor: 'not-allowed',
                          },
                        }}
                      />
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      <Field.Select
                        disabled={!isAdmin}
                        name="permanentAddress.country"
                        label="Country"
                        fullWidth
                        variant="outlined"
                        sx={{
                          '& .MuiOutlinedInput-root': {
                            height: 45,
                          },
                          '& .MuiInputBase-input.Mui-disabled': {
                            cursor: 'not-allowed',
                          },
                        }}
                      >
                        {countries.map((country) => (
                          <MenuItem key={country.label} value={country.label}>
                            {country.label}
                          </MenuItem>
                        ))}
                      </Field.Select>
                    </Grid>
                  </Grid>
                </Paper>

                {/* Data Collection Consent Section */}
                {isAdmin && (
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
                                  {field.value ? 'Enabled' : 'Disabled'}
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
                            <Typography variant="body2" color="info.dark" sx={{ fontWeight: 500 }}>
                              Disclaimer: We do not sell, trade, or otherwise transfer your personal
                              information to third parties
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
                )}

                {isAdmin && (
                  <Box sx={{ display: 'flex', justifyContent: 'flex-start' }}>
                    <LoadingButton
                      color="primary"
                      type="submit"
                      variant="contained"
                      loading={saveChanges}
                      loadingIndicator="Saving..."
                      startIcon={<Iconify icon={disketteBoldIcon} width={18} height={18} />}
                      disabled={!isValid || !isDirty || !isAdmin}
                      sx={{
                        height: 42,
                        px: 3,
                        borderRadius: 1,
                        textTransform: 'none',
                        fontWeight: 500,
                        fontSize: '0.9rem',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                        '&:hover': {
                          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                        },
                        '&.Mui-disabled': {
                          cursor: 'not-allowed',
                          pointerEvents: 'auto',
                        },
                      }}
                    >
                      Save changes
                    </LoadingButton>
                  </Box>
                )}
              </Form>
            </Grid>

            {/* Logo Section - Now on the RIGHT */}
            <Grid item xs={12} md={4}>
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  pt: { xs: 1, md: 2 },
                }}
              >
                <Box sx={{ position: 'relative', mb: 3 }}>
                  {logo ? (
                    <Box
                      sx={{
                        width: 150,
                        height: 150,
                        borderRadius: 2,
                        border: `1px solid ${alpha(theme.palette.divider, 0.2)}`,
                        boxShadow: theme.shadows[2],
                        position: 'relative',
                        margin: '0 auto',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        backgroundColor: 'transparent',
                        padding: 2, // Add padding to ensure logo doesn't touch the borders
                      }}
                    >
                      <Box
                        sx={{
                          width: '100%',
                          height: '100%',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          position: 'relative',
                        }}
                      >
                        <img
                          src={logo}
                          alt="Company Logo"
                          style={{
                            maxWidth: '100%',
                            maxHeight: '100%',
                            width: 'auto',
                            height: 'auto',
                            objectFit: 'contain',
                            display: 'block',
                          }}
                        />
                      </Box>
                    </Box>
                  ) : (
                    <Box
                      sx={{
                        width: 150,
                        height: 150,
                        borderRadius: 2,
                        bgcolor: alpha(theme.palette.primary.main, 0.08),
                        boxShadow: `0 0 0 1px ${alpha(theme.palette.divider, 0.2)}`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        margin: '0 auto',
                      }}
                    >
                      <Iconify
                        icon={officeBuildingIcon}
                        width={70}
                        height={70}
                        color={alpha(theme.palette.primary.main, 0.7)}
                      />
                    </Box>
                  )}

                  {isAdmin && (
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'center',
                        gap: 2,
                        mt: 2,
                      }}
                    >
                      <input
                        style={{ display: 'none' }}
                        id="file-upload"
                        type="file"
                        accept="image/*"
                        onChange={handleUpload}
                      />

                      <Tooltip title={logo ? 'Update logo' : 'Upload logo'}>
                        <label htmlFor="file-upload">
                          <LoadingButton
                            component="span"
                            variant="outlined"
                            color="primary"
                            size="small"
                            startIcon={<Iconify icon={galleryAddBoldIcon} width={18} height={18} />}
                            loading={uploading}
                            loadingPosition="start"
                            sx={{
                              borderRadius: 1,
                              fontSize: '0.8rem',
                              px: 2,
                              py: 0.7,
                              textTransform: 'none',
                              fontWeight: 500,
                              boxShadow: 'none',
                              '&:hover': {
                                boxShadow: theme.shadows[1],
                              },
                            }}
                          >
                            {logo ? 'Change' : 'Upload'}
                          </LoadingButton>
                        </label>
                      </Tooltip>

                      {logo && (
                        <Tooltip title="Remove logo">
                          <LoadingButton
                            variant="outlined"
                            color="error"
                            size="small"
                            startIcon={<Iconify icon={trashBinBoldIcon} width={18} height={18} />}
                            onClick={handleDelete}
                            loading={deleting}
                            loadingPosition="start"
                            sx={{
                              borderRadius: 1,
                              fontSize: '0.8rem',
                              px: 2,
                              py: 0.7,
                              textTransform: 'none',
                              fontWeight: 500,
                              boxShadow: 'none',
                              '&:hover': {
                                boxShadow: theme.shadows[1],
                              },
                            }}
                          >
                            Remove
                          </LoadingButton>
                        </Tooltip>
                      )}
                    </Box>
                  )}
                </Box>

                {!logo && isAdmin && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    align="center"
                    sx={{ mt: 0.5, display: 'block' }}
                  >
                    Add your company logo
                  </Typography>
                )}
              </Box>

              <Paper
                elevation={0}
                sx={{
                  p: 2.5,
                  borderRadius: 2,
                  bgcolor: alpha(theme.palette.background.default, 0.5),
                  border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                  mt: 1,
                }}
              >
                <Typography
                  variant="subtitle2"
                  sx={{ mb: 1, color: 'text.primary', fontWeight: 600 }}
                >
                  Company Logo
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.85rem' }}>
                  Your logo will appear on your account dashboard, emails, and documents generated
                  from PipesHub.
                </Typography>
              </Paper>
            </Grid>
          </Grid>
        </Box>
      </Paper>

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
