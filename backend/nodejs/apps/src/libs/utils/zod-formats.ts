import { z } from 'zod';

/**
 * String format checks that behave like zod 3's `z.string().email(msg)`:
 *  - the optional message belongs to the format failure only, a missing or
 *    non-string value keeps zod's own "required" / "wrong type" wording
 *    (zod 4's `z.email(msg)` puts the message on every issue);
 *  - the value is passed on exactly as sent (zod 4's URL check trims it);
 *  - ISO date-times accept an offset written without a colon (`+0530`).
 *
 * Use as `z.string().max(254).check(emailCheck('Invalid email'))`.
 */
type Check = z.core.CheckFn<string>;

const emailSchema = z.email();
const guidSchema = z.guid();
const urlSchema = z.url();

function formatCheck(
  accepts: (value: string) => boolean,
  format: string,
  message: string | undefined,
): Check {
  return (ctx) => {
    if (accepts(ctx.value)) return;
    ctx.issues.push({
      code: 'invalid_format',
      format,
      input: ctx.value,
      ...(message === undefined ? {} : { message }),
      // The check's own schema is not needed to report the issue.
    } as unknown as z.core.$ZodRawIssue);
  };
}

export const emailCheck = (message?: string): Check =>
  formatCheck((v) => emailSchema.safeParse(v).success, 'email', message);

export const guidCheck = (message?: string): Check =>
  formatCheck((v) => guidSchema.safeParse(v).success, 'guid', message);

export const urlCheck = (message?: string): Check =>
  formatCheck((v) => urlSchema.safeParse(v).success, 'url', message);

// Same pattern zod 3.24 used for `datetime({ offset: true })`.
const DATE =
  '((\\d\\d[2468][048]|\\d\\d[13579][26]|\\d\\d0[48]|[02468][048]00|[13579][26]00)-02-29|\\d{4}-((0[13578]|1[02])-(0[1-9]|[12]\\d|3[01])|(0[469]|11)-(0[1-9]|[12]\\d|30)|(02)-(0[1-9]|1\\d|2[0-8])))';
const TIME = '([01]\\d|2[0-3]):[0-5]\\d:[0-5]\\d(\\.\\d+)?';
const DATETIME_WITH_OFFSET = new RegExp(
  `^${DATE}T${TIME}(Z|([+-]\\d{2}:?\\d{2}))$`,
);

export const datetimeWithOffsetCheck = (message?: string): Check =>
  formatCheck((v) => DATETIME_WITH_OFFSET.test(v), 'datetime', message);

/**
 * `z.number().int()` as zod 3 had it: any whole number the engine can hold is
 * accepted (zod 4 only accepts safe integers), a fraction is a wrong-type issue
 * that does not stop the other checks on the field (`.min()` still reports).
 */
export const integerCheck =
  (message?: string): z.core.CheckFn<number> =>
  (ctx) => {
    if (Number.isInteger(ctx.value)) return;
    ctx.issues.push({
      code: 'invalid_type',
      expected: 'int',
      input: ctx.value,
      continue: true,
      ...(message === undefined ? {} : { message }),
    } as unknown as z.core.$ZodRawIssue);
  };
