import { canonicalDigest } from '../../../libs/utils/canonical-json';
import { DomainEffectiveSetResolver } from '../domain/effective-set-resolver';
import type { EffectiveSetResolver } from '../ports/effective-set-resolver';

/** The domain resolver with the canonical digest of 02-lld §5.4 (the domain itself does not hash). */
export class CanonicalEffectiveSetResolver
  extends DomainEffectiveSetResolver
  implements EffectiveSetResolver
{
  constructor(cap?: number) {
    super({ digest: canonicalDigest, ...(cap === undefined ? {} : { cap }) });
  }
}
