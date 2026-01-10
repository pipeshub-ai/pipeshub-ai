import { BadRequestError } from '../libs/errors/http.errors';

/**
 * XSS (Cross-Site Scripting) sanitization utilities
 * Prevents XSS attacks by detecting and rejecting malicious input
 */

// Pattern to detect HTML tags and JavaScript event handlers
// Case-insensitive patterns to catch variations like <scrIpt>, <ScRiPt>, etc.
// Optimized to prevent ReDoS: use limited quantifiers to prevent exponential backtracking
// Max tag length: 10000 characters to prevent ReDoS
const HTML_TAG_PATTERN = /<[^>]{0,10000}>/i;
// Pattern matches: <script...>...</script> including </script > with spaces
// Uses [^>] instead of [\s\S] to prevent matching across tag boundaries and ReDoS
// Closing tag uses [^>]{0,10000} to match any characters (except >) between script and >
// This catches </script >, </script\t\n bar>, </script xyz>, etc.
const SCRIPT_TAG_PATTERN = /<[\s]*script[^>]{0,10000}>[\s\S]{0,100000}?<\/[\s]*script[^>]{0,10000}>/gi;
// More comprehensive script tag detection (case-insensitive, catches variations)
// Catches opening script tags with optional whitespace - uses [^>] to prevent ReDoS
const SCRIPT_TAG_VARIATIONS = /<[\s]*script[^>]{0,10000}>/gi;
// Explicit pattern for closing script tags - matches any characters (except >) between script and >
// This catches </script >, </script\t\n bar>, </script xyz>, etc.
// Limited to 10000 chars to prevent ReDoS
const SCRIPT_CLOSING_TAG_PATTERN = /<\/[\s]*script[^>]{0,10000}>/gi;
// Optimized to prevent ReDoS: limit attribute value length
const EVENT_HANDLER_PATTERN = /\b(on\w+\s*=\s*["']?[^"'>]{0,1000}["']?)/i;
const JAVASCRIPT_PROTOCOL_PATTERN = /javascript:/i;
const DATA_PROTOCOL_PATTERN = /data:\s*text\/html/i;
// Additional dangerous patterns - optimized to prevent ReDoS with limited quantifiers
const IFRAME_TAG_PATTERN = /<[\s]*iframe[^>]{0,10000}>/gi;
const OBJECT_TAG_PATTERN = /<[\s]*object[^>]{0,10000}>/gi;
const EMBED_TAG_PATTERN = /<[\s]*embed[^>]{0,10000}>/gi;
const SVG_TAG_PATTERN = /<[\s]*svg[^>]{0,10000}>/gi;

