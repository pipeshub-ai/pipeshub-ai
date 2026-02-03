import { useEffect, useCallback, RefObject } from 'react';
import type { Connector } from '../types/types';

/**
 * Configuration for error autoscroll behavior
 */
interface ErrorAutoscrollConfig {
  saveAttempted: boolean;
  formErrors: {
    auth?: Record<string, string>;
    filters?: Record<string, string>;
    sync?: Record<string, string>;
  };
  instanceNameError: string | null;
  adminEmailError: string | null;
  fileError: string | null;
  certificateError: string | null;
  privateKeyError: string | null;
  activeStep: number;
  authOnly: boolean;
  syncSettingsMode: boolean;
  enableMode: boolean;
  syncOnly: boolean;
  isNoAuthType: boolean;
  hasFilters: boolean;
  connector: Connector;
  isBusiness: boolean;
  scrollContainerRef: RefObject<HTMLDivElement>;
  authSectionRef: RefObject<HTMLDivElement>;
  filtersSectionRef: RefObject<HTMLDivElement>;
  syncSectionRef: RefObject<HTMLDivElement>;
  sharepointSectionRef: RefObject<HTMLDivElement>;
  businessOAuthSectionRef: RefObject<HTMLDivElement>;
}

/**
 * Error field ID mapping configuration
 */
interface ErrorFieldMapping {
  sharepoint: {
    clientId: string;
    tenantId: string;
    sharepointDomain: string;
    hasAdminConsent: string;
    certificate: string;
    privateKey: string;
  };
  businessOAuth: {
    adminEmail: string;
    file: string;
  };
  generic: {
    instanceName: string;
  };
}

const ERROR_FIELD_IDS: ErrorFieldMapping = {
  sharepoint: {
    clientId: 'sharepoint-client-id',
    tenantId: 'sharepoint-tenant-id',
    sharepointDomain: 'sharepoint-domain',
    hasAdminConsent: 'sharepoint-admin-consent',
    certificate: 'certificate-upload-section',
    privateKey: 'private-key-upload-section',
  },
  businessOAuth: {
    adminEmail: 'business-oauth-admin-email',
    file: 'business-oauth-file-upload',
  },
  generic: {
    instanceName: 'auth-instance-name-section',
  },
};

const ERROR_CSS_SELECTORS = [
  '.MuiFormHelperText-root.Mui-error',
  '.MuiFormControl-root.Mui-error',
  '.MuiOutlinedInput-root.Mui-error',
  '[aria-invalid="true"]',
] as const;

const SCROLL_CONFIG = {
  DELAY: 100, // ms delay before scrolling
  OFFSET: 100, // pixels from top for better visibility
} as const;

/**
 * Custom hook to automatically scroll to the first validation error field
 * when form submission is attempted.
 * 
 * @param config - Configuration object containing error states, refs, and form mode flags
 */
