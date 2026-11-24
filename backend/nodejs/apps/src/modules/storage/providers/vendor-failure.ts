import { BaseError } from '../../../libs/errors/base.error';

/** A vendor's code is a name (AccessDenied, REQUEST_SEND_ERROR, Md5Mismatch, ENOENT); anything else may be text. */
const VENDOR_CODE = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

interface VendorStatus {
  statusCode?: number;
  code?: string;
}

/** The status and code a vendor error carries (AWS SDK v3, Azure RestError, Node fs); never its text. */
const statusAndCode = (error: unknown): VendorStatus => {
  const e = (error ?? {}) as {
    name?: unknown;
    code?: unknown;
    statusCode?: unknown;
    $metadata?: { httpStatusCode?: unknown };
  };
  const status = e.$metadata?.httpStatusCode ?? e.statusCode;
  const code = typeof e.code === 'string' ? e.code : e.name;
  return {
    ...(Number.isInteger(status) && { statusCode: status as number }),
    ...(typeof code === 'string' && VENDOR_CODE.test(code) && { code }),
  };
};

/**
 * What a provider wrap keeps about a vendor failure: the text for the admin's S3 settings check
 * (`originalError`), and the status and code that may leave the provider.
 */
export const vendorFailure = (
  error: unknown,
): VendorStatus & { originalError: string } => ({
  originalError: error instanceof Error ? error.message : 'Unknown error',
  ...statusAndCode(error),
});

/**
 * An error's cause for a storage failure: the vendor's status and code, from a provider wrap's
 * metadata or the vendor error itself; never its text, which can quote paths and buckets.
 */
export const storageCause = (
  failure: unknown,
): { status?: number; code?: string } => {
  const { statusCode, code } = statusAndCode(
    failure instanceof BaseError ? failure.metadata : failure,
  );
  return {
    ...(statusCode !== undefined && { status: statusCode }),
    ...(code !== undefined && { code }),
  };
};
