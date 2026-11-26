/**
 * What a run delivered and what it only worked with (BGUI-11), decided once,
 * here, for every surface that counts or lists a job's files: the report under
 * its card, its panel's Outputs tab and the end-of-run summary.
 *
 * A delivered file is one the run published as an artifact: the harness
 * publishes the engine's outputs folder (and a loop's `$OUTPUT_DIR`) and
 * refuses what may not leave, so the publication, not a folder name, is the
 * truth. Every other changed file (notes, scratch scripts, page renders) is a
 * working file.
 */
import type { ActivityFile } from './activity';
import type { ArtifactView } from './timeline';

/** The artifacts a run saved for the person: never a discarded draft or a temporary file. */
export function savedArtifacts(
  artifacts: readonly ArtifactView[],
): ArtifactView[] {
  return artifacts.filter(
    (artifact) => artifact.lifecycle !== 'discarded' && !artifact.isTemporary,
  );
}

const depth = (path: string): number => path.split('/').length;

/**
 * The changed paths each saved artifact was published from. An artifact is
 * named by its path inside the outputs folder (`report.pdf`, `charts/q3.png`),
 * so its workspace path ends with that name; of several such paths (a draft
 * copy in `notes/`), the shallowest is the published one, the outputs folder
 * sitting at the workspace's top.
 */
export function deliveredPaths(
  paths: Iterable<string>,
  artifacts: readonly ArtifactView[],
): Set<string> {
  const all = [...paths];
  const delivered = new Set<string>();
  for (const artifact of savedArtifacts(artifacts)) {
    const name = artifact.fileName.replace(/^\/+/, '');
    if (name === '') continue;
    const from = all
      .filter((path) => path === name || path.endsWith(`/${name}`))
      .sort((a, b) => depth(a) - depth(b) || a.localeCompare(b))[0];
    if (from !== undefined) delivered.add(from);
  }
  return delivered;
}

/** The run's changed files, each delivered one marked so. */
export function classifiedFiles(
  files: readonly ActivityFile[],
  artifacts: readonly ArtifactView[],
): ActivityFile[] {
  const delivered = deliveredPaths(
    files.map((file) => file.path),
    artifacts,
  );
  return files.map((file) =>
    delivered.has(file.path) ? { ...file, delivered: true as const } : file,
  );
}
