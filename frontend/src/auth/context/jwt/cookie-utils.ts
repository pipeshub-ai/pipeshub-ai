import Cookies from 'js-cookie';
import { jwtDecode } from './jwt-decode';

// Cookie names
export const ACCESS_TOKEN_COOKIE = 'access_token';
export const REFRESH_TOKEN_COOKIE = 'refresh_token';

// Set tokens in cookies
export const setTokensInCookies = (accessToken: string, refreshToken: string): void => {
  // Decode tokens to get expiry times
  const accessTokenDecoded = jwtDecode(accessToken);
  const refreshTokenDecoded = jwtDecode(refreshToken);
  
  // Set access token cookie with actual token expiry
  if (accessTokenDecoded && accessTokenDecoded.exp) {
    const accessTokenExpiry = new Date(accessTokenDecoded.exp * 1000); // Convert from seconds to milliseconds
    Cookies.set(ACCESS_TOKEN_COOKIE, accessToken, { 
      expires: accessTokenExpiry,
      secure: window.location.protocol === 'https:',
      sameSite: 'strict',
      path: '/' // Explicitly set path to root
    });
  }
  
  // Set refresh token cookie with actual token expiry
  if (refreshTokenDecoded && refreshTokenDecoded.exp) {
    const refreshTokenExpiry = new Date(refreshTokenDecoded.exp * 1000); // Convert from seconds to milliseconds
    Cookies.set(REFRESH_TOKEN_COOKIE, refreshToken, { 
      expires: refreshTokenExpiry,
      secure: window.location.protocol === 'https:',
      sameSite: 'strict',
      path: '/' // Explicitly set path to root
    });
  }
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