export interface IAuthMethod {
  type:
    | 'samlSso'
    | 'otp'
    | 'password'
    | 'google'
    | 'microsoft'
    | 'azureAd'
    | 'oauth'
    | 'github';
}

/** Every method a saved policy may name; edition.ts says which ones this edition offers. */
export enum AuthMethodType {
  SAML_SSO = 'samlSso',
  OTP = 'otp',
  PASSWORD = 'password',
  GOOGLE = 'google',
  MICROSOFT = 'microsoft',
  AZURE_AD = 'azureAd',
  OAUTH = 'oauth',
  GITHUB = 'github',
}
