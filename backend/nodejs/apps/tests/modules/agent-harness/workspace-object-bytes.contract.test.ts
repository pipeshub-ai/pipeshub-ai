import { InMemoryWorkspaceObjects } from '../../../src/modules/agent-harness/adapters/in-memory-workspace-objects';
import { describeWorkspaceObjectBytesContract } from './support/workspace-object-bytes-contract';

describeWorkspaceObjectBytesContract('in-memory', async () => {
  const objects = new InMemoryWorkspaceObjects();
  return {
    objects,
    orgs: ['org-a', 'org-b'],
    missingObjectId: () => `sha256:${'0'.repeat(64)}`,
    tamper: async (orgId, ref, bytes) => {
      objects.objects.set(`${orgId}\u0000${ref.objectId}`, bytes);
    },
  };
});
