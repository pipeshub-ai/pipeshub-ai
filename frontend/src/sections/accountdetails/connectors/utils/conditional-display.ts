export interface ConditionalDisplayRule {
  field: string;
  operator: 'equals' | 'not_equals' | 'contains' | 'not_contains' | 'greater_than' | 'less_than' | 'is_empty' | 'is_not_empty';
  value?: any;
}

export interface ConditionalDisplayConfig {
  [key: string]: {
    showWhen: ConditionalDisplayRule;
  };
}

/**
 * Evaluates whether a UI element should be displayed based on conditional display rules
 * @param config - The conditional display configuration
 * @param elementKey - The key of the element to check (e.g., 'redirectUri')
 * @param formValues - Current form values
 * @returns boolean indicating whether the element should be shown
 */
export const shouldShowElement = (
  config: ConditionalDisplayConfig | undefined,
  elementKey: string,
  formValues: Record<string, any>
): boolean => {
  if (!config || !config[elementKey]) {
    return true; // Show field by default if no conditional rule exists
  }

  const rule = config[elementKey].showWhen;
  const fieldValue = formValues[rule.field];

  switch (rule.operator) {
    case 'equals':
      return fieldValue === rule.value;
    
    case 'not_equals':
      return fieldValue !== rule.value;
    
    case 'contains':
      return fieldValue && typeof fieldValue === 'string' && fieldValue.includes(rule.value);
    
    case 'not_contains':
      return !fieldValue || typeof fieldValue !== 'string' || !fieldValue.includes(rule.value);
    
    case 'greater_than':
      return Number(fieldValue) > Number(rule.value);
    
    case 'less_than':
      return Number(fieldValue) < Number(rule.value);
    
    case 'is_empty':
      return !fieldValue || (typeof fieldValue === 'string' && fieldValue.trim() === '');
    
    case 'is_not_empty':
      return fieldValue && (typeof fieldValue !== 'string' || fieldValue.trim() !== '');
    
    default:
      return false;
  }
};

/**
 * Evaluates all conditional display rules for a given form section
 * @param conditionalDisplay - The conditional display configuration
 * @param formValues - Current form values
 * @returns Object with element keys and their visibility status
 */
export const evaluateConditionalDisplay = (
  conditionalDisplay: ConditionalDisplayConfig | undefined,
  formValues: Record<string, any>
): Record<string, boolean> => {
  if (!conditionalDisplay) {
    return {};
  }

  const result: Record<string, boolean> = {};
  
  Object.keys(conditionalDisplay).forEach(elementKey => {
    result[elementKey] = shouldShowElement(conditionalDisplay, elementKey, formValues);
  });

  return result;
};
