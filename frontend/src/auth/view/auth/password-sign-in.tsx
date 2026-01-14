import type { Theme, SxProps } from '@mui/material';

import { z as zod } from 'zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import emailIcon from '@iconify-icons/mdi/email';
import eyeIcon from '@iconify-icons/solar/eye-bold';
import { zodResolver } from '@hookform/resolvers/zod';
import eyeClosedIcon from '@iconify-icons/solar/eye-closed-bold';
import lockPasswordIcon from '@iconify-icons/solar/lock-password-bold';

import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import Alert from '@mui/material/Alert';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import { styled } from '@mui/material/styles';
import Typography from '@mui/material/Typography';
import IconButton from '@mui/material/IconButton';
import LoadingButton from '@mui/lab/LoadingButton';
import InputAdornment from '@mui/material/InputAdornment';

import { paths } from 'src/routes/paths';
import { useRouter } from 'src/routes/hooks';

import { useBoolean } from 'src/hooks/use-boolean';

import { Iconify } from 'src/components/iconify';
import { Form, Field } from 'src/components/hook-form';

import { useAuthContext } from 'src/auth/hooks';
import { signInWithPassword } from 'src/auth/context/jwt';

// Schema for sign in
const SignInSchema = zod.object({
  email: zod
    .string()
    .min(1, { message: 'Email is required!' })
    .email({ message: 'Email must be a valid email address!' }),
  password: zod.string().min(1, { message: 'Password is required!' }),
});

type SignInSchemaType = zod.infer<typeof SignInSchema>;

interface ErrorResponse {
  errorMessage: string;
}

// Response type for authentication
interface AuthResponse {
  status?: string;
  nextStep?: number;
  allowedMethods?: string[];
  authProviders?: Record<string, any>;
  accessToken?: string;
  refreshToken?: string;
  message?: string;
}

interface PasswordSignInProps {
  email: string;
  onNextStep?: (response: AuthResponse) => void;
  onAuthComplete?: () => void | Promise<void>;
  onForgotPassword: () => void;
  redirectPath?: string;
  sx?: SxProps<Theme>;
}

// Styled components
const StyledRoot = styled(Box)(({ theme }) => ({
  width: '100%',
  borderRadius: theme.shape.borderRadius * 2,
}));

export default function PasswordSignIn({
  email,
  onNextStep,
  onAuthComplete,
  onForgotPassword,
  redirectPath = paths.dashboard.root,
  sx,
}: PasswordSignInProps) {
  const router = useRouter();
  const { checkUserSession } = useAuthContext();
  const showPassword = useBoolean();
  const [isProcessing, setIsProcessing] = useState(false);

  const methods = useForm<SignInSchemaType>({
    resolver: zodResolver(SignInSchema),
    defaultValues: {
      email: email || '',
      password: '',
    },
  });

  const {
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
    clearErrors,
  } = methods;

  const onSubmit = async (data: SignInSchemaType) => {
    setIsProcessing(true);
    try {
      const response = await signInWithPassword({
        email: data.email,
        password: data.password,
      });

      // Check the response
      if (response) {
        if (response.nextStep && response.allowedMethods && response.allowedMethods.length > 0) {
          // We need to go to the next authentication step
          if (onNextStep) {
            onNextStep(response);
          }
        } else if (response.accessToken && response.refreshToken) {
          // Authentication is complete, proceed with login
          await checkUserSession?.();
          // router.refresh();
          if (onAuthComplete) {
            await onAuthComplete();
          } else {
            // Navigate to specified redirect path after successful login
            router.push('/');
          }
        } else {
          // Unexpected response format
          setError('root.serverError', {
            type: 'server',
            message: 'Unexpected response from the server. Please try again.',
          });
        }
      }
    } catch (error) {
      const errorMessage =
        typeof error === 'string' ? error : (error as ErrorResponse)?.errorMessage;

      setError('root.serverError', {
        type: 'server',
        message: errorMessage || 'Authentication failed. Please try again.',
      });
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <StyledRoot sx={sx}>
      <Form methods={methods} onSubmit={handleSubmit(onSubmit)}>
        <Stack spacing={3}>
          {/* Show server error if any */}
          {errors.root?.serverError && (
            <Alert
              severity="error"
              variant="outlined"
              onClose={() => clearErrors('root.serverError')}
            >
              {errors.root.serverError.message}
            </Alert>
          )}

          {/* Email field (disabled as it's already provided) */}
          <Field.Text
            name="email"
            label="Email address"
            disabled
            InputLabelProps={{ shrink: true }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon={emailIcon} width={24} sx={{ color: 'text.secondary' }} />
                </InputAdornment>
              ),
            }}
          />

          {/* Password field with forgot password link */}
          <Box>
            <Field.Text
              name="password"
              label="Password"
              type={showPassword.value ? 'text' : 'password'}
              InputLabelProps={{ shrink: true }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon={lockPasswordIcon} width={24} sx={{ color: 'text.secondary' }} />
                  </InputAdornment>
                ),
                endAdornment: (
                  <InputAdornment position="end">
                    <Tooltip title={showPassword.value ? 'Hide password' : 'Show password'}>
                      <IconButton onClick={showPassword.onToggle} edge="end">
                        <Iconify icon={showPassword.value ? eyeIcon : eyeClosedIcon} />
                      </IconButton>
                    </Tooltip>
                  </InputAdornment>
                ),
              }}
            />

            <Link
              variant="body2"
              color="inherit"
              onClick={onForgotPassword}
              sx={{
                mt: 1,
                display: 'inline-block',
                cursor: 'pointer',
                '&:hover': {
                  color: 'primary.main',
                  textDecoration: 'none',
                },
              }}
            >
              <Typography variant="caption">Forgot Password?</Typography>
            </Link>
          </Box>

          {/* Submit button */}
          <LoadingButton
            fullWidth
            size="large"
            type="submit"
            variant="contained"
            loading={isSubmitting || isProcessing}
            sx={{ mt: 2 }}
          >
            Continue
          </LoadingButton>
        </Stack>
      </Form>
    </StyledRoot>
  );
}
