import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { ERROR_CODES as HARNESS_CODES } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/errors';
import { ANSWER_ERROR_CODES } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import {
  NEXT_ACTION_KINDS,
  TOOL_ERROR_CODES,
} from '../../../../src/modules/agent-harness/contracts/generated/core/enums';
import { ERROR_CODES as CONTROL_PLANE_CODES } from '../../../../src/modules/control-plane/contracts/generated/errors';
import { withOverlayVector } from '../../../support/harness/contracts';

const VECTORS = join(
  __dirname,
  '../../../../../../../contracts/harness/vectors',
);
const read = (name: string): unknown =>
  JSON.parse(readFileSync(join(VECTORS, name), 'utf8'));

type NextAction = { kind: string; text: string };
type Sections = Record<
  'runErrors' | 'answerErrors' | 'toolErrors',
  Record<string, NextAction>
>;

/** UX-06 (C10.02, WP-18): the codes Node answers and ends runs with each say what a person can do next. */
describe('UX-06: Node’s error codes carry their next action', () => {
  // With the entries an edition's overlay vector adds, as the codes its overlay adds to the enumerations.
  const next = withOverlayVector('error-next-actions.json', read('error-next-actions.json') as Sections);
  const kinds = new Set<string>(NEXT_ACTION_KINDS);

  it('every catalogue code Node answers with has a next action of a known kind', () => {
    for (const catalog of [HARNESS_CODES, CONTROL_PLANE_CODES]) {
      for (const [code, entry] of Object.entries(catalog)) {
        const action = (entry as { nextAction: NextAction }).nextAction;
        expect(kinds.has(action.kind), code).to.equal(true);
        expect(action.text, code).to.match(/\S.*\.$/);
      }
    }
  });

  it('error-next-actions.json names exactly the RUN_ERROR, error answer and tool failure codes', () => {
    const { codes } = withOverlayVector('run-error-codes.json', read('run-error-codes.json') as { codes: string[] });
    expect(Object.keys(next.runErrors).sort()).to.deep.equal([...codes].sort());
    expect(Object.keys(next.answerErrors).sort()).to.deep.equal(
      [...ANSWER_ERROR_CODES].sort(),
    );
    expect(Object.keys(next.toolErrors).sort()).to.deep.equal(
      [...TOOL_ERROR_CODES].sort(),
    );
    for (const section of [
      next.runErrors,
      next.answerErrors,
      next.toolErrors,
    ]) {
      for (const [code, action] of Object.entries(section)) {
        expect(kinds.has(action.kind), code).to.equal(true);
        expect(action.kind, code).to.not.equal('internal');
      }
    }
  });
});
