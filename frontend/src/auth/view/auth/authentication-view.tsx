import type { AuthState } from 'src/store/auth-slice';
import type { AuthResponse } from 'src/auth/context/jwt';

import { z as zod } from 'zod';
import PropTypes from 'prop-types';
import { useForm } from 'react-hook-form';
import { useNavigate } from 'react-router';
// Import specific icons
import emailIcon from '@iconify-icons/mdi/email';
import googleIcon from '@iconify-icons/mdi/google';
import { useRef, useState, useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useDispatch, useSelector } from 'react-redux';
import microsoftIcon from '@iconify-icons/mdi/microsoft';
import shieldAccountIcon from '@iconify-icons/mdi/shield-account';
import arrowBackIcon from '@iconify-icons/eva/arrow-ios-back-fill';
import passwordIcon from '@iconify-icons/mdi/form-textbox-password';
import microsoftAzureIcon from '@iconify-icons/mdi/microsoft-azure';
import { GoogleLogin, GoogleOAuthProvider } from '@react-oauth/google';
import cellphoneMessageIcon from '@iconify-icons/mdi/cellphone-message';

import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import Alert from '@mui/material/Alert';
import Stack from '@mui/material/Stack';
import Divider from '@mui/material/Divider';
import Tooltip from '@mui/material/Tooltip';
import { alpha } from '@mui/material/styles';
import TextField from '@mui/material/TextField';
import IconButton from '@mui/material/IconButton';
import Typography from '@mui/material/Typography';
import LoadingButton from '@mui/lab/LoadingButton';
import CardContent from '@mui/material/CardContent';
import InputAdornment from '@mui/material/InputAdornment';
import { Tab, Tabs, Fade, Grow, useTheme, Snackbar } from '@mui/material';

import { ErrorType, withErrorHandling } from 'src/utils/axios';

import { setEmail } from 'src/store/auth-slice';

import { Iconify } from 'src/components/iconify';

import { useAuthContext } from 'src/auth/hooks';
import MicrosoftLoginButton from 'src/auth/components/microsoft-login-button';
import {
  sendOtp,
  OrgExists,
  authInitConfig,
  forgotPassword,
  SignInWithOAuth,
  SignInWithGoogle,
  SignInWithAzureAd,
  SignInWithMicrosoft,
} from 'src/auth/context/jwt';

import OtpSignIn from './otp-sign-in';
import SamlSignIn from './saml-sign-in';
import OAuthSignIn from './oauth-sign-in';
import PasswordSignIn from './password-sign-in';

interface RootState {
  auth: AuthState;
}

interface LazyOtpSignInProps {
  email: string;
  onNextStep?: (response: any) => void;
  onAuthComplete?: () => void;
  onForgotPassword?: () => void;
  [key: string]: any; // For other props
}

// Schema for initial email validation
const InitialSchema = zod.object({
  email: zod
    .string()
    .min(1, { message: 'Email is required!' })
    .email({ message: 'Email must be a valid email address!' }),
});

type InitialSchemaType = zod.infer<typeof InitialSchema>;

interface AuthStep {
  step: number;
  methods: string[];
  authProviders: Record<string, any>;
}

// Tab configuration
const tabConfig = {
  password: {
    icon: passwordIcon,
    label: 'Password',
    component: PasswordSignIn,
  },
  otp: {
    icon: cellphoneMessageIcon,
    label: 'OTP',
    component: LazyOtpSignIn,
  },
  samlSso: {
    icon: shieldAccountIcon,
    label: 'SSO',
    component: SamlSignIn,
  },
};

