import { useEffect, useRef, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
    Container,
    Typography,
    Button,
    CircularProgress,
    Stack,
    Paper,
} from '@mui/material';
import checkCircleIcon from '@iconify-icons/mdi/check-circle-outline';
import { STORAGE_KEY } from 'src/auth/context/jwt/constant';

import errorIcon from '@iconify-icons/mdi/error';
import axios from 'axios';
import { changeEmail } from 'src/auth/context/jwt';
import { Iconify } from 'src/components/iconify';
import { useTheme } from '@mui/material/styles';

export function EmailChangeRedirect() {
    const [searchParams, setSearchParams] = useSearchParams();
    const navigate = useNavigate();
    const theme = useTheme();



    const hasCalledApi = useRef(false);
    const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading');
    const [errorMessage, setErrorMessage] = useState<string>('');

    useEffect(() => {
        const token = searchParams.get('token');

        if (!token) {
            if (status !== 'success') setStatus('error');
            setErrorMessage('Token is missing in url');
            return;
        }

        if (hasCalledApi.current) return;
        hasCalledApi.current = true;

        const validateEmailChange = async () => {
            try {
                await changeEmail({ token });

                setStatus('success');

                localStorage.clear();
                window.location.replace('/auth/sign-in');

            } catch (error) {
                setStatus('error');
                setErrorMessage(error instanceof Error ? error.message : 'An unknown error occurred');
            }
        };

        validateEmailChange();
    }, []);



    return (
        <Container
            maxWidth="sm"
            sx={{
                minHeight: '100vh',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
            }}
        >
            <Paper
                elevation={4}
                sx={{
                    p: 6,
                    borderRadius: "8px",
                    textAlign: 'center',
                    width: '100%',
                }}
            >
                <Stack spacing={3} alignItems="center">
                    {status === 'loading' && (
                        <>
                            <CircularProgress size={60} />
                            <Typography variant="h5">
                                Verifying your email...
                            </Typography>
                        </>
                    )}

                    {status === 'success' && (
                        <>
                            {/* <CheckCircleIcon sx={{ fontSize: 70, color: 'success.main' }} /> */}
                            <Iconify
                                icon={checkCircleIcon}
                                width={50}
                                height={50}
                                sx={{ color: theme.palette.success.main }}
                            />
                            <Typography variant="h4">
                                Email Updated Successfully
                            </Typography>
                            <Typography color="text.secondary">
                                Redirecting to login...
                            </Typography>
                        </>
                    )}

                    {status === 'error' && (
                        <>
                            {/* <ErrorIcon sx={{ fontSize: 70, color: 'error.main' }} /> */}
                            <Iconify
                                icon={errorIcon}
                                width={50}
                                height={50}
                                sx={{ color: theme.palette.error.main }}
                            />
                            <Typography variant="h4">
                                Invalid or Expired Link
                            </Typography>
                            {errorMessage && <Typography variant="body1" sx={{ color: 'text.secondary', mb: 2 }}>
                                {errorMessage}
                            </Typography>}
                            <Button variant="contained" onClick={() => navigate('/')}>
                                Go Home
                            </Button>
                        </>
                    )}
                </Stack>
            </Paper>
        </Container>
    );
}
