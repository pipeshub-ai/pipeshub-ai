import { canonicalDigest } from '../../../../src/libs/utils/canonical-json';
import {
  descriptorSetDigestInput,
  engineDigestInput,
  skillDigestInput,
  toolDigestInput,
} from '../../../../src/modules/control-plane/domain/digests';

/** The scheme's digests, as the impure layers compute them. */
export const toolDescriptorDigest = (tool: Record<string, unknown>): string =>
  canonicalDigest(toolDigestInput(tool));
export const engineDigest = (engine: Record<string, unknown>): string =>
  canonicalDigest(engineDigestInput(engine));
export const skillDescriptorDigest = (
  files: Record<string, { sha256: string; size: number }>,
): string => canonicalDigest(skillDigestInput(files));
export const descriptorSetDigest = (
  named: Iterable<readonly [string, string]>,
): string => canonicalDigest(descriptorSetDigestInput(named));