// Custom Lazy OTP component that prevents auto-sending on mount
function LazyOtpSignIn({ email, ...otherProps }: LazyOtpSignInProps) {
  const [sendOtpClicked, setSendOtpClicked] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const hasAttemptedSend = useRef(false);

  if (!sendOtpClicked) {
    return (
      <Box sx={{ width: '100%', textAlign: 'center' }}>
        <Alert
          severity="info"
          variant="outlined"
          sx={{
            mb: 3,
            bgcolor: (theme) => alpha(theme.palette.info.main, 0.08),
            '& .MuiAlert-message': {
              width: '100%',
            },
          }}
        >
          <Typography variant="body2">
            Click the button below to receive a one-time password via email
          </Typography>
        </Alert>

        {error && (
          <Alert severity="error" variant="outlined" onClose={() => setError('')} sx={{ mb: 3 }}>
            {error}
          </Alert>
        )}

        <LoadingButton
          fullWidth
          size="large"
          variant="contained"
          loading={loading}
          onClick={() => {
            // Prevent multiple clicks/calls
            if (loading || hasAttemptedSend.current) return;

            setLoading(true);
            hasAttemptedSend.current = true;

            // Send OTP when the user explicitly requests it
            sendOtp({ email })
              .then(() => {
                setSendOtpClicked(true);
              })
              .catch((err) => {
                console.error('Error sending OTP:', err);
                setError('Failed to send OTP. Please try again.');
                hasAttemptedSend.current = false;
              })
              .finally(() => {
                setLoading(false);
              });
          }}
          sx={{ mt: 2 }}
        >
          Send OTP
        </LoadingButton>
      </Box>
    );
  }

  // To prevent the OtpSignIn from automatically sending another OTP,
  // we modify the props to pass an initialOtpSent flag
  return <OtpSignIn email={email} {...otherProps} initialOtpSent />;
}

// Social login configuration
const socialConfig = {
  google: {
    icon: googleIcon,
    label: 'Continue with Google',
    color: '#DB4437',
  },
  microsoft: {
    icon: microsoftIcon,
    label: 'Continue with Microsoft',
    color: '#00A4EF',
  },
  azureAd: {
    icon: microsoftAzureIcon,
    label: 'Continue with Azure AD',
    color: '#0078D4',
  },
  oauth: {
    icon: 'mdi:key-variant',
    label: 'Continue with OAuth',
    color: '#6366F1',
  },
};

