/** A named, expiring lease; at most one holder wins at a time. */
export interface LeaderLease {
  /** Acquires or renews the lease for `holder` on store time; false while another holds it. */
  tryAcquire(name: string, holder: string, ttlMs: number): Promise<boolean>;
  release(name: string, holder: string): Promise<void>;
}
