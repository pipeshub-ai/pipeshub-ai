/**
 * Boot-time schema check (mirror of backend/db/boot_assert.py). `requirements` is the
 * schema_requirements.json written at build time: { branch: { requires, code } }. A service refuses to
 * become ready when migrate has not run (epoch < requires) or when its code is older than a contracted
 * schema (min_code_epoch > code). Branches present in the database but not listed are ignored.
 * Reading kernel.schema_epoch is left to the caller (the Node PostgreSQL client is not part of this module).
 */
export interface SchemaRequirement {
  requires: number;
  code: number;
}

export type SchemaRequirements = Record<string, SchemaRequirement>;

/** Rows of `SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch`. */
export interface SchemaEpochRow {
  branch: string;
  epoch: number;
  min_code_epoch: number;
}

export function schemaProblems(
  rows: SchemaEpochRow[],
  requirements: SchemaRequirements,
): string[] {
  const byBranch = new Map(rows.map((r) => [r.branch, r]));
  const problems: string[] = [];
  for (const [branch, req] of Object.entries(requirements)) {
    const { epoch = 0, min_code_epoch: minCode = 0 } =
      byBranch.get(branch) ?? {};
    if (epoch < req.requires) {
      problems.push(
        `schema ${branch} epoch ${String(epoch)} < required ${String(req.requires)}`,
      );
    }
    if (minCode > req.code) {
      problems.push(
        `schema ${branch} min_code_epoch ${String(minCode)} > code ${String(req.code)}`,
      );
    }
  }
  return problems;
}
