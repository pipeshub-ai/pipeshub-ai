/**
 * The process's one shutdown. It runs once whatever asks for it, how often,
 * or from where: on a full disk every log line raised an uncaught exception,
 * and each one started another `Application.stop()` (OPS2: 36,752 at once,
 * which kept the store's server selection busy for 2.5 hours). It always
 * ends the process: a stop that outlives `deadlineMs` exits anyway. Only an
 * operator's signal exits 0; anything else exits non-zero so a supervisor
 * starts the process again.
 */

/** The drain's 10 s and the disposals after it; a stop still running then is stuck. */
export const SHUTDOWN_DEADLINE_MS = 20_000;

export interface ProcessShutdownOptions {
  stop: () => Promise<void>;
  logger: {
    info(message: string, meta?: Record<string, unknown>): void;
    error(message: string, meta?: Record<string, unknown>): void;
  };
  exit: (code: number) => void;
  deadlineMs: number;
}

export interface ProcessShutdown {
  /** SIGTERM, SIGINT: what an operator or a supervisor asks for. */
  signal(name: string): void;
  /** A fault the process cannot vouch for itself after. */
  fault(reason: string, error: unknown): void;
}

export function processShutdown(
  options: ProcessShutdownOptions,
): ProcessShutdown {
  const { stop, logger, exit, deadlineMs } = options;
  let code: number | null = null;

  const begin = (reason: string, wanted: number): void => {
    code = wanted;
    logger.info(`Received ${reason}. Starting graceful shutdown...`);
    setTimeout(() => {
      logger.error('Shutdown did not finish in time; exiting', { deadlineMs });
      exit(1);
    }, deadlineMs).unref();
    stop().then(
      () => {
        exit(code ?? 1);
      },
      (error: unknown) => {
        logger.error('Error during shutdown:', {
          error: error instanceof Error ? error.message : String(error),
        });
        exit(1);
      },
    );
  };

  return {
    signal(name) {
      if (code === null) begin(name, 0);
    },
    fault(reason, error) {
      // Logged only the first time: when the log's own write is what fails, every line raises another fault.
      if (code !== null) {
        code = 1;
        return;
      }
      logger.error(`${reason}:`, {
        error:
          error instanceof Error
            ? { name: error.name, message: error.message }
            : String(error),
      });
      begin(reason, 1);
    },
  };
}
