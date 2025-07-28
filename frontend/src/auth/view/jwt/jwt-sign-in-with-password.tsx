import type { Theme, SxProps } from '@mui/material';

import { z as zod } from 'zod';
import { useForm } from 'react-hook-form';
import { useDispatch } from 'react-redux';
import { useState, useEffect } from 'react';
import eyeIcon from '@iconify-icons/solar/eye-bold';
import { zodResolver } from '@hookform/resolvers/zod';
import eyeClosedIcon from '@iconify-icons/solar/eye-closed-bold';

import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import Alert from '@mui/material/Alert';
import IconButton from '@mui/material/IconButton';
import LoadingButton from '@mui/lab/LoadingButton';
import InputAdornment from '@mui/material/InputAdornment';

import { useRouter } from 'src/routes/hooks';

import { useBoolean } from 'src/hooks/use-boolean';

import { setEmail } from 'src/store/auth-slice';

import { Iconify } from 'src/components/iconify';
import { Form, Field } from 'src/components/hook-form';

import { useAuthContext } from '../../hooks';
import { signInWithPassword } from '../../context/jwt';
// ----------------------------------------------------------------------

export const SignInSchema = zod.object({
  email: zod
    .string()
    .min(1, { message: 'Email is required!' })
    .email({ message: 'Email must be a valid email address!' }),
  password: zod.string(),
});

type SignInSchemaType = zod.infer<typeof SignInSchema>;

interface ErrorResponse {
  errorMessage: string;
}

interface PasswordSignInProps {
  defaultEmail?: string;
  onForgotPassword: () => void;
  sx?: SxProps<Theme>;
}

// ----------------------------------------------------------------------

export default function PasswordSignIn({
  defaultEmail,
  onForgotPassword,
  sx,
}: PasswordSignInProps) {
  const router = useRouter();
  const dispatch = useDispatch();

  const { checkUserSession } = useAuthContext();

  const [errorMsg, setErrorMsg] = useState<string>('');

  const password = useBoolean();

  const methods = useForm<SignInSchemaType>({
    resolver: zodResolver(SignInSchema),
    defaultValues: {
      email: defaultEmail || '', // Use defaultEmail if provided
      password: '',
    },
  });

  const {
    handleSubmit,
    watch,
    formState: { isSubmitting },
  } = methods;

  // Update Redux when email changes in form
  useEffect(() => {
    const subscription = watch((value, { name }) => {
      if (name === 'email') {
        dispatch(setEmail(value.email || ''));
      }
    });
    return () => subscription.unsubscribe();
  }, [watch, dispatch]);

  const onSubmit = handleSubmit(async (data: SignInSchemaType): Promise<void> => {
    try {
      await signInWithPassword({ email: data.email, password: data.password });

      await checkUserSession?.();

      router.refresh();
    } catch (error) {
      setErrorMsg(typeof error === 'string' ? error : (error as ErrorResponse).errorMessage);
    }
  });

  const renderForm = (
    <Box gap={0.5} display="flex" flexDirection="column">
      <Field.Text name="email" label="Email address" InputLabelProps={{ shrink: true }} />

      <Box gap={3} display="flex" flexDirection="column">
        <Link
          variant="body2"
          color="inherit"
          onClick={onForgotPassword}
          sx={{ alignSelf: 'flex-end', cursor: 'pointer' }}
        >
          Forgot Password
        </Link>

        <Field.Text
          name="password"
          label="Password"
          type={password.value ? 'text' : 'password'}
          InputLabelProps={{ shrink: true }}
          InputProps={{
            endAdornment: (
              <InputAdornment position="end">
                <IconButton onClick={password.onToggle} edge="end">
                  <Iconify icon={password.value ? eyeIcon : eyeClosedIcon} />
                </IconButton>
              </InputAdornment>
            ),
          }}
        />
      </Box>

      <LoadingButton
        fullWidth
        color="inherit"
        size="large"
        type="submit"
        variant="contained"
        loading={isSubmitting}
        loadingIndicator="Sign in..."
        sx={{ mt: 4 }}
      >
        Sign in
      </LoadingButton>
    </Box>
  );

  return (
    <>
      {!!errorMsg && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {errorMsg}
        </Alert>
      )}

      <Form methods={methods} onSubmit={onSubmit}>
        {renderForm}
      </Form>
    </>
  );
}
