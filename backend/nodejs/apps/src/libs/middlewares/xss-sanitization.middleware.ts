import { Request, Response, NextFunction } from 'express';
import xss from 'xss';
import { Logger } from '../services/logger.service';
import { BadRequestError } from '../errors/http.errors';
import {
  containsEncodedScriptTag,
  containsHTMLTag,
  containsXSSPattern,
  XSS_CHECK_MAX_LENGTH,
} from '../../utils/xss-sanitization';

/**
 * XSS Sanitization Middleware
 * 
 * Automatically sanitizes all string values in req.body, req.query, and req.params
 * to prevent XSS attacks. This middleware is non-breaking and safe for production.
 * 
 * Features:
 * - Recursively sanitizes nested objects and arrays
 * - Preserves data types (numbers, booleans, null, undefined)
 * - Handles edge cases safely
 * - Non-intrusive: continues even if sanitization fails
 * - Production-ready with proper error handling
 * 
 * Usage:
 *   // Apply globally (in app.ts after body parsing)
 *   app.use(xssSanitizationMiddleware);
 */

/**
 * Sanitizes a string using xss library to remove HTML/script tags
 * This is used for final sanitization after validation
 * @param value - The string to sanitize
 * @returns Sanitized string with HTML tags removed
 */
function sanitizeString(value: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    return value;
  }

  try {
    return xss(value, {
      stripIgnoreTag: true, // Remove tags that are not in the whitelist
      stripIgnoreTagBody: ['script'], // Remove script tag and its content
      whiteList: {}, // Empty whitelist means no HTML tags are allowed
    }).trim();
  } catch (error) {
    // If sanitization fails, return original value to avoid breaking the request
    Logger.getInstance().warn('XSS sanitization failed for string', {
      error: error instanceof Error ? error.message : String(error),
      valueLength: value.length,
    });
    return value;
  }
}

// Field paths are built from the request's own keys, so only plain names are
// repeated back; anything else (a key that is itself markup) is not echoed.
const SAFE_FIELD_PATH = /^[A-Za-z0-9_.[\]-]{1,200}$/;

/**
 * Turns a validation path such as `body.params.arguments.query` into the words
 * a caller needs to find the field: `the field "params.arguments.query"`,
 * `the query parameter "search"` or `the URL parameter "id"`.
 */
function describeField(path: string): string {
  const dot = path.indexOf('.');
  const source = dot === -1 ? path : path.slice(0, dot);
  const field = dot === -1 ? '' : path.slice(dot + 1);
  if (field === '' || !SAFE_FIELD_PATH.test(field)) {
    return 'the request';
  }
  if (source === 'query') {
    return `the query parameter "${field}"`;
  }
  if (source === 'params') {
    return `the URL parameter "${field}"`;
  }
  return `the field "${field}"`;
}

/**
 * Says what was refused and what to change. The angle-bracket hint is only
 * given when the value has something tag-shaped in it; a value refused for
 * `javascript:` or `onclick=` gets no tag hint, and an over-long value is
 * told it is too long rather than that it contains HTML.
 */
function xssRejectionMessage(value: string, field: string): string {
  if (value.length > XSS_CHECK_MAX_LENGTH) {
    return (
      `The value of ${field} is longer than ${XSS_CHECK_MAX_LENGTH.toLocaleString('en-US')} characters, ` +
      `which is too long to check for HTML tags and scripts. Please shorten it and try again.`
    );
  }
  if (containsHTMLTag(value)) {
    return (
      `HTML tags, scripts, and XSS content are not allowed in ${field}. ` +
      `Text in angle brackets, such as <id>, counts as an HTML tag. ` +
      `Please remove any HTML tags and try again.`
    );
  }
  if (containsEncodedScriptTag(value)) {
    return (
      `HTML tags, scripts, and XSS content are not allowed in ${field}. ` +
      `A script tag written in an encoded form, such as &lt;script or %3Cscript, counts. ` +
      `Please remove it and try again.`
    );
  }
  return (
    `HTML tags, scripts, and XSS content are not allowed in ${field}. ` +
    `Script content such as an onclick= handler, a javascript: link or a data:text/html URL counts. ` +
    `Please remove it and try again.`
  );
}

/**
 * The one field of a request that holds a person's question, when it is a
 * string. `holder` is the object the field sits on, so the field is found by
 * object identity rather than by a dotted path a caller could imitate with a
 * key such as "params.arguments.query".
 */
interface QuestionField {
  holder: Record<string, unknown>;
  key: string;
}

/** MCP tools whose `query` argument is a question typed for chat or search. */
const MCP_QUESTION_TOOLS = new Set(['pipeshub_chat', 'pipeshub_search']);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Object.prototype.toString.call(value) === '[object Object]';
}

