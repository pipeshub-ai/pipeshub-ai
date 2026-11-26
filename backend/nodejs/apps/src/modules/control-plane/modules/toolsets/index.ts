import { canonicalDigest } from '../../../../libs/utils/canonical-json';
import { toolDigestInput } from '../../domain/digests';
import { releaseItemsOf } from '../../domain/release-items';
import type { DomainModule } from '../../ports/extension-points';

const KIND = 'toolset';

/** Toolsets: the `ToolsetRegistry` dump; each tool's descriptor covers its input schema. */
export const toolsetsModule: DomainModule = {
  kind: KIND,
  catalog: {
    kind: KIND,
    releaseItems: () => releaseItemsOf(KIND),
    descriptorDigest: (descriptor) =>
      canonicalDigest(toolDigestInput(descriptor)),
  },
};
