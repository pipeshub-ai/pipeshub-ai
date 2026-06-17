/**
 * Canonical, app-agnostic helpers for deriving a file extension from a name.
 *
 * Rules (uniform everywhere):
 *  - The extension is the segment AFTER the LAST dot ("reverse" split), so
 *    names with dots in the middle (e.g. `quarterly.report.2024.pdf`,
 *    `my.data.v2.csv`, `archive.tar.gz`) resolve to the final segment.
 *  - The returned extension is lower-cased and has NO leading dot.
 *  - A name with no dot, a name that ends with a dot, or a dotfile with no
 *    real extension (e.g. `.gitignore`) yields `null` rather than throwing or
 *    treating the whole name as an extension.
 */
export function getFileExtension(name: string | null | undefined): string | null {
  if (!name) return null;
  const lastDot = name.lastIndexOf('.');
  // No dot, leading dot (dotfile with no real extension), or trailing dot.
  if (lastDot <= 0 || lastDot === name.length - 1) return null;
  return name.slice(lastDot + 1).toLowerCase();
}

/**
 * Returns the name with its final extension removed. Mirrors getFileExtension:
 * a name with no real extension is returned unchanged (never empty/garbage).
 */
export function getFilenameWithoutExtension(name: string | null | undefined): string {
  if (!name) return '';
  const lastDot = name.lastIndexOf('.');
  if (lastDot <= 0 || lastDot === name.length - 1) return name;
  return name.slice(0, lastDot);
}
