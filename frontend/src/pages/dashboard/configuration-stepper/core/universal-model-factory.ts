import { z } from 'zod';
import type { IconifyIcon } from '@iconify/react';
import { FIELD_TEMPLATES } from "../config/field-templates";
import { EMBEDDING_PROVIDERS, LLM_PROVIDERS, SMTP_PROVIDERS, STORAGE_PROVIDERS, URL_PROVIDERS } from "../config/providers";

interface BaseFieldConfig {
  name: string;
  label: string;
  placeholder?: string;
  type?: 'text' | 'password' | 'email' | 'number' | 'url' | 'select' | 'checkbox' | 'file';
  icon?: string | IconifyIcon;
  required?: boolean;
  validation?: z.ZodType;
  gridSize?: { xs?: number; sm?: number; md?: number };
  options?: Array<{ value: string; label: string }>;
  multiline?: boolean;
  rows?: number;
  // File upload specific props
  acceptedFileTypes?: string[];
  maxFileSize?: number;
  fileProcessor?: (data: any) => any;
  // Conditional validation
  conditionalValidation?: (formData: any) => z.ZodType;
}

interface ProviderDefinition {
  id: string;
  label: string;
  description?: string;
  modelPlaceholder?: string;
  fields?: readonly (keyof typeof FIELD_TEMPLATES)[];
  customFields?: Record<string, BaseFieldConfig>;
  customValidation?: (fields: any) => z.ZodType;
  isSpecial?: boolean;
  accountType?: 'individual' | 'business';
}

interface ConfigTypeDefinition {
  type: string;
  providers: readonly ProviderDefinition[];
}

class UniversalConfigFactory {
  static generateProvider(definition: ProviderDefinition) {    
    // Handle special providers (like default)
    if (definition.isSpecial) {
      return {
        id: definition.id,
        label: definition.label,
        description: definition.description || 'Special configuration with no additional fields required.',
        isSpecial: true,
        allFields: [],
        schema: z.object({ 
          modelType: z.literal(definition.id),
          providerType: z.literal(definition.id),
        }),
        defaultValues: { 
          modelType: definition.id,
          providerType: definition.id,
        },
        accountType: definition.accountType,
      };
    }

    // Generate fields from field names
    const allFields = (definition.fields || []).map((fieldName) => {
      // Check for custom field overrides first
      if (definition.customFields && definition.customFields[fieldName]) {
        return definition.customFields[fieldName];
      }
      
      // Use template field
      const template = FIELD_TEMPLATES[fieldName];
      if (!template) {
        console.error(`Field template '${fieldName}' not found for provider '${definition.id}'`);
        throw new Error(`Field template '${fieldName}' not found`);
      }
      return { ...template };
    });

    // Generate Zod schema with custom validation if provided
    if (definition.customValidation) {
      const schema = definition.customValidation({});
      
      const defaultValues: Record<string, any> = {
        modelType: definition.id,
        providerType: definition.id,
      };

      allFields.forEach((field: any) => {
        if (field.type === 'number') {
          defaultValues[field.name] = field.name === 'port' ? 587 : 0;
        } else if (field.type === 'checkbox') {
          defaultValues[field.name] = false;
        } else if (field.type === 'select' && field.options) {
          defaultValues[field.name] = field.options[0]?.value || '';
        } else {
          defaultValues[field.name] = '';
        }
      });

      return {
        id: definition.id,
        label: definition.label,
        description: definition.description || `Configure ${definition.label} settings.`,
        modelPlaceholder: definition.modelPlaceholder || '',
        allFields,
        isSpecial: false,
        schema,
        defaultValues,
        accountType: definition.accountType,
      };
    }

    // Standard schema generation
    const schemaFields: Record<string, any> = {
      modelType: z.literal(definition.id),
      providerType: z.literal(definition.id),
    };

    const defaultValues: Record<string, any> = {
      modelType: definition.id,
      providerType: definition.id,
    };

    allFields.forEach((field: any) => {
      if (field.validation) {
        schemaFields[field.name] = field.required !== false 
          ? field.validation 
          : field.validation.optional();
      }
      
      if (field.type === 'number') {
        defaultValues[field.name] = field.name === 'port' ? 587 : 0;
      } else if (field.type === 'checkbox') {
        defaultValues[field.name] = false;
      } else if (field.type === 'select' && field.options) {
        defaultValues[field.name] = field.options[0]?.value || '';
      } else {
        defaultValues[field.name] = '';
      }
    });

    const result = {
      id: definition.id,
      label: definition.label,
      description: definition.description || `Configure ${definition.label} settings.`,
      modelPlaceholder: definition.modelPlaceholder || '',
      allFields,
      isSpecial: false,
      schema: z.object(schemaFields),
      defaultValues,
      accountType: definition.accountType,
    };

    return result;
  }

  static generateConfigType(providers: readonly ProviderDefinition[]) {
    return providers.map(config => this.generateProvider(config));
  }
}

// AUTO-GENERATED CONFIGS

export const LLM_CONFIG = UniversalConfigFactory.generateConfigType(LLM_PROVIDERS);
export const EMBEDDING_CONFIG = UniversalConfigFactory.generateConfigType(EMBEDDING_PROVIDERS);
export const STORAGE_CONFIG = UniversalConfigFactory.generateConfigType(STORAGE_PROVIDERS);
export const SMTP_CONFIG = UniversalConfigFactory.generateConfigType(SMTP_PROVIDERS);
export const URL_CONFIG = UniversalConfigFactory.generateConfigType(URL_PROVIDERS);


// HELPER FUNCTIONS

export const getLlmProviders = () => LLM_CONFIG;
export const getLlmProviderById = (id: string) => LLM_CONFIG.find(p => p.id === id) || null;

export const getEmbeddingProviders = () => EMBEDDING_CONFIG;
export const getEmbeddingProviderById = (id: string) => EMBEDDING_CONFIG.find(p => p.id === id) || null;

export const getStorageProviders = () => STORAGE_CONFIG;
export const getStorageProviderById = (id: string) => STORAGE_CONFIG.find(p => p.id === id) || null;

export const getUrlProviders = () => URL_CONFIG;
export const getUrlProviderById = (id: string) => URL_CONFIG.find(p => p.id === id) || null;

export const getSmtpProviders = () => SMTP_CONFIG;
export const getSmtpProviderById = (id: string) => SMTP_CONFIG.find(p => p.id === id) || null;

// TYPE DEFINITIONS

export interface BaseFormValues {
  modelType?: string;
  providerType: string;
  _provider?: string;
}

export interface LlmFormValues extends BaseFormValues {
  apiKey?: string;
  model?: string;
  endpoint?: string;
  deploymentName?: string;
}

export interface EmbeddingFormValues extends BaseFormValues {
  apiKey?: string;
  model?: string;
  endpoint?: string;
}

export interface StorageFormValues extends BaseFormValues {
  // S3 fields
  s3AccessKeyId?: string;
  s3SecretAccessKey?: string;
  s3Region?: string;
  s3BucketName?: string;
  // Azure Blob fields
  accountName?: string;
  accountKey?: string;
  containerName?: string;
  endpointProtocol?: 'http' | 'https';
  endpointSuffix?: string;
  // Local storage fields
  mountName?: string;
  baseUrl?: string;
  // General storage type
  storageType?: string;
}

export interface UrlFormValues extends BaseFormValues {
  frontendUrl?: string;
  connectorUrl?: string;
}

export interface SmtpFormValues extends BaseFormValues {
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  fromEmail?: string;
}