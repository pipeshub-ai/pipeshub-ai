import type { Theme, SxProps } from '@mui/material';

import { z as zod } from 'zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import LoadingButton from '@mui/lab/LoadingButton';
import { Snackbar, Typography } from '@mui/material';

import { useRouter } from 'src/routes/hooks';
import { useTurnstile } from 'src/hooks/use-turnstile';

import { CONFIG } from 'src/config-global';

import { Form, Field } from 'src/components/hook-form';
import { TurnstileWidget } from 'src/components/turnstile/turnstile-widget';

import { forgotPassword } from '../../context/jwt';
import { FormHead } from '../../components/form-head';

// ----------------------------------------------------------------------

export const ForgotPasswordSchema = zod.object({
  email: zod
    .string()
    .min(1, { message: 'Email is required!' })
    .email({ message: 'Email must be a valid email address!' }),
});

type ForgotPasswordSchemaType = zod.infer<typeof ForgotPasswordSchema>;


interface ForgotPasswordProps {
  sx?: SxProps<Theme>;
  onBackToSignIn: () => void;
}

// ----------------------------------------------------------------------

export default function ForgotPassword({ onBackToSignIn, sx } : ForgotPasswordProps) {
  const router = useRouter();

  const [errorMsg, setErrorMsg] = useState<string>('');
  const [openSnackBar, setOpenSnackBar] = useState<boolean>(false);
  const [successMsg, setSuccessMsg] = useState<string>('');
  const { turnstileToken, handleSuccess, handleError, handleExpire } = useTurnstile();

  const methods = useForm<ForgotPasswordSchemaType>({
    resolver: zodResolver(ForgotPasswordSchema),
  });

  const {
    handleSubmit,
    formState: { isSubmitting },
  } = methods;

  const onSubmit = handleSubmit(async (data : ForgotPasswordSchemaType) : Promise<void> => {
    try {
      await forgotPassword({ email: data.email, turnstileToken });
      setSuccessMsg('Reset Password email sent successfully');
      setOpenSnackBar(true);
      setErrorMsg('');

      setTimeout(() => {
        router.refresh();
      }, 3000);
    } catch (error) {
      setErrorMsg(typeof error === 'string' ? error : error.errorMessage);
    }
  });

  const handleSnackBarClose = () => {
    setOpenSnackBar(false);
  };

  const renderForm = (
    <Box gap={3} display="flex" flexDirection="column">
      <Field.Text name="email" label="Email address" InputLabelProps={{ shrink: true }} />

      {CONFIG.turnstileSiteKey && (
        <TurnstileWidget
          siteKey={CONFIG.turnstileSiteKey}
          onSuccess={handleSuccess}
          onError={handleError}
          onExpire={handleExpire}
          size="invisible"
        />
      )}

      <LoadingButton
        fullWidth
        color="inherit"
        size="large"
        type="submit"
        variant="contained"
        loading={isSubmitting}
        disabled={!turnstileToken && !!CONFIG.turnstileSiteKey}
        loadingIndicator="Sending request..."
      >
        {CONFIG.turnstileSiteKey && !turnstileToken ? 'Verifying...' : 'Send request'}
      </LoadingButton>
    </Box>
  );

  return (
    <>
      <FormHead
        title="Forgot your password?"
        description="Enter your email to recieve the verification link needed to reset your password"
        sx={{ textAlign: { xs: 'center', md: 'left' } }}
      />

      {!!errorMsg && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {errorMsg}
        </Alert>
      )}

      <Form methods={methods} onSubmit={onSubmit}>
        {renderForm}
      </Form>
      <Box
        display="flex"
        alignItems="center"
        justifyContent="center"
        mt={3}
        onClick={onBackToSignIn}
        sx={{ cursor: 'pointer' }}
      >
        {/* <ArrowBackIosIcon sx={{ mr: 1, fontSize: 15 }} /> */}
        <Typography>Return to sign in</Typography>
      </Box>
      <Snackbar
        open={openSnackBar}
        // autoHideDuration={3000} // Automatically close after 3 seconds
        onClose={handleSnackBarClose}
        message={successMsg}
      />
    </>
  );
}