/**
 * Finds the question on a chat or search tool call made through MCP, and on
 * the search route MCP calls, so that one string can be passed through
 * unchecked and unchanged.
 *
 * Why: a question is free text, and people (and AI agents) write things like
 * "cite [Summary of <id>]" or "what does List<String> return?". The web chat
 * already accepts such text, because its routes under /api/v1/conversations/
 * are skipped by this middleware entirely (see below). MCP's chat and search
 * were refused instead, because the MCP request itself (POST /mcp, the
 * question in `params.arguments.query`) and the search route MCP calls
 * (POST /api/v1/search, the question in `query`) were checked like any form.
 *
 * Why this is safe: the question is never rendered as HTML. It is stored as
 * plain text and the web app shows it, and the conversation title cut from
 * it, as React text, which escapes `<` and `>` (frontend
 * app/(main)/chat/components/message-area/expandable-user-query.tsx and
 * app/(main)/chat/sidebar/chat-section-element.tsx). It is also the same
 * text the web chat route already stores unchecked.
 *
 * Only that one string is passed through. Every other field of these
 * requests (filters, ids, model keys) and every field of every other request
 * is still checked and sanitized as before. Matching is exact and
 * case-sensitive, so a request that does not match is checked as before.
 */
function findQuestionField(req: Request): QuestionField | undefined {
  if (req.method !== 'POST' || !isPlainObject(req.body)) {
    return undefined;
  }
  const body = req.body;

  // The search route MCP's pipeshub_search calls, and the web search box.
  if (req.path === '/api/v1/search' || req.path === '/api/v1/search/') {
    return typeof body.query === 'string'
      ? { holder: body, key: 'query' }
      : undefined;
  }

  // A JSON-RPC tool call to the MCP endpoint.
  if (req.path === '/mcp' || req.path === '/mcp/') {
    const params = body.params;
    if (
      body.method !== 'tools/call' ||
      !isPlainObject(params) ||
      typeof params.name !== 'string' ||
      !MCP_QUESTION_TOOLS.has(params.name)
    ) {
      return undefined;
    }
    const args = params.arguments;
    return isPlainObject(args) && typeof args.query === 'string'
      ? { holder: args, key: 'query' }
      : undefined;
  }

  return undefined;
}

/**
 * True when `holder[key]` is the request's question field and is no longer
 * than XSS_CHECK_MAX_LENGTH. A longer question is not passed through: it is
 * checked like any other field, which refuses it as too long, so the
 * exemption never lifts the length ceiling the check enforces.
 */
function isQuestionField(
  holder: unknown,
  key: string,
  question: QuestionField | undefined,
): boolean {
  if (
    question === undefined ||
    holder !== question.holder ||
    key !== question.key
  ) {
    return false;
  }
  const text = question.holder[key];
  return typeof text === 'string' && text.length <= XSS_CHECK_MAX_LENGTH;
}

/**
 * Recursively checks for HTML/XSS content in an object or array
 * Throws an error if HTML is detected
 *
 * @param value - The value to check (can be any type)
 * @param path - Current path in the object (for error messages)
 * @param question - The request's question field, which is not checked
 * @throws BadRequestError if HTML/XSS content is detected
 */
function validateNoXSS(
  value: any,
  path: string = '',
  question?: QuestionField,
): void {
  // Handle null and undefined
  if (value === null || value === undefined) {
    return;
  }

  // Handle strings - check for HTML/XSS using utility function
  if (typeof value === 'string') {
    if (containsXSSPattern(value)) {
      throw new BadRequestError(
        xssRejectionMessage(value, describeField(path)),
      );
    }
    return;
  }

  // Handle primitive types (numbers, booleans, symbols, bigint) - return as-is
  if (
    typeof value !== 'object' ||
    value instanceof Buffer ||
    value instanceof Date ||
    value instanceof RegExp ||
    value instanceof Error
  ) {
    return;
  }

  // Handle arrays - validate each element
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      validateNoXSS(item, path ? `${path}[${index}]` : `[${index}]`, question);
    });
    return;
  }

  // Handle plain objects - validate each property
  // Check for plain objects (not class instances, Buffers, Dates, etc.)
  if (Object.prototype.toString.call(value) === '[object Object]') {
    for (const key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        const propValue = value[key];
        
        // Skip file upload objects (multer file objects)
        // These have specific properties like buffer, mimetype, originalname, etc.
        if (
          propValue &&
          typeof propValue === 'object' &&
          (propValue.buffer instanceof Buffer ||
           (propValue.fieldname && propValue.originalname))
        ) {
          // This is likely a multer file object - skip validation
          continue;
        }

        // A chat or search question is free text, never rendered as HTML.
        // See findQuestionField for why it is not checked.
        if (isQuestionField(value, key, question)) {
          continue;
        }

        const fieldPath = path ? `${path}.${key}` : key;
        validateNoXSS(propValue, fieldPath, question);
      }
    }
    return;
  }

  // For other types (numbers, booleans, class instances, etc.), no validation needed
  return;
}

/**
 * Recursively sanitizes all string values in an object or array
 * This is used after validation passes to ensure clean data
 * Preserves data structure and types
 * 
 * @param value - The value to sanitize (can be any type)
 * @param question - The request's question field, which is kept unchanged
 * @returns Sanitized value with same structure
 */
