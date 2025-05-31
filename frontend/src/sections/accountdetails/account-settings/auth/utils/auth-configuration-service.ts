import axios from 'src/utils/axios';


export interface GoogleAuthConfig {
  clientId: string;
}

export interface AzureAuthConfig {
  clientId: string;
  tenantId: string;
}

export interface MicrosoftAuthConfig {
  clientId: string;
  tenantId: string;
}

export interface SamlSsoConfig {
  entryPoint?: string;     
  certificate?: string;    
  emailKey?: string;        
  logoutUrl?: string;       
  entityId?: string;       
}

export interface OAuthConfig {
  clientId: string;
  clientSecret?: string;
  authorizationUrl?: string;
  tokenEndpoint?: string;
  userInfoEndpoint?: string;
  providerName: string;
  scope?: string;
  redirectUri?: string;
}

// Base API URL
const API_BASE_URL = '/api/v1/configurationManager/authConfig';

/**
 * Fetch Google authentication configuration
 * @returns {Promise<GoogleAuthConfig>} The Google auth configuration
 */
export const getGoogleAuthConfig = async (): Promise<GoogleAuthConfig> => {
  try {
    const response = await axios.get(`${API_BASE_URL}/google`);
    return response.data;
  } catch (error) {
    console.error('Failed to fetch Google auth configuration:', error);
    throw error;
  }
};

/**
 * Fetch Azure AD authentication configuration
 * @returns {Promise<AzureAuthConfig>} The Azure AD auth configuration
 */
export const getAzureAuthConfig = async (): Promise<AzureAuthConfig> => {
  try {
    const response = await axios.get(`${API_BASE_URL}/azureAd`);
    return response.data;
  } catch (error) {
    console.error('Failed to fetch Azure auth configuration:', error);
    throw error;
  }
};

/**
 * Fetch Microsoft authentication configuration
 * @returns {Promise<MicrosoftAuthConfig>} The Microsoft auth configuration
 */
export const getMicrosoftAuthConfig = async (): Promise<MicrosoftAuthConfig> => {
  try {
    const response = await axios.get(`${API_BASE_URL}/microsoft`);
    return response.data;
  } catch (error) {
    console.error('Failed to fetch Microsoft auth configuration:', error);
    throw error;
  }
};

/**
 * Fetch SAML SSO authentication configuration
 * @returns {Promise<SamlSsoConfig>} The SAML SSO auth configuration
 */
export const getSamlSsoConfig = async (): Promise<SamlSsoConfig> => {
  try {
    const response = await axios.get(`${API_BASE_URL}/sso`);
    return response.data;
  } catch (error) {
    console.error('Failed to fetch SAML SSO configuration:', error);
    throw error;
  }
};

/**
 * Fetch OAuth authentication configuration
 * @returns {Promise<OAuthConfig>} The OAuth auth configuration
 */
export const getOAuthConfig = async (): Promise<OAuthConfig> => {
  try {
    const response = await axios.get(`${API_BASE_URL}/oauth`);
    return response.data;
  } catch (error) {
    console.error('Failed to fetch OAuth configuration:', error);
    throw error;
  }
};



/**
 * Update Google auth configuration
 * @param {GoogleAuthConfig} googleConfig - Google auth configuration
 * @returns {Promise<any>} The API response
 */
export const updateGoogleAuthConfig = async (googleConfig: GoogleAuthConfig): Promise<any> => {
  try {
    const response = await axios.post(`${API_BASE_URL}/google`, googleConfig);
    return response.data;
  } catch (error) {
    console.error('Failed to update Google auth configuration:', error);
    throw error;
  }
};

/**
 * Update Azure AD auth configuration
 * @param {AzureAuthConfig} azureConfig - Azure AD auth configuration
 * @returns {Promise<any>} The API response
 */
export const updateAzureAuthConfig = async (azureConfig: AzureAuthConfig): Promise<any> => {
  try {
    const response = await axios.post(`${API_BASE_URL}/azureAd`, azureConfig);
    return response.data;
  } catch (error) {
    console.error('Failed to update Azure AD auth configuration:', error);
    throw error;
  }
};

/**
 * Update Microsoft auth configuration
 * @param {MicrosoftAuthConfig} microsoftConfig - Microsoft auth configuration
 * @returns {Promise<any>} The API response
 */
export const updateMicrosoftAuthConfig = async (microsoftConfig: MicrosoftAuthConfig): Promise<any> => {
  try {
    const response = await axios.post(`${API_BASE_URL}/microsoft`, microsoftConfig);
    return response.data;
  } catch (error) {
    console.error('Failed to update Microsoft auth configuration:', error);
    throw error;
  }
};

/**
 * Update SAML SSO auth configuration
 * @param {SamlSsoConfig} samlConfig - SAML SSO auth configuration
 * @returns {Promise<any>} The API response
 */
export const updateSamlSsoConfig = async (samlConfig: SamlSsoConfig): Promise<any> => {
  try {
    const response = await axios.post(`${API_BASE_URL}/sso`, samlConfig);
    return response.data;
  } catch (error) {
    console.error('Failed to update SAML SSO configuration:', error);
    throw error;
  }
};

/**
 * Update OAuth auth configuration
 * @param {OAuthConfig} oauthConfig - OAuth auth configuration
 * @returns {Promise<any>} The API response
 */
export const updateOAuthConfig = async (oauthConfig: OAuthConfig): Promise<any> => {
  try {
    const response = await axios.post(`${API_BASE_URL}/oauth`, oauthConfig);
    return response.data;
  } catch (error) {
    console.error('Failed to update OAuth configuration:', error);
    throw error;
  }
};

export default {
  getGoogleAuthConfig,
  getAzureAuthConfig,
  getMicrosoftAuthConfig,
  getSamlSsoConfig,
  getOAuthConfig,
  updateGoogleAuthConfig,
  updateAzureAuthConfig,
  updateMicrosoftAuthConfig,
  updateSamlSsoConfig,
  updateOAuthConfig
};