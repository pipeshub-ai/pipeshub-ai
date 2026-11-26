/**
 * AI Access takes the cards after the org's default coding engine from `AiAccessControls` and more engine settings
 * from `AiAccessEnginesExtras` (config.ts): this build adds neither.
 */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { AiAccessControls, AiAccessEnginesExtras } from '@/config';
import { AiAccessControls as DefaultControls } from '../components/ai-access-controls';
import { AiAccessEnginesExtras as DefaultEngines } from '../components/ai-access-engines-extras';

describe('AI Access slots', () => {
  it('AiAccessEnginesExtras is the default this build exports, and renders nothing', () => {
    expect(AiAccessEnginesExtras).toBe(DefaultEngines);
    expect(render(<AiAccessEnginesExtras />).container.innerHTML).toBe('');
  });

  it('AiAccessControls is the default this build exports, and renders nothing', () => {
    expect(AiAccessControls).toBe(DefaultControls);
    expect(render(<AiAccessControls />).container.innerHTML).toBe('');
  });
});