function sanitizeValue(value: any, question?: QuestionField): any {
  // Handle null and undefined
  if (value === null || value === undefined) {
    return value;
  }

  // Handle strings - sanitize
  if (typeof value === 'string') {
    return sanitizeString(value);
  }

  // Handle primitive types (numbers, booleans, symbols, bigint) - return as-is
  if (
    typeof value !== 'object' ||
    value instanceof Buffer ||
    value instanceof Date ||
    value instanceof RegExp ||
    value instanceof Error
  ) {
    return value;
  }

  // Handle arrays - sanitize each element
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeValue(item, question));
  }

  // Handle plain objects - sanitize each property
  // Check for plain objects (not class instances, Buffers, Dates, etc.)
  if (Object.prototype.toString.call(value) === '[object Object]') {
    const sanitized: Record<string, any> = {};
    for (const key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        try {
          const propValue = value[key];
          
          // Skip file upload objects (multer file objects)
          // These have specific properties like buffer, mimetype, originalname, etc.
          if (
            propValue &&
            typeof propValue === 'object' &&
            (propValue.buffer instanceof Buffer ||
             (propValue.fieldname && propValue.originalname))
          ) {
            // This is likely a multer file object - preserve it as-is
            sanitized[key] = propValue;
            continue;
          }

          // Kept as typed: stripping tags would silently change the question,
          // turning "[Summary of <id>]" into "[Summary of ]". See
          // findQuestionField for why this is safe.
          if (isQuestionField(value, key, question)) {
            sanitized[key] = propValue;
            continue;
          }

          sanitized[key] = sanitizeValue(propValue, question);
        } catch (error) {
          // If sanitizing a property fails, keep the original value
          Logger.getInstance().warn('XSS sanitization failed for property', {
            key,
            error: error instanceof Error ? error.message : String(error),
          });
          sanitized[key] = value[key];
        }
      }
    }
    return sanitized;
  }

  // For other types (class instances, etc.), return as-is
  // This preserves file uploads, binary data, and other non-string types
  return value;
}

/**
 * XSS Sanitization Middleware
 * 
 * Sanitizes all string inputs in req.body, req.query, and req.params
 * This middleware is safe to use globally and won't break existing functionality.
 * 
 * It runs after body parsing but before route handlers, ensuring all user input
 * is sanitized before reaching controllers.
 */
export const xssSanitizationMiddleware = (
  req: Request,
  _res: Response,
  next: NextFunction,
): void => {
  try {
    // First, validate that no HTML/XSS content exists (reject if found)
    // This prevents HTML from being stored in the database
    
    if (req.path.startsWith('/api/v1/agents/') && (req.method === 'POST' || req.method === 'PUT')) {
      next();
      return;
    }

    if (req.path.startsWith('/api/v1/conversations/') && (req.method === 'POST' || req.method === 'PUT')) {
      next();
      return;
    }

    if (req.path.startsWith('/api/v1/connectors') && (req.method === 'POST' || req.method === 'PUT')) {
      next();
      return;
    }

    // Skills persist SKILL.md and resource files that routinely contain HTML/JS
    // samples (e.g. anthropics/skills pptx). Same exemption as agents.
    if (
      (req.path === '/api/v1/skills' || req.path.startsWith('/api/v1/skills/')) &&
      (req.method === 'POST' || req.method === 'PUT' || req.method === 'PATCH')
    ) {
      next();
      return;
    }

    // Validate request body
    if (req.body && typeof req.body === 'object' && !Array.isArray(req.body)) {
      // Skip if body is a Buffer (file upload)
      if (!(req.body instanceof Buffer)) {
        // A chat or search question made through MCP is passed through as
        // the web chat's is; every other body field is still checked.
        const question = findQuestionField(req);
        validateNoXSS(req.body, 'body', question);
        // After validation passes, sanitize to ensure clean data
        req.body = sanitizeValue(req.body, question);
      }
    }

    // Validate query parameters
    if (req.query && typeof req.query === 'object' && !Array.isArray(req.query)) {
      validateNoXSS(req.query, 'query');
      // After validation passes, sanitize to ensure clean data
      req.query = sanitizeValue(req.query) as typeof req.query;
    }

    // Validate route parameters
    if (req.params && typeof req.params === 'object' && !Array.isArray(req.params)) {
      validateNoXSS(req.params, 'params');
      // After validation passes, sanitize to ensure clean data
      req.params = sanitizeValue(req.params) as typeof req.params;
    }

    // Continue to next middleware/route handler
    next();
  } catch (error) {
    // If it's a BadRequestError (our validation error), pass it to error handler
    if (error instanceof BadRequestError) {
      return next(error);
    }

    // For other errors, log but don't break the request
    // This ensures the middleware is non-breaking even if something goes wrong
    Logger.getInstance().error('XSS sanitization middleware error', {
      error: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
      path: req.path,
      method: req.method,
    });

    // Continue with the request even if sanitization fails
    // This ensures no breaking changes in production
    next();
  }
};