// Format string specifier pattern - matches %s, %x, %n, %1$s, %-10s, %.2f, etc.
// Pattern matches: % followed by optional positional (digits$), flags, width, precision, and type
// Optimized to prevent ReDoS: limited quantifiers and more specific patterns
// Max width: 10 digits, max precision: 10 digits to prevent exponential backtracking
const FORMAT_SPECIFIER_PATTERN = /%(?:\d{1,10}\$)?[#0\-+ ]{0,10}(?:\d{1,10})?(?:\.\d{1,10})?[diouxXeEfFgGaAcspn%a-zA-Z]/;

/**
 * Checks if a string contains potentially dangerous XSS patterns
 * @param value - The string to check
 * @returns true if XSS patterns are detected, false otherwise
 */
export function containsXSSPattern(value: string | undefined | null): boolean {
  if (!value || typeof value !== 'string') {
    return false;
  }

  // Input length limit to prevent ReDoS attacks
  // Reject extremely long inputs before regex matching
  if (value.length > 100000) {
    return true; // Treat as potentially dangerous if too long
  }

  // Normalize to lowercase for case-insensitive detection
  const normalized = value.toLowerCase();

  // Use simple string checks first to avoid expensive regex operations
  // Check for common dangerous patterns using indexOf (faster than regex)
  const hasLessThan = normalized.includes('<');
  const hasScript = normalized.includes('script');
  const hasIframe = normalized.includes('iframe');
  const hasObject = normalized.includes('object');
  const hasEmbed = normalized.includes('embed');
  const hasSvg = normalized.includes('svg');

  // Only use regex if simple checks indicate potential issues
  // Limit input to prevent ReDoS - only check first 10000 chars if input is very long
  const checkValue = value.length > 10000 ? value.substring(0, 10000) : value;
  const checkNormalized = normalized.length > 10000 ? normalized.substring(0, 10000) : normalized;

  // Check for HTML tags (case-insensitive) - only if '<' is present
  if (hasLessThan && HTML_TAG_PATTERN.test(checkValue)) {
    return true;
  }

  // Check for script tags (including case variations like <scrIpt>, <ScRiPt>)
  // Also check for closing tags with spaces like </script >
  // Only check if 'script' is present in the string
  if (hasScript) {
    if (
      SCRIPT_TAG_PATTERN.test(checkValue) ||
      SCRIPT_TAG_VARIATIONS.test(checkValue) ||
      SCRIPT_CLOSING_TAG_PATTERN.test(checkValue)
    ) {
      return true;
    }
  }

  // Check for other dangerous tags - only if tag names are present
  if (hasLessThan) {
    if (
      (hasIframe && IFRAME_TAG_PATTERN.test(checkValue)) ||
      (hasObject && OBJECT_TAG_PATTERN.test(checkValue)) ||
      (hasEmbed && EMBED_TAG_PATTERN.test(checkValue)) ||
      (hasSvg && SVG_TAG_PATTERN.test(checkValue))
    ) {
      return true;
    }
  }

  // Check for JavaScript event handlers (onerror, onclick, etc.)
  // Use simple check first - only use regex if 'on' is present
  if (checkNormalized.includes('on') && EVENT_HANDLER_PATTERN.test(checkNormalized)) {
    return true;
  }

  // Check for javascript: protocol - use simple check first
  if (checkNormalized.includes('javascript:') && JAVASCRIPT_PROTOCOL_PATTERN.test(checkNormalized)) {
    return true;
  }

  // Check for data: protocol with HTML - use simple check first
  if (checkNormalized.includes('data:') && DATA_PROTOCOL_PATTERN.test(checkNormalized)) {
    return true;
  }

  // Check for encoded script tags (common obfuscation techniques)
  if (
    normalized.includes('&lt;script') ||
    normalized.includes('%3cscript') ||
    normalized.includes('&#60;script') ||
    normalized.includes('&#x3c;script')
  ) {
    return true;
  }

  return false;
}

/**
 * Checks if a string contains format string specifiers
 * @param value - The string to check
 * @returns true if format specifiers are detected, false otherwise
 */
export function containsFormatSpecifiers(
  value: string | undefined | null,
): boolean {
  if (!value || typeof value !== 'string') {
    return false;
  }

  // Input length limit to prevent ReDoS attacks
  // Reject extremely long inputs before regex matching
  if (value.length > 100000) {
    return true; // Treat as potentially dangerous if too long
  }

  // Use simple string check first - only use regex if '%' is present
  if (!value.includes('%')) {
    return false;
  }

  // Limit input to prevent ReDoS - only check first 10000 chars if input is very long
  const checkValue = value.length > 10000 ? value.substring(0, 10000) : value;
  return FORMAT_SPECIFIER_PATTERN.test(checkValue);
}

/**
 * Validates that a string does not contain format string specifiers
 * @param value - The string to validate
 * @param fieldName - Name of the field being validated (for error message)
 * @throws BadRequestError if format specifiers are detected
 */
export function validateNoFormatSpecifiers(
  value: string | undefined | null,
  fieldName: string = 'input',
): void {
  if (containsFormatSpecifiers(value)) {
    throw new BadRequestError(
      `${fieldName} contains potentially dangerous format specifiers. Format specifiers like %s, %x, %n are not allowed.`,
    );
  }
}

/**
 * Validates that a string does not contain XSS patterns
 * @param value - The string to validate
 * @param fieldName - Name of the field being validated (for error message)
 * @throws BadRequestError if XSS patterns are detected
 */
export function validateNoXSS(
  value: string | undefined | null,
  fieldName: string = 'input',
): void {
  if (containsXSSPattern(value)) {
    throw new BadRequestError(
      `${fieldName} contains potentially dangerous content. HTML tags, scripts, and event handlers are not allowed.`,
    );
  }
}

/**
 * Validates that a string does not contain XSS patterns or format specifiers
 * @param value - The string to validate
 * @param fieldName - Name of the field being validated (for error message)
 * @throws BadRequestError if XSS patterns or format specifiers are detected
 */
export function validateNoXSSOrFormatSpecifiers(
  value: string | undefined | null,
  fieldName: string = 'input',
): void {
  validateNoXSS(value, fieldName);
  validateNoFormatSpecifiers(value, fieldName);
}

/**
 * Sanitizes a string by removing HTML tags and encoding special characters
 * Uses iterative replacement to prevent bypass attacks via nested patterns
 * Note: This is a basic sanitization. For production, consider using a library like DOMPurify
 * @param value - The string to sanitize
 * @returns Sanitized string
 */
export function sanitizeString(value: string | undefined | null): string {
  if (!value || typeof value !== 'string') {
    return '';
  }

  // Input length limit to prevent ReDoS attacks
  // Truncate extremely long inputs before processing
  const maxLength = 100000;
  let sanitized = value.length > maxLength ? value.substring(0, maxLength) : value;

  // Iterative removal of dangerous tags to prevent bypass via nested patterns
  // Example attack: <scr<script>ipt> becomes <script> after one pass
  // Solution: Keep replacing until no more changes occur
  const maxIterations = 10;
  let previousValue = '';
  let iterations = 0;

  // Keep replacing until the string stabilizes or max iterations reached
  while (sanitized !== previousValue && iterations < maxIterations) {
    previousValue = sanitized;
    iterations++;

    // Remove all dangerous tags in each iteration
    sanitized = sanitized
      // Remove script closing tags
      .replace(/<\/[\s]*script[^>]{0,10000}>/gi, '')
      // Remove script opening tags (complete and incomplete)
      .replace(/<[\s]*script[\s]*[^>]{0,10000}>?/gi, '')
      // Remove any remaining <script pattern
      .replace(/<[\s]*script/gi, '')
      // Remove iframe closing tags
      .replace(/<\/[\s]*iframe[^>]{0,10000}>/gi, '')
      // Remove iframe opening tags (complete and incomplete)
      .replace(/<[\s]*iframe[\s]*[^>]{0,10000}>?/gi, '')
      // Remove any remaining <iframe pattern
      .replace(/<[\s]*iframe/gi, '')
      // Remove object closing tags
      .replace(/<\/[\s]*object[^>]{0,10000}>/gi, '')
      // Remove object opening tags (complete and incomplete)
      .replace(/<[\s]*object[\s]*[^>]{0,10000}>?/gi, '')
      // Remove any remaining <object pattern
      .replace(/<[\s]*object/gi, '')
      // Remove embed tags (complete and incomplete)
      .replace(/<[\s]*embed[\s]*[^>]{0,10000}>?/gi, '')
      // Remove any remaining <embed pattern
      .replace(/<[\s]*embed/gi, '')
      // Remove svg closing tags
      .replace(/<\/[\s]*svg[^>]{0,10000}>/gi, '')
      // Remove svg opening tags (complete and incomplete)
      .replace(/<[\s]*svg[\s]*[^>]{0,10000}>?/gi, '')
      // Remove any remaining <svg pattern
      .replace(/<[\s]*svg/gi, '');
  }

  // Iteratively remove any remaining HTML tags until string stabilizes
  previousValue = '';
  iterations = 0;
  
  while (sanitized !== previousValue && iterations < maxIterations) {
    previousValue = sanitized;
    iterations++;
    // Remove any HTML-like tags
    sanitized = sanitized.replace(/<[^>]{0,10000}>/gi, '');
  }
  
  // Final pass: Encode special characters to prevent any remaining XSS
  // This ensures that any remaining < or > characters are safely encoded
  sanitized = sanitized
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;')
    .replace(/\//g, '&#x2F;');

  return sanitized;
}

/**
 * Sanitizes a value for safe inclusion in JSON responses
 * Recursively sanitizes strings in objects and arrays
 * @param value - The value to sanitize (can be any type)
 * @returns Sanitized value
 */
export function sanitizeForResponse(value: any): any {
  if (value === null || value === undefined) {
    return value;
  }

  if (typeof value === 'string') {
    return sanitizeString(value);
  }

  if (Array.isArray(value)) {
    return value.map((item) => sanitizeForResponse(item));
  }

  if (typeof value === 'object') {
    const sanitized: Record<string, any> = {};
    for (const key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        sanitized[key] = sanitizeForResponse(value[key]);
      }
    }
    return sanitized;
  }

  // For numbers, booleans, etc., return as-is
  return value;
}

/**
 * Validates and sanitizes a boolean query parameter
 * @param value - The query parameter value
 * @param fieldName - Name of the field being validated
 * @returns Validated boolean value
 * @throws BadRequestError if value is invalid or contains XSS
 */
export function validateBooleanParam(
  value: string | undefined | null,
  fieldName: string = 'parameter',
): boolean | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }

  // First check for XSS patterns
  validateNoXSS(value, fieldName);

  // Convert string to boolean
  const lowerValue = String(value).toLowerCase().trim();
  
  if (lowerValue === 'true' || lowerValue === '1') {
    return true;
  }
  if (lowerValue === 'false' || lowerValue === '0' || lowerValue === '') {
    return false;
  }

  throw new BadRequestError(
    `${fieldName} must be a valid boolean value (true/false)`,
  );
}