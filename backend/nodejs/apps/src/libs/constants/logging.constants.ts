import path from 'path';

export const ENV_LOG_LEVEL = 'LOG_LEVEL';
export const ENV_LOG_DIR = 'LOG_DIR';


export const DEFAULT_LOG_LEVEL = 'info';
export const DEFAULT_LOG_DIR = path.join(process.cwd(), 'logs');

export const LOG_FILE_MAX_SIZE = 20 * 1024 * 1024;
export const ERROR_LOG_MAX_FILES = 5;
export const COMBINED_LOG_MAX_FILES = 10;
export const ERROR_LOG_FILENAME = 'error.log';
export const COMBINED_LOG_FILENAME = 'combined.log';
