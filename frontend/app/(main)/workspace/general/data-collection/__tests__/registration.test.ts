import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('Data Collection switch: registration', () => {
  it('config.ts names this module as the General page settings of the default build', () => {
    const config = readFileSync(resolve(__dirname, '../../../../../../config.ts'), 'utf8');
    expect(config).toMatch(
      /export \{ useGeneralDataCollection as useGeneralPageSettings \} from '@\/app\/\(main\)\/workspace\/general\/data-collection\/use-general-data-collection';/,
    );
  });
});
