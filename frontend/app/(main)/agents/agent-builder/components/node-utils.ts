import { NODE_TYPES_WITHOUT_INPUT_HANDLES, NODE_TYPES_WITHOUT_OUTPUT_HANDLES } from './node-constants';

export function shouldShowInputHandles(nodeType: string): boolean {
  return !Object.values(NODE_TYPES_WITHOUT_INPUT_HANDLES).some((fn) => fn(nodeType));
}

export function shouldShowOutputHandles(nodeType: string): boolean {
  return !Object.values(NODE_TYPES_WITHOUT_OUTPUT_HANDLES).some((fn) => fn(nodeType));
}

export function calculateHandlePosition(
  index: number,
  total: number,
  baseOffset: number,
  increment: number
): string {
  if (total === 1) return '50%';
  const startPosition = baseOffset - ((total - 1) * increment) / 2;
  return `${startPosition + index * increment}%`;
}
