import { useState } from 'react';

import Box from '@mui/material/Box';
import Alert from '@mui/material/Alert';
import Typography from '@mui/material/Typography';
import LoadingButton from '@mui/lab/LoadingButton';

import { CONFIG } from 'src/config-global';

import { SESSION_TOKEN_KEY } from 'src/auth/context/jwt';

interface SamlSignInProps {
  email: string;
  providerName: string;
}

export default function SamlSignIn({ email, providerName }: SamlSignInProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSamlLogin = async () => {
    setLoading(true);
    setError('');
    try {
      // Redirect to SAML entry point
      const sessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
      window.location.href = `${CONFIG.authUrl}/api/v1/saml/signIn?email=${email}&sessionToken=${sessionToken}`;
    } catch (err) {
      setError('Failed to initialize SSO. Please try again.');
      setLoading(false);
    }
  };

  return (
    <Box>
      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {providerName === "SSO" && (<Typography variant="body2" color="text.secondary" mb={3}>
        Continue to sign in with your organization&apos;s SSO provider
      </Typography>)}
      {providerName !== "SSO" && (<Typography variant="body2" color="text.secondary" mb={3}>
        {`Continue to sign in with your ${providerName} account`}
      </Typography>)}

      <LoadingButton
        fullWidth
        size="large"
        variant="contained"
        loading={loading}
        onClick={handleSamlLogin}
      >
        {`Continue with ${providerName}`}
      </LoadingButton>
    </Box>
  );
}
