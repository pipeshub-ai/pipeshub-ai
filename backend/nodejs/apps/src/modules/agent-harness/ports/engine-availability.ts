/**
 * Which pinned engines this deployment cannot run because the sandbox broker
 * can neither find nor pull their image (H1; an admin-supplied Claude Code
 * image never built, E9), and whether coding engines have their sandbox here
 * at all (recs2: Helm has no egress proxy or agent-sandbox yet, P5.3). Engines
 * are named `kind@version`.
 */
export interface EngineAvailability {
  /**
   * The engines the broker reports unrunnable; null when it says nothing
   * about engines (or none is configured). Rejects when it could not be asked.
   */
  unavailable(): Promise<ReadonlySet<string> | null>;
  /**
   * The engines among `unavailable()` held back because their profile is
   * signed by no key this deployment trusts (ADR-SEC-01 D7), not for want of
   * an image; null when the broker does not say. Rejects when it could not be
   * asked.
   */
  untrusted?(): Promise<ReadonlySet<string> | null>;
  /**
   * The sandbox runtimes the broker offers (`docker`, `gvisor`, ...); null
   * when it does not say. Rejects when it could not be asked. An engine whose
   * isolation floor none of them meets is refused (gVisor on production).
   */
  modes?(): Promise<ReadonlySet<string> | null>;
  /**
   * True when the broker reports that coding engines have no sandbox on this
   * deployment (no egress proxy for their gateways): every engine is refused
   * with `ENGINE_SANDBOX_MISSING`. False when it reports one, or says nothing.
   * Rejects when it could not be asked.
   */
  sandboxMissing?(): Promise<boolean>;
  /**
   * Each engine's run image digest as the broker resolved it (`kind@version`
   * -> `sha256:<hex>`, C2.20); null when it does not say. Rejects when it
   * could not be asked. A run pins its engine's digest and its worker refuses
   * any other image (`engine_image_changed`).
   */
  imageDigests?(): Promise<ReadonlyMap<string, string> | null>;
  /**
   * Whether the broker's gVisor filters each sandbox's syscalls with Docker's
   * seccomp profile as well (runsc registered with `--oci-seccomp`, R32-SECCOMP);
   * null when gVisor is not offered or the broker does not say. Rejects when it
   * could not be asked.
   */
  gvisorSyscallFilter?(): Promise<boolean | null>;
}
