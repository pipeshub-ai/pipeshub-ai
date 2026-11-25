/**
 * WP-56 (C4.08 x WP-51): an agent's "Safety and checks" (plan approval, result checks) travel with its export
 * (schema version 2). The export no longer lists them as not included; a version 1 file, made before they
 * travelled, still imports and its result still says to set them again (R12-AX-2).
 *
 * WP56-GATES-6 the export's "not included" line does not name the Safety and checks, in every locale.
 * WP56-GATES-7 a version 2 file is read, with the gates shown in the preview; a version 1 file is still read.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { readAgentExport } from '../../config-transfer';

const LOCALES = path.resolve(__dirname, '../../../../../lib/i18n/locales');

const FILE = {
  format: 'pipeshub.agent-config',
  schemaVersion: 2,
  exportedAt: 1,
  source: { agentVersion: null, digest: null },
  agent: {
    name: 'Support triage',
    models: [],
    toolsets: [],
    mcpServers: [],
    knowledge: [],
    skills: [],
    gates: { planApproval: 'required', verify: { checks: [{ kind: 'file', path: 'a.md' }], onFail: 'flag' } },
  },
  policy: {},
  excluded: [],
};

describe('WP-56 agent gates travel with an export', () => {
  it('WP56-GATES-6: the export no longer says the Safety and checks are left out, in every locale', () => {
    for (const file of readdirSync(LOCALES).filter((name) => name.endsWith('.json'))) {
      const words = JSON.parse(readFileSync(path.join(LOCALES, file), 'utf8'));
      const gates = words.agentTransfer.excluded.gates as string;
      expect(gates, file).toBeTruthy();
      expect(words.agentTransfer.export.notIncluded as string, file).not.toContain(gates);
    }
  });

  it('WP56-GATES-7: a version 2 export is read with its Safety and checks; a version 1 export is still read', () => {
    const v2 = readAgentExport(JSON.stringify(FILE));
    expect(v2.ok).toBe(true);
    expect(v2.ok && v2.preview.gates).toEqual({ planApproval: true, checks: ['file'] });

    const { gates: _left, ...v1Agent } = FILE.agent;
    const v1 = readAgentExport(JSON.stringify({ ...FILE, schemaVersion: 1, agent: v1Agent }));
    expect(v1.ok && v1.preview.gates).toEqual(null);

    expect(readAgentExport(JSON.stringify({ ...FILE, schemaVersion: 3 }))).toEqual({
      ok: false,
      problem: 'newerSchema',
      schemaVersion: 3,
    });
  });
});
