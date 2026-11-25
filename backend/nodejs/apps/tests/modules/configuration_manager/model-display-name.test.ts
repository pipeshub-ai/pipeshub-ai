import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  modelDisplayName,
  UNAVAILABLE_MODEL,
} from '../../../src/modules/configuration_manager/utils/model-display-name';

interface Case {
  id: string;
  entry: Record<string, unknown> | null;
  modelName: string | null;
  display: string;
}

const vectors = JSON.parse(
  readFileSync(
    join(__dirname, '../../../../../../contracts/harness/vectors/model-display-names.json'),
    'utf-8',
  ),
) as { unavailable: string; cases: Case[] };

describe('P2.2 DN-1: model display names (shared vectors with Python)', () => {
  it('uses the vectors fallback label', () => {
    expect(UNAVAILABLE_MODEL).to.equal(vectors.unavailable);
  });

  for (const vector of vectors.cases) {
    it(vector.id, () => {
      const shown = modelDisplayName(vector.entry, vector.modelName);
      expect(shown).to.equal(vector.display);
      const configuration = (vector.entry?.['configuration'] ?? {}) as Record<string, unknown>;
      for (const hidden of [vector.entry?.['modelKey'], configuration['deploymentName'], configuration['apiKey']]) {
        if (typeof hidden === 'string') expect(shown).to.not.include(hidden);
      }
    });
  }
});
