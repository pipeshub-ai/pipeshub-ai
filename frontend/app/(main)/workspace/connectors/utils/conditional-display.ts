// ========================================
// Conditional display evaluation
// ========================================

import type { ConditionalDisplayConfig } from '../types';

/**
 * Evaluate all conditional display rules against current form values.
 * Returns a map of field name → boolean (visible or not).
 */
export function evaluateConditionalDisplay(
  conditionalDisplay: ConditionalDisplayConfig | undefined,
  formValues: Record<string, unknown>
): Record<string, boolean> {
  if (!conditionalDisplay) return {};

  const result: Record<string, boolean> = {};

  for (const key of Object.keys(conditionalDisplay)) {
    result[key] = shouldShowElement(conditionalDisplay, key, formValues);
  }

  return result;
}

/**
 * Check if a specific element should be shown, following dependency chains.
 * If a parent field is hidden, all dependent fields are also hidden.
 */
export function shouldShowElement(
  conditionalDisplay: ConditionalDisplayConfig,
  elementKey: string,
  formValues: Record<string, unknown>
): boolean {
  const rule = conditionalDisplay[elementKey];
  if (!rule?.showWhen) return true;

  const { field, operator, value } = rule.showWhen;

  // Check if the dependent field is itself conditionally hidden
  if (conditionalDisplay[field]) {
    const parentVisible = shouldShowElement(conditionalDisplay, field, formValues);
    if (!parentVisible) return false;
  }

  const currentValue = formValues[field];

  return evaluateOperator(operator, currentValue, value);
}

/**
 * Evaluate a single operator condition.
 */
function evaluateOperator(
  operator: string,
  currentValue: unknown,
  targetValue: unknown
): boolean {
  switch (operator) {
    case 'equals':
      return currentValue === targetValue;

    case 'not_equals':
      return currentValue !== targetValue;

    case 'contains': {
      if (Array.isArray(currentValue)) {
        return currentValue.includes(targetValue);
      }
      if (typeof currentValue === 'string' && typeof targetValue === 'string') {
        return currentValue.includes(targetValue);
      }
      return false;
    }

    case 'not_contains': {
      if (Array.isArray(currentValue)) {
        return !currentValue.includes(targetValue);
      }
      if (typeof currentValue === 'string' && typeof targetValue === 'string') {
        return !currentValue.includes(targetValue);
      }
      return true;
    }

    case 'greater_than':
      return Number(currentValue) > Number(targetValue);

    case 'less_than':
      return Number(currentValue) < Number(targetValue);

    case 'is_empty':
      return (
        currentValue === undefined ||
        currentValue === null ||
        currentValue === '' ||
        (Array.isArray(currentValue) && currentValue.length === 0)
      );

    case 'is_not_empty':
      return (
        currentValue !== undefined &&
        currentValue !== null &&
        currentValue !== '' &&
        !(Array.isArray(currentValue) && currentValue.length === 0)
      );

    default:
      return true;
  }
}
