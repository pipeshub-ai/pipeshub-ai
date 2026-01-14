import { paths } from 'src/routes/paths';

import axios from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { STORAGE_KEY, SESSION_TOKEN_KEY, STORAGE_KEY_REFRESH } from './constant';
import { getAccessTokenFromCookie, getRefreshTokenFromCookie, setTokensInCookies, clearAuthCookies } from './cookie-utils';

// ----------------------------------------------------------------------

export type sessionParams = {
  accessToken: string | null;
  refreshToken: string | null;
};

export function jwtDecode(token: string | null) {
  try {
    if (!token) return null;

    const parts = token.split('.');
    if (parts.length < 2) {
      throw new Error('Invalid token!');
    }

    const base64Url = parts[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const decoded = JSON.parse(atob(base64));

    return decoded;
  } catch (error) {
    throw new Error('Error decoding jwt token',error);
  }
}

// ----------------------------------------------------------------------

export async function isValidToken(accessToken: string | null): Promise<boolean> {
  if (!accessToken) {
    // Try to get from cookie
    accessToken = getAccessTokenFromCookie();
  }
  
  if (!accessToken) {
    return false;
  }

  try {
    const decoded = jwtDecode(accessToken);

    if (!decoded || !('exp' in decoded)) {
      return false;
    }

    const currentTime = Date.now() / 1000;

    if (decoded.exp < currentTime) {
      // Token is expired, try to refresh it
      const refreshToken = getRefreshTokenFromCookie();
      if (!refreshToken) return false;
      
      try {
        const res = await axios.post(
          `${CONFIG.authUrl}/api/v1/userAccount/refresh/token`,
          {},
          {
            headers: {
              Authorization: `Bearer ${refreshToken}`,
            },
          }
        );
        
        // Update both tokens from the response
        if (res.data.accessToken) {
          // If backend returns a new refresh token, use it; otherwise keep the existing one
          const newRefreshToken = res.data.refreshToken || refreshToken;
          setSession(res.data.accessToken, newRefreshToken);
          return true;
        }
        
        return false;
      } catch (error) {
        return false;
      }
    }

    return true;
  } catch (error) {
    return false;
  }
}

// ----------------------------------------------------------------------

export function tokenExpired(exp: number): void {
  const currentTime = Date.now();
  const timeLeft = exp * 1000 - currentTime;
  const refreshTime = timeLeft - 2 * 60 * 1000; // Refresh 2 minutes before expiry

  setTimeout(async () => {
    const refreshToken = getRefreshTokenFromCookie();

    if (!refreshToken) {
      console.error('No refresh token found. Unable to refresh access token');
      alert('Session expired, please sign in again');
      clearAuthCookies();
      window.location.href = paths.auth.jwt.signIn;
      return;
    }
    
    try {
      const res = await axios.post(
        `${CONFIG.authUrl}/api/v1/userAccount/refresh/token`,
        {},
        {
          headers: {
            Authorization: `Bearer ${refreshToken}`,
          },
        }
      );
      
      // Update both tokens from the response
      if (res.data.accessToken) {
        // If backend returns a new refresh token, use it; otherwise keep the existing one
        const newRefreshToken = res.data.refreshToken || refreshToken;
        setSession(res.data.accessToken, newRefreshToken);
      }
    } catch (error) {
      alert('Session expired. Please sign in again');
      clearAuthCookies();
      window.location.href = paths.auth.jwt.signIn;
    }
  }, refreshTime);
}

// ----------------------------------------------------------------------

export async function setSessionToken(sessionToken: string | null): Promise<void> {
  try {
    if (sessionToken) {
      sessionStorage.setItem(SESSION_TOKEN_KEY, sessionToken);
      axios.defaults.headers.common['x-session-token'] = sessionToken;
    } else {
      sessionStorage.removeItem(SESSION_TOKEN_KEY);
      delete axios.defaults.headers.common['x-session-token'];
    }
  } catch (error) {
    throw new Error('Error setting session token');
  }
}

export async function setSession(accessToken: string | null, refreshToken: string | null): Promise<void> {
  try {
    if (accessToken && refreshToken) {
      // Store tokens in cookies
      setTokensInCookies(accessToken, refreshToken);
      
      // IMPORTANT: Set axios default header so all requests include the token
      axios.defaults.headers.common.Authorization = `Bearer ${accessToken}`;

      const decodedToken = jwtDecode(accessToken);

      if (decodedToken && 'exp' in decodedToken) {
        tokenExpired(decodedToken.exp);
      } else {
        throw new Error('Invalid access token!');
      }
    } else {
      // Clear cookies and auth header
      clearAuthCookies();
      delete axios.defaults.headers.common.Authorization;
    }
  } catch (error) {
    throw new Error('Error during set session');
  }
}
