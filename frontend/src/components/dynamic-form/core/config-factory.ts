// ===================================================================
// 📁 src/entities/dynamic-forms/core/config-factory.ts
// ===================================================================

import { z } from 'zod';
import { ProviderConfig } from './providers';
import { FIELD_TEMPLATES, FieldTemplate } from './field-templates';

export interface GeneratedProvider {
  id: string;
  label: string;
  description: string;
  modelPlaceholder?: string;
  allFields: FieldTemplate[];
  isSpecial: boolean;
  schema: z.ZodType;
  defaultValues: Record<string, any>;
  accountType?: 'individual' | 'business';
}

export class DynamicConfigFactory {
  static generateProvider(definition: ProviderConfig): GeneratedProvider {
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
        const template = FIELD_TEMPLATES[fieldName];
        const customOverrides = definition.customFields[fieldName];
        return { ...template, ...customOverrides };
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

      allFields.forEach((field: FieldTemplate) => {
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

    allFields.forEach((field: FieldTemplate) => {
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

    return {
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
  }

  static generateConfigType(providers: readonly ProviderConfig[]): GeneratedProvider[] {
    return providers.map(config => this.generateProvider(config));
  }
}
