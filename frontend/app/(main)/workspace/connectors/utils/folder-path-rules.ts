/**
 * Checks a new value of a connector's Folders filter before it is saved, as the server
 * does (`folder_value_problems` in `folder_scope.py`), so the reason is shown at the field in the
 * reader's language. Values saved before are never checked here: they keep syncing as they did.
 */

export type FolderPathStyle = 'posix' | 'windows';

export type FolderPathProblem =
  | 'climbs_out'
  | 'empty_segment'
  | 'excludes_everything'
  | 'too_long'
  | 'unc_path'
  | 'double_star_in_name'
  | 'too_many_double_stars'
  | 'too_many_stars';

/** The one map from the server's codes to the field's messages. */
export const FOLDER_PATH_PROBLEM_KEYS: Record<FolderPathProblem, string> = {
  climbs_out: 'workspace.connectors.filters.folderPaths.errors.climbsOut',
  empty_segment: 'workspace.connectors.filters.folderPaths.errors.emptySegment',
  excludes_everything: 'workspace.connectors.filters.folderPaths.errors.excludesEverything',
  too_long: 'workspace.connectors.filters.folderPaths.errors.tooLong',
  unc_path: 'workspace.connectors.filters.folderPaths.errors.uncPath',
  double_star_in_name: 'workspace.connectors.filters.folderPaths.errors.doubleStarInName',
  too_many_double_stars: 'workspace.connectors.filters.folderPaths.errors.tooManyDoubleStars',
  too_many_stars: 'workspace.connectors.filters.folderPaths.errors.tooManyStars',
};

const MAX_LENGTH = 1024;
const MAX_DOUBLE_STAR_SEGMENTS = 4;
const MAX_STARS_IN_A_NAME = 16;

/** The lengths of the runs of unescaped `*` in one folder name. */
function starRuns(name: string, escapes: boolean): number[] {
  const runs: number[] = [];
  let run = 0;
  for (let i = 0; i < name.length; i += 1) {
    if (escapes && name[i] === '\\' && (name[i + 1] === '*' || name[i + 1] === '?')) {
      if (run) runs.push(run);
      run = 0;
      i += 1;
    } else if (name[i] === '*') {
      run += 1;
    } else {
      if (run) runs.push(run);
      run = 0;
    }
  }
  if (run) runs.push(run);
  return runs;
}

/** Every reason, in the server's order, a new folder value would be refused. */
export function folderPathProblems(
  value: string,
  { exclude, pathStyle = 'posix', wildcards = false }: { exclude: boolean; pathStyle?: FolderPathStyle; wildcards?: boolean }
): FolderPathProblem[] {
  const windows = pathStyle === 'windows';
  const problems: FolderPathProblem[] = [];
  if (value.replace(/\\/g, '/').split('/').some((part) => part.trim() === '..')) problems.push('climbs_out');
  const text = value.trim();
  const body = (windows ? text.replace(/\\/g, '/') : text).replace(/^\/+|\/+$/g, '');
  const names = body.split('/');
  if (body.includes('//')) problems.push('empty_segment');
  if (exclude && names.every((name) => name === '' || name === '**')) problems.push('excludes_everything');
  if (value.length > MAX_LENGTH) problems.push('too_long');
  if (windows && text.startsWith('\\\\') && names.filter(Boolean).length >= 2) problems.push('unc_path');
  if (wildcards) {
    const escapes = !windows;
    const named = names.filter((name) => name !== '**');
    if (named.some((name) => starRuns(name, escapes).some((run) => run > 1))) problems.push('double_star_in_name');
    if (names.filter((name) => name === '**').length > MAX_DOUBLE_STAR_SEGMENTS) problems.push('too_many_double_stars');
    if (named.some((name) => starRuns(name, escapes).reduce((a, b) => a + b, 0) > MAX_STARS_IN_A_NAME)) {
      problems.push('too_many_stars');
    }
  }
  return problems;
}

/** A pattern whose last folder looks like a file type (`*.pdf`): patterns pick folders. */
export function looksLikeAFileType(value: string): boolean {
  const last = value.trim().replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop() ?? '';
  return /\*\.[A-Za-z0-9]{1,5}$/.test(last);
}
