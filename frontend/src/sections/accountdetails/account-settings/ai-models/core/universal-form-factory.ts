import { z } from 'zod';
import type { IconifyIcon } from '@iconify/react';
import keyIcon from '@iconify-icons/mdi/key';
import robotIcon from '@iconify-icons/mdi/robot';

export interface BaseFieldConfig {
  name: string;
  label: string;
  placeholder?: string;
  type?: 'text' | 'password';
  icon?: string | IconifyIcon;
  required?: boolean;
  validation?: z.ZodType;
}

export interface ProviderDefinition {
  id: string;
  label: string;
  description?: string;
  modelPlaceholder?: string;
  extraFields?: BaseFieldConfig[];
  customValidation?: (data: any) => z.ZodType;
  excludeApiKey?: boolean;
}

export interface ModelTypeDefinition {
  type: string;
  providers: ProviderDefinition[];
  commonFields?: BaseFieldConfig[];
}

export class UniversalModelFactory {
  private static readonly DEFAULT_COMMON_FIELDS: BaseFieldConfig[] = [
    {
      name: 'apiKey',
      label: 'API Key',
      type: 'password',
      placeholder: 'Your API Key',
      icon: keyIcon,
      required: true,
      validation: z.string().min(1, 'API Key is required'),
    },
    {
      name: 'model',
      label: 'Model Name',
      placeholder: 'Model name',
      icon: robotIcon,
      required: true,
      validation: z.string().min(1, 'Model is required'),
    },
  ];

  static generateProviderConfig(
    modelType: string,
    definition: ProviderDefinition,
    customCommonFields?: BaseFieldConfig[]
  ) {
    const commonFields = customCommonFields || this.DEFAULT_COMMON_FIELDS;
    
    // 🔥 FIX: Filter out apiKey field if provider excludes it
    let fieldsToInclude = [...commonFields];
    
    if (definition.excludeApiKey) {
      // Only exclude the apiKey field, keep model and other fields
      fieldsToInclude = commonFields.filter(field => field.name !== 'apiKey');
    }
    
    const allFields = [...fieldsToInclude, ...(definition.extraFields || [])];
    
    // Generate Zod schema
    const schemaFields: Record<string, z.ZodType> = {
      modelType: z.literal(definition.id),
    };

    allFields.forEach(field => {
      if (field.validation) {
        schemaFields[field.name] = field.required !== false 
          ? field.validation 
          : field.validation.optional();
      }
    });

    const schema = definition.customValidation 
      ? definition.customValidation(schemaFields)
      : z.object(schemaFields);

    // Generate default values
    const defaultValues: Record<string, any> = {
      modelType: definition.id,
    };

    allFields.forEach(field => {
      defaultValues[field.name] = '';
    });

    return {
      id: definition.id,
      label: definition.label,
      schema,
      defaultValues,
      modelPlaceholder: definition.modelPlaceholder || '',
      description: definition.description || `Enter your ${definition.label} credentials to get started.`,
      additionalFields: definition.extraFields || [],
      allFields, // 🔥 This now correctly excludes only apiKey when excludeApiKey: true
    };
  }

  static generateModelType(definition: ModelTypeDefinition) {
    const providers = definition.providers.map(provider => 
      this.generateProviderConfig(definition.type, provider, definition.commonFields)
    );

    return {
      type: definition.type,
      providers,
    };
  }

  // Helper to get provider by ID
  static getProviderById(providers: any[], id: string) {
    return providers.find(provider => provider.id === id);
  }

  // Helper to get all provider IDs
  static getProviderIds(providers: any[]): string[] {
    return providers.map(provider => provider.id);
  }
}

// UNIVERSAL TYPES

export interface BaseFormValues {
  modelType: string;
  _provider?: string;
}

export interface BaseModelFormValues extends BaseFormValues {
  apiKey: string;
  model: string;
}