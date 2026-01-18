import { useState, useEffect, useCallback } from 'react';

import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import Alert from '@mui/material/Alert';
import Stack from '@mui/material/Stack';
import Button from '@mui/material/Button';
import Divider from '@mui/material/Divider';
import Typography from '@mui/material/Typography';
import CircularProgress from '@mui/material/CircularProgress';

import { paths } from 'src/routes/paths';
import { useRouter, useSearchParams } from 'src/routes/hooks';

import { CONFIG } from 'src/config-global';

import { useAuthContext } from 'src/auth/hooks';

// ----------------------------------------------------------------------

interface ScopeInfo {
  name: string;
  description: string;
  category: string;
}

interface AppInfo {
  name: string;
  description?: string;
  logoUrl?: string;
  homepageUrl?: string;
  privacyPolicyUrl?: string;
}

interface ConsentData {
  app: AppInfo;
  scopes: ScopeInfo[];
  user: {
    email: string;
    name?: string;
  };
  redirectUri: string;
  state?: string;
}

interface AuthorizeResponse {
  requiresConsent?: boolean;
  consentData?: ConsentData;
  codeChallenge?: string;
  codeChallengeMethod?: string;
  redirectUrl?: string;
  error?: string;
  error_description?: string;
}

// ----------------------------------------------------------------------

