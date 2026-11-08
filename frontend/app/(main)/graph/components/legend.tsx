'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

const dot = (colour: string) => (
  <span aria-hidden="true" style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: colour, marginRight: 4 }} />
);

/** What the picture's marks mean, in words as well as colour (the table names every kind too). */
export function Legend() {
  const { t } = useTranslation();
  return (
    <Flex gap="3" wrap="wrap" align="center">
      <Text size="1" color="gray">{dot('#3f8f6b')}{t('graph.ui.legend.functions')}</Text>
      <Text size="1" color="gray">{dot('#4f6fd0')}{t('graph.ui.legend.classes')}</Text>
      <Text size="1" color="gray">{dot('#1c2220')}{t('graph.ui.legend.focus')}</Text>
      <Text size="1" color="gray">{t('graph.ui.legendInferred')}</Text>
      <Text size="1" color="gray">{t('graph.ui.disclosure')}</Text>
    </Flex>
  );
}
