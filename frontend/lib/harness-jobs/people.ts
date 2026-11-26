/**
 * How the app names a person or an agent it knows only by a stored id: by their name, else their email,
 * else a word ("Someone in your org"), never the id. Before this, a failed or partial name lookup left a Mongo or
 * graph id on screen (an approval's asker, an agent rule, a share-dialog row).
 */

type Words = (key: string, options?: Record<string, unknown>) => string;

/** A stored id (a graph key, a UUID or a Mongo object id), which a person cannot read. */
export const ID_LIKE = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24}|user:[0-9a-f-]{24,36})$/i;

const readable = (value: string | null | undefined): string | null => {
  const v = value?.trim();
  return v && !ID_LIKE.test(v) ? v : null;
};

/** A person: their name, their email, or "Someone in your org". */
export function personLabel(person: { name?: string | null; email?: string | null }, t: Words): string {
  return readable(person.name) ?? readable(person.email) ?? t('ui.common.someoneInOrg');
}

/** A name looked up by id: the name found, or "Someone in your org" when the lookup had none. */
export function personFromDirectory(names: Readonly<Record<string, string>>, id: string, t: Words): string {
  return readable(names[id]) ?? t('ui.common.someoneInOrg');
}

/** An agent looked up by key: its name, or "An agent" when the lookup had none. */
export function agentFromDirectory(names: Readonly<Record<string, string>>, key: string, t: Words): string {
  return readable(names[key]) ?? t('ui.common.anAgent');
}