export function OAuthAuthorizeView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { authenticated, loading: authLoading } = useAuthContext();

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>('');
  const [consentData, setConsentData] = useState<ConsentData | null>(null);
  const [codeChallenge, setCodeChallenge] = useState<string>('');
  const [codeChallengeMethod, setCodeChallengeMethod] = useState<string>('');

  // Get OAuth params from URL
  const clientId = searchParams.get('client_id');
  const redirectUri = searchParams.get('redirect_uri');
  const responseType = searchParams.get('response_type');
  const scope = searchParams.get('scope');
  const state = searchParams.get('state');
  const codeChallengeParam = searchParams.get('code_challenge');
  const codeChallengeMethodParam = searchParams.get('code_challenge_method');

  // Build the full query string for returnTo
  const buildReturnTo = useCallback(() => {
    const params = new URLSearchParams();
    if (clientId) params.set('client_id', clientId);
    if (redirectUri) params.set('redirect_uri', redirectUri);
    if (responseType) params.set('response_type', responseType);
    if (scope) params.set('scope', scope);
    if (state) params.set('state', state);
    if (codeChallengeParam) params.set('code_challenge', codeChallengeParam);
    if (codeChallengeMethodParam) params.set('code_challenge_method', codeChallengeMethodParam);
    return `/oauth/authorize?${params.toString()}`;
  }, [clientId, redirectUri, responseType, scope, state, codeChallengeParam, codeChallengeMethodParam]);

  // Fetch consent data from backend
  const fetchConsentData = useCallback(async () => {
    try {
      setLoading(true);
      setError('');

      // Build query params
      const params = new URLSearchParams();
      if (clientId) params.set('client_id', clientId);
      if (redirectUri) params.set('redirect_uri', redirectUri);
      if (responseType) params.set('response_type', responseType || 'code');
      if (scope) params.set('scope', scope);
      if (state) params.set('state', state);
      if (codeChallengeParam) params.set('code_challenge', codeChallengeParam);
      if (codeChallengeMethodParam) params.set('code_challenge_method', codeChallengeMethodParam);

      const accessToken = localStorage.getItem('jwt_access_token');

      const response = await fetch(
        `${CONFIG.backendUrl}/api/v1/oauth2/authorize?${params.toString()}`,
        {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${accessToken}`,
            'Content-Type': 'application/json',
          },
        }
      );

      const data: AuthorizeResponse = await response.json();

      if (!response.ok) {
        // Handle OAuth errors
        if (data.error) {
          throw new Error(data.error_description || data.error);
        }
        throw new Error('Failed to get authorization data');
      }

      // Check if we need to redirect immediately (e.g., error case)
      if (data.redirectUrl) {
        window.location.href = data.redirectUrl;
        return;
      }

      // Display consent page
      if (data.requiresConsent && data.consentData) {
        setConsentData(data.consentData);
        setCodeChallenge(data.codeChallenge || '');
        setCodeChallengeMethod(data.codeChallengeMethod || '');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  }, [clientId, redirectUri, responseType, scope, state, codeChallengeParam, codeChallengeMethodParam]);

  // Check authentication and fetch data
  useEffect(() => {
    if (authLoading) return;

    if (!authenticated) {
      // Redirect to login with returnTo parameter
      const returnTo = encodeURIComponent(buildReturnTo());
      router.replace(`${paths.auth.jwt.signIn}?returnTo=${returnTo}`);
      return;
    }

    // Validate required params
    if (!clientId || !redirectUri) {
      setError('Missing required OAuth parameters (client_id, redirect_uri)');
      setLoading(false);
      return;
    }

    fetchConsentData();
  }, [authLoading, authenticated, clientId, redirectUri, router, buildReturnTo, fetchConsentData]);

  // Handle consent submission
  const handleConsent = async (consent: 'granted' | 'denied') => {
    try {
      setSubmitting(true);

      const accessToken = localStorage.getItem('jwt_access_token');

      const response = await fetch(`${CONFIG.backendUrl}/api/v1/oauth2/authorize`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          client_id: clientId,
          redirect_uri: redirectUri,
          scope,
          state,
          code_challenge: codeChallenge,
          code_challenge_method: codeChallengeMethod,
          consent,
        }),
      });

      const data = await response.json();

      if (data.redirectUrl) {
        // Redirect to the client's callback URL with the code
        window.location.href = data.redirectUrl;
      } else {
        setError('No redirect URL received from server');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to process consent');
    } finally {
      setSubmitting(false);
    }
  };

  // Loading state
  if (authLoading || loading) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
        }}
      >
        <CircularProgress size={40} sx={{ mb: 2 }} />
        <Typography variant="body1">Loading authorization request...</Typography>
      </Box>
    );
  }

  // Error state
  if (error) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          p: 3,
        }}
      >
        <Alert severity="error" sx={{ mb: 2, maxWidth: 500 }}>
          {error}
        </Alert>
      </Box>
    );
  }

  // No consent data
  if (!consentData) {
    return (
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          p: 3,
        }}
      >
        <Alert severity="warning" sx={{ maxWidth: 500 }}>
          No authorization data available
        </Alert>
      </Box>
    );
  }

  // Consent page
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        p: 3,
        bgcolor: 'background.default',
      }}
    >
      <Card sx={{ maxWidth: 480, width: '100%', p: 4 }}>
        <Stack spacing={3}>
          {/* App Header */}
          <Stack direction="row" spacing={2} alignItems="center">
            {consentData.app.logoUrl ? (
              <Box
                component="img"
                src={consentData.app.logoUrl}
                alt={consentData.app.name}
                sx={{ width: 48, height: 48, borderRadius: 1 }}
              />
            ) : (
              <Box
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: 1,
                  bgcolor: 'primary.main',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'primary.contrastText',
                  fontWeight: 'bold',
                  fontSize: 20,
                }}
              >
                {consentData.app.name.charAt(0).toUpperCase()}
              </Box>
            )}
            <Box>
              <Typography variant="h6">{consentData.app.name}</Typography>
              {consentData.app.description && (
                <Typography variant="body2" color="text.secondary">
                  {consentData.app.description}
                </Typography>
              )}
            </Box>
          </Stack>

          <Divider />

          {/* User Info */}
          <Box>
            <Typography variant="body2" color="text.secondary">
              Signed in as
            </Typography>
            <Typography variant="body1" fontWeight="medium">
              {consentData.user.name || consentData.user.email}
            </Typography>
            {consentData.user.name && (
              <Typography variant="body2" color="text.secondary">
                {consentData.user.email}
              </Typography>
            )}
          </Box>

          <Divider />

          {/* Permissions */}
          <Box>
            <Typography variant="subtitle1" gutterBottom>
              This application is requesting access to:
            </Typography>
            <Stack spacing={1.5} sx={{ mt: 2 }}>
              {consentData.scopes.map((scopeItem) => (
                <Box
                  key={scopeItem.name}
                  sx={{
                    p: 1.5,
                    borderRadius: 1,
                    bgcolor: 'action.hover',
                  }}
                >
                  <Typography variant="body2" fontWeight="medium">
                    {scopeItem.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {scopeItem.description}
                  </Typography>
                </Box>
              ))}
            </Stack>
          </Box>

          <Divider />

          {/* Privacy Notice */}
          <Typography variant="caption" color="text.secondary">
            By clicking &quot;Allow&quot;, you authorize {consentData.app.name} to access your data
            as described above.
            {consentData.app.privacyPolicyUrl && (
              <>
                {' '}
                <a
                  href={consentData.app.privacyPolicyUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Privacy Policy
                </a>
              </>
            )}
          </Typography>

          {/* Action Buttons */}
          <Stack direction="row" spacing={2}>
            <Button
              fullWidth
              variant="outlined"
              color="inherit"
              onClick={() => handleConsent('denied')}
              disabled={submitting}
            >
              Deny
            </Button>
            <Button
              fullWidth
              variant="contained"
              onClick={() => handleConsent('granted')}
              disabled={submitting}
            >
              {submitting ? <CircularProgress size={24} /> : 'Allow'}
            </Button>
          </Stack>
        </Stack>
      </Card>
    </Box>
  );
}
