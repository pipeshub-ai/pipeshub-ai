import type { Theme, SxProps } from '@mui/material';

import { z as zod } from 'zod';
import { useForm } from 'react-hook-form';
import { useRef, useState, useEffect } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import otpIcon from '@iconify-icons/mdi/cellphone-message';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Stack from '@mui/material/Stack';
import Button from '@mui/material/Button';
import Typography from '@mui/material/Typography';
import LoadingButton from '@mui/lab/LoadingButton';
import { alpha, styled } from '@mui/material/styles';
import InputAdornment from '@mui/material/InputAdornment';

import { paths } from 'src/routes/paths';
import { useRouter } from 'src/routes/hooks';

import { Iconify } from 'src/components/iconify';
import { Form, Field } from 'src/components/hook-form';

import { useAuthContext } from 'src/auth/hooks';
import { sendOtp, VerifyOtp } from 'src/auth/context/jwt';

// Schema for OTP
const OtpSchema = zod.object({
  otp: zod
    .string()
    .min(1, { message: 'OTP is required!' })
    .length(6, { message: 'OTP must be 6 digits!' })
    .regex(/^\d+$/, { message: 'OTP must contain only numbers!' }),
});

type OtpSchemaType = zod.infer<typeof OtpSchema>;

interface ErrorResponse {
  errorMessage: string;
}

interface OtpSignInProps {
  email: string;
  initialOtpSent?: boolean; // Flag to indicate OTP was already sent
  onNextStep?: (response: any) => void;
  onAuthComplete?: () => void | Promise<void>;
  redirectPath?: string;
  sx?: SxProps<Theme>;
}

// Styled components
const StyledRoot = styled(Box)(({ theme }) => ({
  width: '100%',
  borderRadius: theme.shape.borderRadius * 2,
}));

export default function OtpSignIn({
  email,
  initialOtpSent = false,
  onNextStep,
  onAuthComplete,
  redirectPath = paths.dashboard.root,
  sx,
}: OtpSignInProps) {
  const router = useRouter();
  const { checkUserSession } = useAuthContext();

  const [countdown, setCountdown] = useState(60);
  const [otpSent, setOtpSent] = useState(initialOtpSent);
  const [resending, setResending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [sendingInitial, setSendingInitial] = useState(false);
  const initialSendAttempted = useRef(initialOtpSent);

  const methods = useForm<OtpSchemaType>({
    resolver: zodResolver(OtpSchema),
    defaultValues: {
      otp: '',
    },
  });

  const {
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
    clearErrors,
  } = methods;

  // Send OTP only once on component mount and only if not already sent
  useEffect(() => {
    // Only try to send OTP if we haven't tried already and initialOtpSent is false
    if (!initialSendAttempted.current && !otpSent && !sendingInitial && !initialOtpSent) {
      const sendInitialOtp = async () => {
        initialSendAttempted.current = true;
        setSendingInitial(true);
        try {
          await sendOtp({ email });
          setOtpSent(true);
        } catch (error) {
          setError('root.serverError', {
            type: 'server',
            message: 'Failed to send OTP. Please try again.',
          });
        } finally {
          setSendingInitial(false);
        }
      };

      sendInitialOtp();
    }
  }, [email, otpSent, setError, sendingInitial, initialOtpSent]);

  // Countdown timer after sending OTP
  useEffect(() => {
    if (otpSent && countdown > 0) {
      const timer = setTimeout(() => {
        setCountdown(countdown - 1);
      }, 1000);
      return () => clearTimeout(timer);
    }
    return undefined; // Explicit return for when the condition isn't met
  }, [otpSent, countdown]);

  const handleResendOtp = async () => {
    if (resending) return;

    setResending(true);
    try {
      await sendOtp({ email });
      setOtpSent(true);
      setCountdown(60);
    } catch (error) {
      setError('root.serverError', {
        type: 'server',
        message: 'Failed to resend OTP. Please try again.',
      });
    } finally {
      setResending(false);
    }
  };

  const onSubmit = async (data: OtpSchemaType) => {
    if (verifying) return;

    setVerifying(true);
    try {
      const response = await VerifyOtp({
        email,
        otp: data.otp,
      });

      // Check the response
      if (response && response.nextStep !== undefined && onNextStep) {
        // We need to move to the next authentication step
        onNextStep(response);
      } else {
        // Authentication is complete
        await checkUserSession?.();
        // router.refresh();
        if (onAuthComplete) {
          await onAuthComplete();
        } else {
          // Navigate to specified redirect path after successful login
          router.push('/');
        }
      }
    } catch (error) {
      const errorMessage =
        typeof error === 'string' ? error : (error as ErrorResponse)?.errorMessage;

      setError('root.serverError', {
        type: 'server',
        message: errorMessage || 'OTP verification failed. Please try again.',
      });
    } finally {
      setVerifying(false);
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

          {/* OTP sent confirmation */}
          <Alert
            severity="info"
            variant="outlined"
            sx={{
              bgcolor: (theme) => alpha(theme.palette.info.main, 0.08),
              '& .MuiAlert-message': {
                width: '100%',
              },
            }}
          >
            <Typography variant="body2">
              {sendingInitial ? (
                'Sending one-time password to your email...'
              ) : (
                <>
                  A one-time password has been sent to your email address: <strong>{email}</strong>
                </>
              )}
            </Typography>
          </Alert>

          {/* OTP input field */}
          <Field.Text
            name="otp"
            label="Enter 6-digit OTP"
            autoFocus
            disabled={sendingInitial}
            InputLabelProps={{ shrink: true }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon={otpIcon} width={24} sx={{ color: 'text.secondary' }} />
                </InputAdornment>
              ),
            }}
          />

          {/* Resend OTP option */}
          <Box sx={{ display: 'flex', justifyContent: 'center', mt: 1 }}>
            {countdown > 0 ? (
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                Resend OTP in {countdown} seconds
              </Typography>
            ) : (
              <Button
                variant="text"
                onClick={handleResendOtp}
                disabled={resending || sendingInitial}
                sx={{ textTransform: 'none' }}
              >
                {resending ? 'Sending...' : 'Resend OTP'}
              </Button>
            )}
          </Box>

          {/* Submit button */}
          <LoadingButton
            fullWidth
            size="large"
            type="submit"
            variant="contained"
            loading={isSubmitting || verifying || sendingInitial}
            disabled={sendingInitial}
            sx={{ mt: 2 }}
          >
            Verify OTP
          </LoadingButton>
        </Stack>
      </Form>
    </StyledRoot>
  );
}
