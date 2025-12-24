/**
 * Safe integer validation and arithmetic utilities
 * Prevents integer overflow vulnerabilities
 */

const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const MAX_PAGE = Math.floor(MAX_SAFE_INTEGER / 100); // Safe max page assuming max limit of 100
const MAX_LIMIT = 100; // Maximum allowed limit

/**
 * Safely parses an integer from a string, validating against overflow
 * @param value - The value to parse
 * @param defaultValue - Default value if parsing fails
 * @param min - Minimum allowed value
 * @param max - Maximum allowed value
 * @returns The parsed integer or default value
 * @throws Error if value is invalid or would cause overflow
 */
export function safeParseInt(
  value: string | undefined | null,
  defaultValue: number,
  min: number = 1,
  max: number = MAX_SAFE_INTEGER,
): number {
  if (!value) {
    return defaultValue;
  }

  const parsed = parseInt(String(value), 10);

  // Check for NaN
  if (isNaN(parsed)) {
    throw new Error(`Invalid number: ${value}`);
  }

  // Check for overflow (beyond safe integer range)
  if (parsed > MAX_SAFE_INTEGER || parsed < -MAX_SAFE_INTEGER) {
    throw new Error(`Number exceeds safe integer range: ${value}`);
  }

  // Check bounds
  if (parsed < min) {
    throw new Error(`Number must be at least ${min}: ${value}`);
  }

  if (parsed > max) {
    throw new Error(`Number must be at most ${max}: ${value}`);
  }

  return parsed;
}

/**
 * Safely calculates skip value for pagination: (page - 1) * limit
 * Validates that the calculation won't overflow
 * @param page - Page number (1-indexed)
 * @param limit - Items per page
 * @returns The skip value
 * @throws Error if calculation would overflow
 */
export function safeCalculateSkip(page: number, limit: number): number {
  // Validate inputs are safe integers
  if (page > MAX_SAFE_INTEGER || limit > MAX_SAFE_INTEGER) {
    throw new Error('Page or limit exceeds safe integer range');
  }

  // Check for overflow: (page - 1) * limit
  // For multiplication: check if a != 0 && b > MAX / a
  if (limit !== 0 && page - 1 > MAX_SAFE_INTEGER / limit) {
    throw new Error('Pagination calculation would overflow');
  }

  const skip = (page - 1) * limit;

  // Double-check result is safe
  if (skip > MAX_SAFE_INTEGER || skip < 0) {
    throw new Error('Pagination skip value exceeds safe integer range');
  }

  return skip;
}

/**
 * Safely parses and validates pagination parameters
 * @param pageStr - Page number as string
 * @param limitStr - Limit as string
 * @param defaultPage - Default page number (default: 1)
 * @param defaultLimit - Default limit (default: 20)
 * @param maxLimit - Maximum allowed limit (default: 100)
 * @returns Object with validated page, limit, and skip values
 * @throws Error if validation fails
 */
export function safeParsePagination(
  pageStr: string | undefined | null,
  limitStr: string | undefined | null,
  defaultPage: number = 1,
  defaultLimit: number = 20,
  maxLimit: number = MAX_LIMIT,
): { page: number; limit: number; skip: number } {
  const page = safeParseInt(pageStr, defaultPage, 1, MAX_PAGE);
  const limit = safeParseInt(limitStr, defaultLimit, 1, maxLimit);
  const skip = safeCalculateSkip(page, limit);

  return { page, limit, skip };
}

/**
 * Safely calculates total pages: (totalCount + limit - 1) // limit
 * @param totalCount - Total number of items
 * @param limit - Items per page
 * @returns Total number of pages
 * @throws Error if calculation would overflow
 */
export function safeCalculateTotalPages(
  totalCount: number,
  limit: number,
): number {
  // Validate inputs
  if (totalCount < 0 || limit <= 0) {
    throw new Error('Invalid totalCount or limit for page calculation');
  }

  if (totalCount > MAX_SAFE_INTEGER || limit > MAX_SAFE_INTEGER) {
    throw new Error('Total count or limit exceeds safe integer range');
  }

  // Check for overflow in addition: totalCount + limit - 1
  if (totalCount > MAX_SAFE_INTEGER - limit + 1) {
    throw new Error('Total pages calculation would overflow');
  }

  const totalPages = Math.ceil(totalCount / limit);

  // Validate result
  if (totalPages > MAX_SAFE_INTEGER) {
    throw new Error('Total pages exceeds safe integer range');
  }

  return totalPages;
}

