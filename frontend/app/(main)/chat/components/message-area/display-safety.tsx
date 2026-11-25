'use client';

/**
 * SEC-B-04 (ADR-TEXT-01): what the card does with text that could pass for something else, shown to the person
 * deciding. A marker (`⟨U+202E⟩`) stands out from the text around it, and a note says once, in words, why it is
 * there and which host is a lookalike of which: the person sees what will happen and why the card looks odd.
 */
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { hasMarker, markInvisible, punycodeHosts } from '@/lib/utils/display-safe';

const MARKER = /(⟨U\+[0-9A-F]{4,6}⟩)/;

const MARKER_STYLE: React.CSSProperties = {
  fontFamily: 'var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, monospace)',
  fontSize: '0.85em',
  color: 'var(--amber-11)',
  background: 'var(--amber-a3)',
  borderRadius: 'var(--radius-1)',
  padding: '0 2px',
};

/** `text` (already display-safe) with each marker set apart, so it cannot be read as part of an address. */
export function MarkedText({ text }: { text: string }) {
  const { t } = useTranslation();
  if (!hasMarker(text)) return <>{text}</>;
  return (
    <>
      {text.split(MARKER).map((part, index) =>
        MARKER.test(part) ? (
          <span key={index} data-testid="display-marker" title={t('approvalCard.textCheck.markerTitle')} style={MARKER_STYLE}>
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

/** What the person should know about the card's text, or nothing when it is plain. */
export function displayNotes(texts: readonly string[]): { hidden: boolean; hosts: Array<{ ascii: string; unicode: string }> } {
  const hosts: Array<{ ascii: string; unicode: string }> = [];
  texts.forEach((text) =>
    punycodeHosts(text).forEach((host) => {
      // The Unicode form is the lookalike itself: anything in it a reader cannot see is a marker too.
      const unicode = markInvisible(host.unicode);
      if (!hosts.some((h) => h.ascii === host.ascii)) hosts.push({ ascii: host.ascii, unicode });
    }),
  );
  return { hidden: texts.some(hasMarker), hosts };
}

/** The note under the card's reason line: hidden characters were made visible, a host is a lookalike. */
export function DisplaySafetyNote({ texts }: { texts: readonly string[] }) {
  const { t } = useTranslation();
  const { hidden, hosts } = displayNotes(texts);
  if (!hidden && hosts.length === 0) return null;
  return (
    <Flex
      align="start"
      gap="2"
      role="note"
      data-testid="approval-text-check"
      style={{ padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-2)', background: 'var(--amber-a3)' }}
    >
      <MaterialIcon name="warning_amber" size={16} color="var(--amber-11)" />
      <Flex direction="column" gap="1">
        {hosts.map((host) => (
          <Text key={host.ascii} size="2" data-testid="approval-text-check-host" style={{ color: 'var(--amber-11)' }}>
            {t('approvalCard.textCheck.host', { ascii: host.ascii, unicode: host.unicode })}
          </Text>
        ))}
        {hidden ? (
          <Text size="2" data-testid="approval-text-check-hidden" style={{ color: 'var(--amber-11)' }}>
            {t('approvalCard.textCheck.hidden')}
          </Text>
        ) : null}
      </Flex>
    </Flex>
  );
}
