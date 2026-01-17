import { useLocation, useNavigate } from 'react-router';
import { useMemo, useEffect, useCallback } from 'react';

import { useSetState } from 'src/hooks/use-set-state';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { AuthContext } from '../auth-context';
import { STORAGE_KEY, STORAGE_KEY_REFRESH } from './constant';
import { jwtDecode, setSession, isValidToken } from './utils';

import type { AuthState } from '../../types';

// ----------------------------------------------------------------------

/**
 * NOTE:
 * We only build demo at basic level.
 * Customer will need to do some extra handling yourself if you want to extend the logic and other features...
 */

type Props = {
  children: React.ReactNode;
};

export function AuthProvider({ children }: Props) {
  const { state, setState } = useSetState<AuthState>({
    user: null,
    loading: true,
  });

  const navigate = useNavigate();
  const path = useLocation();

  const checkUserSession = useCallback(async () => {
    try {
      let accessToken = localStorage.getItem(STORAGE_KEY);
      const refreshToken = localStorage.getItem(STORAGE_KEY_REFRESH);
      if (accessToken && (await isValidToken(accessToken))) {
        accessToken = localStorage.getItem(STORAGE_KEY); // isValidToken might change accesstoken
        setSession(accessToken, refreshToken);
        const decodedToken = jwtDecode(accessToken);
        const { userId } = decodedToken;

        const res = await axios.get(`${CONFIG.authUrl}/api/v1/users/${userId}`);

        const user = res.data;

        setState({
          user: { ...user, accessToken, accountType: decodedToken.accountType },
          loading: false,
        });
      } else {
        setState({ user: null, loading: false });
      }
    } catch (error) {
      setState({ user: null, loading: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    checkUserSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ----------------------------------------------------------------------

  const checkAuthenticated = state.user ? 'authenticated' : 'unauthenticated';

  const status = state.loading ? 'loading' : checkAuthenticated;

  useEffect(() => { 
    if (checkAuthenticated === 'authenticated' && path.pathname === '/auth/sign-in') {
      navigate('/');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkAuthenticated, path.pathname]);

  const memoizedValue = useMemo(
    () => ({
      user: state.user
        ? {
            ...state.user,
            role: state.user?.role ?? 'admin',
          }
        : null,
      checkUserSession,
      loading: status === 'loading',
      authenticated: status === 'authenticated',
      unauthenticated: status === 'unauthenticated',
    }),
    [checkUserSession, state.user, status]
  );

  return <AuthContext.Provider value={memoizedValue}>{children}</AuthContext.Provider>;
}
