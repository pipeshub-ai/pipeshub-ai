import { describe, expect, it } from 'vitest';
import {
  CAPABILITY_SECTION_ORDER,
  isRegistryBadgeCapability,
  modelTypesForSection,
  registryCapabilityForModelType,
} from '../types';

describe('capability sections', () => {
  it('lists each tab’s configured model buckets', () => {
    expect(modelTypesForSection('text_generation')).toEqual(['llm', 'reasoning', 'multiModal', 'slm']);
    expect(modelTypesForSection('image_generation')).toEqual(['imageGeneration']);
    expect(modelTypesForSection('embedding')).toEqual(['embedding']);
    expect(modelTypesForSection('decision')).toEqual(['decision']);
  });

  it('has a decision tab whose models open the decision schema', () => {
    expect(CAPABILITY_SECTION_ORDER).toContain('decision');
    expect(registryCapabilityForModelType('decision')).toBe('decision');
    expect(isRegistryBadgeCapability('decision')).toBe(true);
  });
});
