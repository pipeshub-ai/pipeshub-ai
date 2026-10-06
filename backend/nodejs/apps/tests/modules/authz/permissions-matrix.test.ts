import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { z } from 'zod';
import {
  CURRENT_PHASE,
  deciders,
  isPhaseActive,
  type MatrixDecider,
  type MatrixRow,
} from './matrix-deciders';

const rowSchema = z
  .object({
    id: z.string().min(1),
    decider: z.string().min(1),
    since: z.string().regex(/^PH-\d{2}$/),
    langs: z.array(z.enum(['ts', 'py', 'fe'])).min(1).optional(),
    given: z
      .object({
        principal: z.string().min(1),
        resource: z.string().min(1),
        grants: z.array(z.record(z.string(), z.unknown())),
      })
      .strict(),
    action: z.string().min(1),
    expect: z
      .object({
        allow: z.boolean(),
        role: z.string().optional(),
        code: z.string().optional(),
      })
      .strict(),
  })
  .strict();

const matrixSchema = z
  .object({ version: z.literal(1), rows: z.array(rowSchema) })
  .strict();

const DEFAULT_LANGS: ReadonlyArray<'ts' | 'py' | 'fe'> = ['ts', 'py'];

export function validateMatrix(
  doc: unknown,
  registry: Record<string, MatrixDecider>,
  currentPhase: string,
  lang: 'ts' | 'py' = 'ts',
): { rows: MatrixRow[]; errors: string[] } {
  const parsed = matrixSchema.safeParse(doc);
  if (!parsed.success) {
    return {
      rows: [],
      errors: parsed.error.issues.map((i) => `schema: ${i.path.join('.')}: ${i.message}`),
    };
  }
  const rows = parsed.data.rows as MatrixRow[];
  const errors: string[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    if (seen.has(row.id)) {
      errors.push(`row "${row.id}": duplicate id`);
    }
    seen.add(row.id);
    const targetsLang = (row.langs ?? DEFAULT_LANGS).includes(lang);
    if (targetsLang && isPhaseActive(row.since, currentPhase) && !registry[row.decider]) {
      errors.push(
        `row "${row.id}": no decider "${row.decider}" registered (since ${row.since} <= ${currentPhase})`,
      );
    }
  }
  return { rows, errors };
}

const matrixPath = join(__dirname, '../../fixtures/authz/permissions-matrix.json');

function row(id: string, decider = 'known', since = 'PH-00'): unknown {
  return {
    id,
    decider,
    since,
    given: { principal: 'u1', resource: 'r1', grants: [] },
    action: 'read',
    expect: { allow: true },
  };
}

describe('permissions golden matrix', () => {
  const doc: unknown = JSON.parse(readFileSync(matrixPath, 'utf8'));
  const { rows, errors } = validateMatrix(doc, deciders, CURRENT_PHASE);

  it('validates the committed matrix', () => {
    expect(errors).to.deep.equal([]);
  });

  for (const r of rows) {
    if (!(r.langs ?? DEFAULT_LANGS).includes('ts')) {
      continue;
    }
    if (!isPhaseActive(r.since)) {
      it.skip(`${r.id} (enabled in ${r.since})`, () => undefined);
      continue;
    }
    it(r.id, () => {
      const decide = deciders[r.decider];
      if (!decide) {
        throw new Error(`row "${r.id}": no decider "${r.decider}" registered`);
      }
      expect(decide(r)).to.deep.equal(r.expect);
    });
  }

  describe('validation (PH00-11)', () => {
    const registry = { known: () => ({ allow: true }) };

    it('accepts the empty matrix', () => {
      expect(validateMatrix({ version: 1, rows: [] }, registry, 'PH-00').errors).to.deep.equal([]);
    });

    it('names a duplicated id', () => {
      const { errors: errs } = validateMatrix(
        { version: 1, rows: [row('dup'), row('dup')] },
        registry,
        'PH-00',
      );
      expect(errs).to.have.length(1);
      expect(errs[0]).to.contain('"dup"').and.to.contain('duplicate');
    });

    it('names a row with an unknown decider when since <= current phase', () => {
      const { errors: errs } = validateMatrix(
        { version: 1, rows: [row('r-missing', 'nope')] },
        registry,
        'PH-00',
      );
      expect(errs).to.have.length(1);
      expect(errs[0]).to.contain('"r-missing"').and.to.contain('"nope"');
    });

    it('does not require a decider for a later-phase row', () => {
      const { errors: errs } = validateMatrix(
        { version: 1, rows: [row('r-later', 'nope', 'PH-03')] },
        registry,
        'PH-00',
      );
      expect(errs).to.deep.equal([]);
    });

    it('rejects a malformed row', () => {
      const bad = { version: 1, rows: [{ id: 'x' }] };
      expect(validateMatrix(bad, registry, 'PH-00').errors).to.not.be.empty;
    });
  });
});
