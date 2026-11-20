/** Stands in for an ACL-version extension, so a test can see where the code applies it. */
export const testAcl = {
  versionBump: () => ({ $inc: { aclVersion: 1 } }),
  bumpDocument: (doc: object) => {
    const d = doc as { aclVersion?: number | null }
    d.aclVersion = (d.aclVersion ?? 0) + 1
  },
}

/** Content access that grants nothing, for tests about the share write itself. */
export const noContentAccess = {
  shared: () => Promise.resolve(),
  unshared: () => Promise.resolve(),
  viewed: () => Promise.resolve(),
}
