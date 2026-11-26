import { canonicalDigest } from '../../../../libs/utils/canonical-json';
import type { Descriptor } from '../../domain/catalog-item';
import { skillDigestInput } from '../../domain/digests';
import { releaseItemsOf } from '../../domain/release-items';
import type { DomainModule } from '../../ports/extension-points';

const KIND = 'skill';

const bundleDigest = (descriptor: Descriptor): string => {
  if (!('files' in descriptor)) {
    throw new TypeError(`skill descriptor ${descriptor.name} has no file list`);
  }
  return canonicalDigest(skillDigestInput(descriptor.files));
};

/** Skills: the built-in packs, by bundle digest. */
export const skillsModule: DomainModule = {
  kind: KIND,
  catalog: {
    kind: KIND,
    releaseItems: () => releaseItemsOf(KIND),
    descriptorDigest: bundleDigest,
  },
};
