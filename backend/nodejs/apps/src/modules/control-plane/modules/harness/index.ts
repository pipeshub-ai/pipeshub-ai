import { canonicalDigest } from '../../../../libs/utils/canonical-json';
import { engineDigestInput } from '../../domain/digests';
import { releaseItemsOf } from '../../domain/release-items';
import type { DomainModule } from '../../ports/extension-points';

const KIND = 'engine';

/** The harness: allow-listed engine versions, by binary digest. */
export const harnessModule: DomainModule = {
  kind: KIND,
  catalog: {
    kind: KIND,
    releaseItems: () => releaseItemsOf(KIND),
    descriptorDigest: (descriptor) =>
      canonicalDigest(engineDigestInput(descriptor)),
  },
};
