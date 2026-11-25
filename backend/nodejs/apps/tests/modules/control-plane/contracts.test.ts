import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join, resolve } from 'path';
import * as controlPlane from '../../../src/modules/control-plane';

/** CPD0-5 (Node half): the control plane's error codes and enumerations are the contract files'. */
const ROOT = resolve(__dirname, '..', '..', '..', '..', '..', '..', 'contracts', 'control-plane');
const read = (name: string): unknown => JSON.parse(readFileSync(join(ROOT, name), 'utf8'));

describe('control-plane contracts (CPD-0)', () => {
  it('ERROR_CODES is errors.json, entry by entry', () => {
    const catalog = read('errors.json') as { codes: Array<{ code: string } & Record<string, unknown>> };
    expect(Object.keys(controlPlane.ERROR_CODES)).to.deep.equal(catalog.codes.map((c) => c.code));
    for (const { code, ...entry } of catalog.codes) {
      expect({ ...controlPlane.ERROR_CODES[code as controlPlane.ErrorCode] }, code).to.deep.equal(entry);
    }
  });

  it('the layers and remediation actors are common.schema.json (the layers by its ref to the policy layer, CPD3-4)', () => {
    const defs = (read('common.schema.json') as { $defs: Record<string, { enum: string[]; $ref?: string }> }).$defs;
    const policyLayer = JSON.parse(
      readFileSync(join(ROOT, '..', 'harness', 'core', 'policy', 'policy-layer.schema.json'), 'utf8'),
    ) as { $id: string; $defs: Record<string, { enum: string[] }> };
    expect(defs['LayerName']!.$ref).to.equal(`${policyLayer.$id}#/$defs/LayerName`);
    expect([...controlPlane.LAYERS]).to.deep.equal(policyLayer.$defs['LayerName']!.enum);
    expect([...controlPlane.REMEDIATION_ACTORS]).to.deep.equal(defs['RemediationActor']!.enum);
  });

  it('a Problem names a catalogued code and a known remediation actor', () => {
    const problem = {
      code: 'CP_CREDENTIAL_MISSING',
      status: controlPlane.ERROR_CODES.CP_CREDENTIAL_MISSING.status,
      message: 'Connect your Linear account.',
      retryable: controlPlane.ERROR_CODES.CP_CREDENTIAL_MISSING.retryable,
      remediation: { action: 'connect_account', actor: controlPlane.ERROR_CODES.CP_CREDENTIAL_MISSING.remediation },
    };
    expect(controlPlane.problemSchema.safeParse(problem).success).to.equal(true);
    expect(controlPlane.problemSchema.safeParse({ ...problem, remediation: { action: 'x', actor: 'anyone' } }).success).to.equal(false);
  });
});
