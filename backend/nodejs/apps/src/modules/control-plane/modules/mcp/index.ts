import { canonicalDigest } from '../../../../libs/utils/canonical-json';
import { toolDigestInput } from '../../domain/digests';
import { releaseItemsOf } from '../../domain/release-items';
import type { DomainModule } from '../../ports/extension-points';

const KIND = 'mcp';

/** MCP servers: the template catalog. Tools are discovered and approved per installation. */
export const mcpModule: DomainModule = {
  kind: KIND,
  catalog: {
    kind: KIND,
    releaseItems: () => releaseItemsOf(KIND),
    descriptorDigest: (descriptor) =>
      canonicalDigest(toolDigestInput(descriptor)),
  },
};
