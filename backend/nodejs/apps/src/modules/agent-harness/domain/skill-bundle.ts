import { createHash } from 'crypto';
import { canonicalDigest } from '../contracts/canonical-json';
import { normalizeWorkspacePath } from './workspace';

/**
 * Skill pins (ARCHITECTURE X-8, P7.1). A run pins each granted skill by the
 * digest of its content, which Python computes (`harness_core.skills.skill_bundle_digest`);
 * this module computes the same digest so Node can check one, and keeps the
 * name-and-version pin runs admitted earlier carry. Shared vectors:
 * `contracts/harness/vectors/skill-bundles.json`.
 */

export const SKILL_BUNDLE_FORMAT = 'skill-bundle/1';

/** Files form a JSON object, so RFC 8785 orders paths the same way Python does. */
export function skillBundleDigest(
  files: ReadonlyMap<string, Uint8Array>,
): `sha256:${string}` {
  const entries: Record<string, { sha256: string; size: number }> = {};
  for (const [path, content] of files) {
    entries[normalizeWorkspacePath(path)] = {
      sha256: createHash('sha256').update(content).digest('hex'),
      size: content.byteLength,
    };
  }
  return canonicalDigest({ format: SKILL_BUNDLE_FORMAT, files: entries });
}

/** The pin written before content digests; Python still resolves it to that version's content. */
export const legacySkillPinDigest = (
  name: string,
  version: string | null,
): `sha256:${string}` => canonicalDigest({ name, version });
