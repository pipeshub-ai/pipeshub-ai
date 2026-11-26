/**
 * Strip a run of the given characters from the start and/or end of a string, in linear time. A regex such as
 * `/x+$/` scans again from every position of a long run that does not end the string (quadratic on untrusted text).
 */
export function trimRun(
  text: string,
  chars: string,
  ends: { start?: boolean; end?: boolean } = { start: true, end: true },
): string {
  let from = 0;
  let to = text.length;
  if (ends.start)
    while (from < to && chars.includes(text.charAt(from))) from += 1;
  if (ends.end)
    while (to > from && chars.includes(text.charAt(to - 1))) to -= 1;
  return text.slice(from, to);
}
