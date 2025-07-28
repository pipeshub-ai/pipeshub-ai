import { z } from 'zod';

import { FIELD_TEMPLATES } from './field-templates';

import type { ProviderConfig } from './providers';
import type { FieldTemplate } from './field-templates';

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
  private static createFieldFromTemplate(template: any): FieldTemplate {
    return {
      name: template.name,
      label: template.label,
      placeholder: template.placeholder,
      type: template.type,
      icon: template.icon,
      required: template.required,
      validation: template.validation,
      gridSize: template.gridSize,
      options: template.options,
      multiline: template.multiline,
      rows: template.rows,
      acceptedFileTypes: template.acceptedFileTypes,
      maxFileSize: template.maxFileSize,
      fileProcessor: template.fileProcessor,
    };
  }

  private static generateDefaultValues(
    fields: FieldTemplate[],
    providerId: string
  ): Record<string, any> {
    const defaultValues: Record<string, any> = {
      modelType: providerId,
      providerType: providerId,
    };

    fields.forEach((field: FieldTemplate) => {
      // Use custom default value if provided
      const customDefaultValue = (field as any).customDefaultValue;
      if (customDefaultValue !== undefined) {
        defaultValues[field.name] = customDefaultValue;
      } else if (field.type === 'number') {
        defaultValues[field.name] = field.name === 'port' ? 587 : 0;
      } else if (field.type === 'checkbox') {
        defaultValues[field.name] = false;
      } else if (field.type === 'select' && field.options) {
        defaultValues[field.name] = field.options[0]?.value || '';
      } else {
        defaultValues[field.name] = '';
      }
    });

    return defaultValues;
  }

  static generateProvider(definition: ProviderConfig): GeneratedProvider {
    // Handle special providers (like default)
    if (definition.isSpecial) {
      return {
        id: definition.id,
        label: definition.label,
        description:
          definition.description || 'Special configuration with no additional fields required.',
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

    // Generate fields from field names or field objects
    const allFields: FieldTemplate[] = (definition.fields || []).map((fieldItem) => {
      // Handle both string and object formats
      const fieldName = typeof fieldItem === 'string' ? fieldItem : fieldItem.name;
      const customRequired = typeof fieldItem === 'object' ? fieldItem.required : undefined;
      const customDefaultValue = typeof fieldItem === 'object' ? fieldItem.defaultValue : undefined;
      const customPlaceholder = typeof fieldItem === 'object' ? fieldItem.placeholder : undefined;

      const template = FIELD_TEMPLATES[fieldName];
      if (!template) {
        console.error(`Field template '${fieldName}' not found for provider '${definition.id}'`);
        throw new Error(`Field template '${fieldName}' not found`);
      }

      // Use helper function to create a mutable field template
      const field = this.createFieldFromTemplate(template);

      // Apply custom required from field object
      if (customRequired !== undefined) {
        field.required = customRequired;
      }

      // Apply custom placeholder from field object
      if (customPlaceholder !== undefined) {
        field.placeholder = customPlaceholder;
      }

      // Apply custom field overrides (can still override required if needed)
      if (definition.customFields && definition.customFields[fieldName]) {
        const customOverrides = definition.customFields[fieldName];
        Object.assign(field, customOverrides);
      }

      // Store custom default value for later use
      if (customDefaultValue !== undefined) {
        (field as any).customDefaultValue = customDefaultValue;
      }

      return field;
    });

    // Generate Zod schema with custom validation if provided
    if (definition.customValidation) {
      const schema = definition.customValidation({});
      const defaultValues = this.generateDefaultValues(allFields, definition.id);

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

    allFields.forEach((field: FieldTemplate) => {
      if (field.validation) {
        const isRequired = field.required !== false;

        // For optional fields, make the validation optional
        if (!isRequired) {
          // Create optional validation for URL fields
          if (field.type === 'url') {
            schemaFields[field.name] = z
              .string()
              .optional()
              .or(z.literal(''))
              .refine(
                (val) => {
                  if (!val || val.trim() === '') return true;
                  try {
                    const url = new URL(val);
                    return !!url;
                  } catch {
                    return false;
                  }
                },
                { message: 'Must be a valid URL' }
              );
          } else {
            schemaFields[field.name] = field.validation.optional();
          }
        } else {
          schemaFields[field.name] = field.validation;
        }
      }
    });

    const defaultValues = this.generateDefaultValues(allFields, definition.id);

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
    return providers.map((config) => this.generateProvider(config));
  }
}