export const useErrorAutoscroll = (config: ErrorAutoscrollConfig) => {
  const {
    saveAttempted,
    formErrors,
    instanceNameError,
    adminEmailError,
    fileError,
    certificateError,
    privateKeyError,
    activeStep,
    authOnly,
    syncSettingsMode,
    enableMode,
    syncOnly,
    isNoAuthType,
    hasFilters,
    connector,
    isBusiness,
    scrollContainerRef,
    authSectionRef,
    filtersSectionRef,
    syncSectionRef,
    sharepointSectionRef,
    businessOAuthSectionRef,
  } = config;

  /**
   * Check if an error object has any truthy values
   */
  const hasErrors = useCallback((errors: Record<string, string> = {}) => 
    Object.values(errors).some(Boolean),
    []
  );

  /**
   * Check if any validation errors exist in the form
   */
  const hasValidationErrors = useCallback(() => 
    hasErrors(formErrors.auth) ||
    hasErrors(formErrors.filters) ||
    hasErrors(formErrors.sync) ||
    !!instanceNameError || !!adminEmailError || !!fileError || !!certificateError || !!privateKeyError,
    [formErrors, instanceNameError, adminEmailError, fileError, certificateError, privateKeyError, hasErrors]
  );

  /**
   * Detect connector type for specialized error handling
   */
  const isSharePointCertAuth = useCallback(() => 
    connector.type === 'SharePoint Online' &&
    (connector.authType === 'OAUTH_CERTIFICATE' || connector.authType === 'OAUTH_ADMIN_CONSENT'),
    [connector]
  );

  const isGoogleBusinessOAuth = useCallback(() => 
    isBusiness &&
    connector.appGroup === 'Google Workspace' &&
    connector.authType === 'OAUTH' &&
    connector.scope === 'team',
    [isBusiness, connector]
  );

  /**
   * Check if SharePoint section has errors
   */
  const hasSharePointErrors = useCallback(() => {
    if (!isSharePointCertAuth()) return false;
    return !!(certificateError || privateKeyError || hasErrors(formErrors.auth));
  }, [isSharePointCertAuth, certificateError, privateKeyError, formErrors.auth, hasErrors]);

  /**
   * Check if Business OAuth section has errors
   */
  const hasBusinessOAuthErrors = useCallback(() => {
    if (!isGoogleBusinessOAuth()) return false;
    return !!(adminEmailError || fileError);
  }, [isGoogleBusinessOAuth, adminEmailError, fileError]);

  /**
   * Check if Generic Auth section has errors
   */
  const hasGenericAuthErrors = useCallback(() => 
    hasErrors(formErrors.auth) || !!instanceNameError,
    [formErrors.auth, instanceNameError, hasErrors]
  );

  /**
   * Get the first error field ID for SharePoint section
   */
  const getFirstSharePointErrorId = useCallback((): string | null => {
    const authErrors = formErrors.auth || {};
    const { sharepoint } = ERROR_FIELD_IDS;
    
    if (authErrors.clientId) return sharepoint.clientId;
    if (authErrors.tenantId) return sharepoint.tenantId;
    if (authErrors.sharepointDomain) return sharepoint.sharepointDomain;
    if (authErrors.hasAdminConsent) return sharepoint.hasAdminConsent;
    if (certificateError) return sharepoint.certificate;
    if (privateKeyError) return sharepoint.privateKey;
    return null;
  }, [formErrors.auth, certificateError, privateKeyError]);

  /**
   * Get the first error field ID for Business OAuth section
   */
  const getFirstBusinessOAuthErrorId = useCallback((): string | null => {
    const { businessOAuth } = ERROR_FIELD_IDS;
    
    if (adminEmailError) return businessOAuth.adminEmail;
    if (fileError) return businessOAuth.file;
    return null;
  }, [adminEmailError, fileError]);

  /**
   * Get the first error field ID for Generic Auth section
   */
  const getFirstGenericAuthErrorId = useCallback((): string | null => {
    if (instanceNameError) return ERROR_FIELD_IDS.generic.instanceName;
    
    const errorFieldName = Object.keys(formErrors.auth || {}).find(
      key => formErrors.auth?.[key]
    );
    
    return errorFieldName ? `auth-field-${errorFieldName}` : null;
  }, [instanceNameError, formErrors.auth]);

  /**
   * Determine which section contains errors based on form mode and active step
   */
  const determineErrorSection = useCallback((): RefObject<HTMLDivElement> | null => {
    if (authOnly) {
      if (hasSharePointErrors()) return sharepointSectionRef;
      if (hasBusinessOAuthErrors()) return businessOAuthSectionRef;
      if (hasGenericAuthErrors()) return authSectionRef;
      return null;
    }
    
    if (syncSettingsMode || enableMode || syncOnly) {
      if (hasFilters) {
        if (activeStep === 0 && hasErrors(formErrors.filters)) return filtersSectionRef;
        if (activeStep === 1 && hasErrors(formErrors.sync)) return syncSectionRef;
        if (hasErrors(formErrors.filters)) return filtersSectionRef;
      } else if (hasErrors(formErrors.sync)) {
        return syncSectionRef;
      }
      return null;
    }
    
    const hasAuthErrors = hasErrors(formErrors.auth) || !!instanceNameError || 
      !!adminEmailError || !!fileError || !!certificateError || !!privateKeyError;
    
    if (isNoAuthType) {
      if (hasFilters) {
        if (activeStep === 0 && hasErrors(formErrors.filters)) return filtersSectionRef;
        if (activeStep === 1 && hasErrors(formErrors.sync)) return syncSectionRef;
      } else if (hasErrors(formErrors.sync)) {
        return syncSectionRef;
      }
    } else if (hasFilters) {
      if (activeStep === 0 && hasAuthErrors) {
        if (hasSharePointErrors()) return sharepointSectionRef;
        if (hasBusinessOAuthErrors()) return businessOAuthSectionRef;
        return authSectionRef;
      }
      if (activeStep === 1 && hasErrors(formErrors.filters)) return filtersSectionRef;
      if (activeStep === 2 && hasErrors(formErrors.sync)) return syncSectionRef;
    } else {
      if (activeStep === 0 && hasAuthErrors) {
        if (hasSharePointErrors()) return sharepointSectionRef;
        if (hasBusinessOAuthErrors()) return businessOAuthSectionRef;
        return authSectionRef;
      }
      if (activeStep === 1 && hasErrors(formErrors.sync)) return syncSectionRef;
    }
    
    return null;
  }, [
    authOnly,
    syncSettingsMode,
    enableMode,
    syncOnly,
    isNoAuthType,
    hasFilters,
    activeStep,
    formErrors,
    instanceNameError,
    adminEmailError,
    fileError,
    certificateError,
    privateKeyError,
    hasSharePointErrors,
    hasBusinessOAuthErrors,
    hasGenericAuthErrors,
    hasErrors,
    sharepointSectionRef,
    businessOAuthSectionRef,
    authSectionRef,
    filtersSectionRef,
    syncSectionRef,
  ]);

  /**
   * Find the first error element within the target section
   */
  const findFirstErrorElement = useCallback(
    (targetSection: HTMLDivElement, targetSectionRef: RefObject<HTMLDivElement>): HTMLElement | null => {
      let errorFieldId: string | null = null;
      
      if (targetSectionRef === sharepointSectionRef) {
        errorFieldId = getFirstSharePointErrorId();
      } else if (targetSectionRef === businessOAuthSectionRef) {
        errorFieldId = getFirstBusinessOAuthErrorId();
      } else if (targetSectionRef === authSectionRef) {
        errorFieldId = getFirstGenericAuthErrorId();
      }
      
      // Try to find element by ID
      if (errorFieldId) {
        let element = targetSection.querySelector(`#${errorFieldId}`);
        if (element) return element as HTMLElement;
        
        // For generic auth, search within nested container
        if (targetSectionRef === authSectionRef) {
          const genericAuthSection = targetSection.querySelector('#generic-auth-section');
          if (genericAuthSection) {
            element = genericAuthSection.querySelector(`#${errorFieldId}`);
            if (element) return element as HTMLElement;
          }
        }
      }
      
      // Fallback: Find first element with error styling
      const foundElement = ERROR_CSS_SELECTORS.reduce<HTMLElement | null>((result, selector) => {
        if (result) return result;
        
        const elements = targetSection.querySelectorAll(selector);
        if (elements.length > 0) {
          const element = elements[0] as HTMLElement;
          const parent = element.closest('[id]');
          return (parent || element) as HTMLElement;
        }
        return null;
      }, null);
      
      return foundElement || targetSection;
    },
    [
      getFirstSharePointErrorId,
      getFirstBusinessOAuthErrorId,
      getFirstGenericAuthErrorId,
      sharepointSectionRef,
      businessOAuthSectionRef,
      authSectionRef,
    ]
  );

  /**
   * Perform the scroll to error element
   */
  const scrollToError = useCallback(() => {
    const targetSectionRef = determineErrorSection();
    if (!targetSectionRef?.current || !scrollContainerRef.current) return;
    
    const scrollContainer = scrollContainerRef.current;
    const targetSection = targetSectionRef.current;
    
    const errorElement = findFirstErrorElement(targetSection, targetSectionRef);
    if (!errorElement) return;
    
    setTimeout(() => {
      const containerRect = scrollContainer.getBoundingClientRect();
      const elementRect = errorElement.getBoundingClientRect();
      const scrollTop = scrollContainer.scrollTop + (elementRect.top - containerRect.top) - SCROLL_CONFIG.OFFSET;
      
      scrollContainer.scrollTo({
        top: Math.max(0, scrollTop),
        behavior: 'smooth'
      });
    }, SCROLL_CONFIG.DELAY);
  }, [determineErrorSection, findFirstErrorElement, scrollContainerRef]);

  /**
   * Effect to trigger autoscroll when save is attempted with errors
   */
  useEffect(() => {
    if (!saveAttempted || !hasValidationErrors()) return;
    
    scrollToError();
  }, [saveAttempted, hasValidationErrors, scrollToError]);
};

