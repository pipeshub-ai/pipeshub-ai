import Cookies from 'js-cookie';

// Cookie names
export const ACCESS_TOKEN_COOKIE = 'access_token';
export const REFRESH_TOKEN_COOKIE = 'refresh_token';

// Set tokens in cookies
export const setTokensInCookies = (accessToken: string, refreshToken: string): void => {
  // Set access token cookie (expires in 15 minutes)
  Cookies.set(ACCESS_TOKEN_COOKIE, accessToken, { 
    expires: 1/96, // 15 minutes
    secure: window.location.protocol === 'https:',
    sameSite: 'lax',
    path: '/' // Explicitly set path to root
  });
  
  // Set refresh token cookie (expires in 7 days)
  Cookies.set(REFRESH_TOKEN_COOKIE, refreshToken, { 
    expires: 7,
    secure: window.location.protocol === 'https:',
    sameSite: 'lax',
    path: '/' // Explicitly set path to root
  });
};

// Get access token from cookie
export const getAccessTokenFromCookie = (): string | null => {
  const token = Cookies.get(ACCESS_TOKEN_COOKIE);
  return token || null;
};

// Get refresh token from cookie
export const getRefreshTokenFromCookie = (): string | null => {
  const token = Cookies.get(REFRESH_TOKEN_COOKIE);
  return token || null;
};

// Check if user has valid cookies
export const hasAuthCookies = (): boolean => 
  !!getAccessTokenFromCookie();

// Clear auth cookies
export const clearAuthCookies = (): void => {
  Cookies.remove(ACCESS_TOKEN_COOKIE, { path: '/' });
  Cookies.remove(REFRESH_TOKEN_COOKIE, { path: '/' });
};