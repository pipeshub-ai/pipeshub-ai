/**
 * Rules for SQL that goes through the unit of work. `lintSql` runs on every statement at runtime; `scanSource`
 * is the CI scan over TypeScript files that use this module.
 */

export const TAG_RE =
  /\/\*\s*pipeshub:([a-z][a-z0-9_]*\.[a-zA-Z][a-zA-Z0-9_]*)\s*\*\//;

export function sqlTag(text: string): string | undefined {
  return TAG_RE.exec(text)?.[1];
}

/** Blank out comments and quoted text so keywords inside them do not match. */
export function stripSql(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/--[^\n]*/g, ' ')
    .replace(/(\$[A-Za-z_]*\$)[\s\S]*?\1/g, "''")
    .replace(/'(?:[^']|'')*'/g, "''")
    .replace(/"(?:[^"]|"")*"/g, '""');
}

const STATEMENT_RULES: Array<[RegExp, string]> = [
  [
    /^\s*SET\s+(?!LOCAL\b)/i,
    'session SET (use the unit of work; only set_config(..., true) is allowed)',
  ],
  [/^\s*RESET\b/i, 'RESET changes session state'],
  [/^\s*DECLARE\b/i, 'cursors are banned (use keyset pagination)'],
  [/^\s*LISTEN\b|^\s*UNLISTEN\b/i, 'LISTEN is banned'],
  [
    /^\s*(PREPARE|EXECUTE|DEALLOCATE)\b/i,
    'SQL-level PREPARE/EXECUTE/DEALLOCATE are banned',
  ],
  [/^\s*DISCARD\b/i, 'DISCARD is banned'],
  [
    /^\s*(BEGIN|COMMIT|END|ROLLBACK|ABORT|START\s+TRANSACTION|PREPARE\s+TRANSACTION)\b/i,
    'transaction control belongs to the unit of work',
  ],
];

const ANYWHERE_RULES: Array<[RegExp, string]> = [
  [
    /\bSET\s+LOCAL\s+(app\.|role\b|session_authorization\b)/i,
    'the org, user, request and role bindings belong to the unit prologue',
  ],
  [/\bWITH\s+HOLD\b/i, 'WITH HOLD cursors are banned'],
  [
    /\bset_config\s*\([^)]*,\s*false\s*\)/i,
    'set_config(..., false) is session-scoped; use true',
  ],
  [
    /\bpg_(try_)?advisory_xact_lock(_shared)?\s*\(\s*(7340|7341|7350)\b/i,
    'advisory lock classes 7340, 7341 and 7350 belong to the kernel',
  ],
  [
    /\bpg_(try_)?advisory_(lock|unlock)(_shared|_all)?\s*\(/i,
    'session advisory locks are banned; use the xact forms',
  ],
];

export function lintSql(text: string): string[] {
  const problems: string[] = [];
  const stripped = stripSql(text);
  for (const stmt of stripped.split(';')) {
    for (const [re, msg] of STATEMENT_RULES)
      if (re.test(stmt)) problems.push(msg);
    if (
      /\bCREATE\s+(GLOBAL\s+|LOCAL\s+)?TEMP(ORARY)?\s+TABLE\b/i.test(stmt) &&
      !/\bON\s+COMMIT\s+DROP\b/i.test(stmt)
    ) {
      problems.push('CREATE TEMP TABLE needs ON COMMIT DROP');
    }
  }
  for (const [re, msg] of ANYWHERE_RULES)
    if (re.test(stripped)) problems.push(msg);
  // string literals are blanked in `stripped`, and the setting name is one
  if (
    /\bset_config\s*\(\s*'(app\.[^']*|role|session_authorization)'/i.test(
      text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/--[^\n]*/g, ' '),
    )
  )
    problems.push(
      'the org, user, request and role bindings belong to the unit prologue',
    );
  return [...new Set(problems)];
}

const SQL_START =
  /^\s*(\/\*[\s\S]*?\*\/\s*)?(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|WITH|MERGE\s+INTO)\b/i;

export interface SourceProblem {
  line: number;
  problem: string;
}

/**
 * Find SQL string literals in TypeScript source and apply the rules: tagged, no interpolation, no banned
 * constructs. shortcut: a character scanner, not an AST, and a backtick inside `${}` ends the template early;
 * switch to the TypeScript compiler API if that ever produces a false result.
 */
export function scanSource(src: string): SourceProblem[] {
  const out: SourceProblem[] = [];
  let i = 0;
  let line = 1;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === '\n') line++;
    if (c === '/' && src[i + 1] === '/') {
      while (i < n && src[i] !== '\n') i++;
      continue;
    }
    if (c === '/' && src[i + 1] === '*') {
      const end = src.indexOf('*/', i + 2);
      const stop = end < 0 ? n : end + 2;
      line += (src.slice(i, stop).match(/\n/g) ?? []).length;
      i = stop;
      continue;
    }
    if (c === "'" || c === '"' || c === '`') {
      const start = i;
      const startLine = line;
      i++;
      while (i < n && src[i] !== c) {
        if (src[i] === '\\') i++;
        if (src[i] === '\n') line++;
        i++;
      }
      const body = src.slice(start + 1, i);
      i++;
      if (SQL_START.test(body) && /\b(FROM|INTO|SET|VALUES)\b/i.test(body)) {
        if (!TAG_RE.test(body))
          out.push({
            line: startLine,
            problem: 'SQL without a /* pipeshub:module.fn */ tag',
          });
        if (c === '`' && body.includes('${'))
          out.push({
            line: startLine,
            problem: 'SQL built with interpolation; use parameters',
          });
        for (const p of lintSql(body))
          out.push({ line: startLine, problem: p });
      }
      continue;
    }
    i++;
  }
  return out;
}