export const AuthenticationView = () => {
  const dispatch = useDispatch();
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState('');
  const [authSteps, setAuthSteps] = useState<AuthStep[]>([]);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [selectedTabs, setSelectedTabs] = useState<number[]>([0, 0]); // Track selected tab for each step
  const emailFromStore = useSelector((state: RootState) => state.auth.email);
  const { checkUserSession } = useAuthContext();
  const navigate = useNavigate();
  const [snackbar, setSnackbar] = useState({
    open: false,
    message: '',
    severity: 'success' as 'success' | 'error',
  });

  // Prevent components from auto-initializing when steps change
  const componentMountRef = useRef(false);

  const methods = useForm<InitialSchemaType>({
    resolver: zodResolver(InitialSchema),
    defaultValues: {
      email: emailFromStore || '',
    },
  });

  const {
    handleSubmit,
    register,
    formState: { errors },
  } = methods;

  const theme = useTheme();

  // Get current authentication step
  const currentStep = authSteps[currentStepIndex] || null;
  const selectedTab = selectedTabs[currentStepIndex] || 0;

  // Initial authentication configuration
  const onSubmit = async (data: InitialSchemaType) => {
    setLoading(true);
    setError('');
    try {
      const response = await authInitConfig(data.email);
      if (response) {
        // Initialize first auth step
        const newStep: AuthStep = {
          step: response.currentStep,
          methods: response.allowedMethods,
          authProviders: response.authProviders || {},
        };

        setAuthSteps([newStep]);
        setCurrentStepIndex(0);
        setSelectedTabs([0, 0]); // Reset tab selections
        dispatch(setEmail(data.email));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  // Handle the next step in MFA
  const handleNextAuthStep = (response: AuthResponse) => {
    if (response.nextStep !== undefined && response.allowedMethods) {
      // Create a new auth step
      const newStep: AuthStep = {
        step: response.nextStep,
        methods: response.allowedMethods,
        authProviders: response.authProviders || {},
      };

      // Add the new step to our steps array
      setAuthSteps((prev) => [...prev, newStep]);
      setCurrentStepIndex((prev) => prev + 1);

      // Reset our component mount ref for the new step
      componentMountRef.current = false;
    }
  };

  // Handle successful authentication
  const handleAuthComplete = () => {
    checkUserSession?.();
    // router.push('/');
    navigate('/');
  };

  // Handle Google login success
  const handleGoogleLoginSuccess = async (response: any) => {
    try {
      const { credential } = response;
      if (!credential) {
        throw new Error('No credential received from Google');
      }

      setLoading(true);
      const authResponse = await SignInWithGoogle({ credential });

      // Check if this is the final step
      if (authResponse.accessToken && authResponse.refreshToken) {
        handleAuthComplete();
      } else if (authResponse.nextStep !== undefined) {
        handleNextAuthStep(authResponse);
      }
    } catch (err) {
      console.error('Google login failed', err);
      setError('Google login failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Handle Microsoft/Azure login success
  const handleMsalLoginSuccess = async (response: any) => {
    try {
      const { credential, method } = response;
      if (!credential) {
        throw new Error(`No credential received from ${method}`);
      }

      setLoading(true);
      let authResponse;

      if (method === 'microsoft') {
        authResponse = await SignInWithMicrosoft(credential);
      } else {
        // azureAd
        authResponse = await SignInWithAzureAd(credential);
      }

      // Check if this is the final step
      if (authResponse.accessToken && authResponse.refreshToken) {
        handleAuthComplete();
      } else if (authResponse.nextStep !== undefined) {
        handleNextAuthStep(authResponse);
      }
    } catch (err) {
      console.error('Microsoft/Azure login failed', err);
      setError('Authentication failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Handle OAuth login success
  const handleOAuthLoginSuccess = async (credentials: { accessToken?: string; idToken?: string }) => {
    try {
      if (!credentials.accessToken && !credentials.idToken) {
        throw new Error('No credentials received from OAuth provider');
      }

      setLoading(true);
      const authResponse = await SignInWithOAuth(credentials);

      // Check if this is the final step
      if (authResponse.accessToken && authResponse.refreshToken) {
        handleAuthComplete();
      } else if (authResponse.nextStep !== undefined) {
        handleNextAuthStep(authResponse);
      }
    } catch (err) {
      console.error('OAuth login failed', err);
      setError('OAuth authentication failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleTabChange = (_: React.SyntheticEvent, newValue: number) => {
    // Update the selected tab for the current step
    const newSelectedTabs = [...selectedTabs];
    newSelectedTabs[currentStepIndex] = newValue;
    setSelectedTabs(newSelectedTabs);

    // Reset component mount ref when a new tab is selected
    componentMountRef.current = false;
  };

  const handleBack = () => {
    // Always go back to email form
    setAuthSteps([]);
    setCurrentStepIndex(0);
    setSelectedTabs([0, 0]);
    componentMountRef.current = false;
  };

  const handleForgotPassword = () => {
    // Make sure we have an email address
    if (!emailFromStore) {
      setError('Email address is required to reset password.');
      return;
    }

    // Show loading indicator
    setLoading(true);

    // Use the withErrorHandling wrapper for consistent error processing
    withErrorHandling(
      async () => {
        await forgotPassword({ email: emailFromStore });

        // Show success message
        setSnackbar({
          open: true,
          message: 'Password reset instructions have been sent to your email.',
          severity: 'success',
        });
      },
      (processedError) => {
        // Handle specific error types
        if (
          processedError.type === ErrorType.NETWORK_ERROR ||
          processedError.type === ErrorType.TIMEOUT_ERROR
        ) {
          setError(
            'Network error. Unable to connect to server. Please check your internet connection.'
          );
        } else if (processedError.type === ErrorType.VALIDATION_ERROR) {
          setError(processedError.message || 'Please provide a valid email address.');
        } else {
          setError(
            processedError.message || 'Failed to send password reset email. Please try again later.'
          );
        }
      }
    ).finally(() => {
      setLoading(false);
    });
  };

  // Track when components have mounted to prevent re-initializing
  useEffect(() => {
    if (currentStep) {
      componentMountRef.current = true;
    }
  }, [currentStep, selectedTab]);

  useEffect(() => {
    const checkOrgExists = async () => {
      try {
        const response = await OrgExists();
        if (response.exists === false) {
          // setSnackbar({
          //   open: true,
          //   message: `Set up account to continue`,
          //   severity: 'error',
          // });
          navigate('/auth/sign-up');
        } else {
          navigate('/auth/sign-in');
        }
      } catch (err) {
        console.error('Error checking if organization exists:', err);
      }
    };

    checkOrgExists();
    // eslint-disable-next-line
  }, []);

  // Initial email form
  if (authSteps.length === 0) {
    return (
      <Fade in timeout={450}>
        <Card
          sx={{
            width: '100%',
            maxWidth: 480,
            mx: 'auto',
            mt: 4,
            backdropFilter: 'blur(6px)',
            bgcolor: (theme1) => alpha(theme1.palette.background.paper, 0.9),
            boxShadow: (theme1) => `0 0 2px ${alpha(theme1.palette.grey[500], 0.2)}, 
                                 0 12px 24px -4px ${alpha(theme1.palette.grey[500], 0.12)}`,
          }}
        >
          <CardContent sx={{ pt: 5, pb: 5 }}>
            <Box sx={{ mb: 5, textAlign: 'center' }}>
              <Typography variant="h4" sx={{ mb: 1, fontWeight: 700 }}>
                Welcome
              </Typography>

              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                Sign in to continue to your account
              </Typography>
            </Box>

            {error && (
              <Grow in>
                <Alert
                  severity="error"
                  onClose={() => setError('')}
                  sx={{
                    mb: 3,
                    '& .MuiAlert-message': { width: '100%' },
                  }}
                >
                  {error}
                </Alert>
              </Grow>
            )}

            <form onSubmit={handleSubmit(onSubmit)}>
              <TextField
                fullWidth
                autoFocus
                size="medium"
                label="Email address"
                variant="outlined"
                {...register('email')}
                error={!!errors.email}
                helperText={errors.email?.message}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify icon={emailIcon} width={24} sx={{ color: 'text.secondary' }} />
                    </InputAdornment>
                  ),
                }}
                sx={{
                  mb: 3,
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
                    bgcolor: 'background.paper',
                  },
                }}
              />

              <LoadingButton
                fullWidth
                size="large"
                type="submit"
                variant="contained"
                loading={loading}
                sx={{
                  height: 48,
                  bgcolor: 'primary.main',
                  color: 'primary.contrastText',
                  borderRadius: 1.5,
                  '&:hover': {
                    bgcolor: 'primary.dark',
                  },
                }}
              >
                Continue
              </LoadingButton>
            </form>
          </CardContent>
        </Card>
      </Fade>
    );
  }

  if (!currentStep) {
    return null;
  }

  // Split methods into tabs and social logins
  const tabMethods = currentStep.methods.filter((method) =>
    ['password', 'otp', 'samlSso'].includes(method)
  );
  const socialMethods = currentStep.methods.filter((method) =>
    ['google', 'microsoft', 'azureAd', 'oauth'].includes(method)
  );

  // Get the component for the current tab
  const currentMethod = tabMethods[selectedTab];
  const CurrentAuthComponent =
    currentMethod && tabConfig[currentMethod as keyof typeof tabConfig]
      ? tabConfig[currentMethod as keyof typeof tabConfig].component
      : null;

  return (
    <Fade in timeout={450} key={`step-${currentStepIndex}-tab-${selectedTab}`}>
      <Card
        sx={{
          width: '100%',
          maxWidth: 480,
          mx: 'auto',
          mt: 4,
          backdropFilter: 'blur(6px)',
          bgcolor: (theme1) => alpha(theme1.palette.background.paper, 0.9),
          boxShadow: (theme1) => `0 0 2px ${alpha(theme1.palette.grey[500], 0.2)}, 
                              0 12px 24px -4px ${alpha(theme1.palette.grey[500], 0.12)}`,
        }}
      >
        <CardContent sx={{ pt: 3, pb: 5 }}>
          {/* Header with back button */}
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 4 }}>
            <Tooltip title="Back to email">
              <IconButton
                onClick={handleBack}
                sx={{
                  mr: 2,
                  color: 'text.secondary',
                  '&:hover': {
                    bgcolor: (theme1) => alpha(theme1.palette.primary.main, 0.08),
                  },
                }}
              >
                <Iconify icon={arrowBackIcon} />
              </IconButton>
            </Tooltip>

            <Box>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                Sign in
              </Typography>
              <Typography variant="body2" sx={{ color: 'text.secondary', mt: 0.5 }}>
                {emailFromStore}
              </Typography>
            </Box>
          </Box>

          {/* Error message */}
          {error && (
            <Grow in>
              <Alert
                severity="error"
                onClose={() => setError('')}
                sx={{
                  mb: 3,
                  '& .MuiAlert-message': { width: '100%' },
                }}
              >
                {error}
              </Alert>
            </Grow>
          )}

          {/* Tab-based Authentication UI - Both steps use the same UI pattern */}
          {tabMethods.length > 0 && (
            <>
              {/* Tab navigation - Always show tabs if methods exist */}
              <Tabs
                value={selectedTab}
                onChange={handleTabChange}
                variant="fullWidth"
                aria-label="auth methods tabs"
                sx={{
                  mb: 4,
                  '& .MuiTab-root': {
                    minHeight: 48,
                    textTransform: 'none',
                    flexDirection: 'row',
                    fontWeight: 600,
                    color: 'text.secondary',
                    '&.Mui-selected': {
                      color: 'primary.main',
                    },
                    '& .MuiTab-iconWrapper': {
                      mr: 1,
                    },
                  },
                  '& .MuiTabs-indicator': {
                    bgcolor: 'primary.main',
                  },
                }}
              >
                {tabMethods.map((method) => {
                  const config = tabConfig[method as keyof typeof tabConfig];
                  return (
                    <Tab
                      key={method}
                      icon={<Iconify icon={config.icon} width={22} />}
                      label={config.label}
                      iconPosition="start"
                    />
                  );
                })}
              </Tabs>

              {/* Authentication component */}
              {CurrentAuthComponent && (
                <Box sx={{ position: 'relative' }}>
                  <CurrentAuthComponent
                    email={emailFromStore}
                    onNextStep={handleNextAuthStep}
                    onAuthComplete={handleAuthComplete}
                    onForgotPassword={handleForgotPassword}
                  />
                </Box>
              )}
            </>
          )}

          {/* Social login buttons */}
          {socialMethods.length > 0 && (
            <>
              {tabMethods.length > 0 && (
                <Divider sx={{ my: 4 }}>
                  <Typography variant="body2" sx={{ color: 'text.disabled', px: 1 }}>
                    OR
                  </Typography>
                </Divider>
              )}

              <Stack spacing={2}>
                {socialMethods.map((method) => {
                  const config = socialConfig[method as keyof typeof socialConfig];

                  if (method === 'google') {
                    const clientId = currentStep?.authProviders?.google?.clientId;

                    if (!clientId) {
                      return null;
                    }

                    return (
                      <GoogleOAuthProvider key={method} clientId={clientId}>
                        <Box sx={{ width: '100%' }}>
                          <GoogleLogin
                            onSuccess={handleGoogleLoginSuccess}
                            onError={() => {
                              setError('Google login failed. Please try again.');
                            }}
                            useOneTap
                            type="standard"
                            theme="outline"
                            size="large"
                            width="100%"
                            logo_alignment="center"
                            shape="rectangular"
                            text="continue_with"
                          />
                        </Box>
                      </GoogleOAuthProvider>
                    );
                  }
                  if (method === 'microsoft' || method === 'azureAd') {
                    // Get the appropriate client ID and authority from providers
                    let clientId;
                    let authority;

                    if (method === 'microsoft') {
                      clientId = currentStep?.authProviders?.microsoft?.clientId;
                      authority =
                        currentStep?.authProviders?.microsoft?.authority ||
                        'https://login.microsoftonline.com/common';
                    } else {
                      // azureAd
                      clientId = currentStep?.authProviders?.azuread?.clientId;
                      authority =
                        currentStep?.authProviders?.azureAd?.authority ||
                        'https://login.microsoftonline.com/common';
                    }

                    if (!clientId) {
                      return null;
                    }

                    return (
                      <MicrosoftLoginButton
                        key={method}
                        config={config}
                        method={method}
                        clientId={clientId}
                        authority={authority}
                        onSuccess={handleMsalLoginSuccess}
                        onError={(errorMessage) => setError(errorMessage)}
                      />
                    );
                  }

                  if (method === 'oauth') {
                    const oauthConfig = currentStep?.authProviders?.oauth;
                    
                    if (!oauthConfig) {
                      return null;
                    }

                    return (
                      <OAuthSignIn
                        key={method}
                        email={emailFromStore}
                        authConfig={oauthConfig}
                        onSuccess={handleOAuthLoginSuccess}
                        onError={(errorMessage) => setError(errorMessage)}
                      />
                    );
                  }

                  return null;
                })}
              </Stack>
            </>
          )}
        </CardContent>
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
      </Card>
    </Fade>
  );
};

LazyOtpSignIn.propTypes = {
  email: PropTypes.string.isRequired,
};
