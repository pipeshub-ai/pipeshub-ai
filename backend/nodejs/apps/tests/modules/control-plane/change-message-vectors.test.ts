/**
 * CPD6-34: the bytes a change event travels as, held to the vector the Python
 * subscriber reads them from (`contracts/control-plane/vectors/change-message.json`,
 * `tests/unit/control_plane/test_change_message_vectors.py`).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import type { ChangeEvent } from '../../../src/modules/control-plane';
import { changeEventMessage } from '../../../src/modules/control-plane/domain/change-message';

interface Case {
  id: string;
  event: ChangeEvent;
  topic: string;
  key: string;
  envelope: string;
  brokerValue: string;
}

const { cases } = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/change-message.json'), 'utf8'),
) as { cases: Case[] };

describe('CPD6-34: a change event on the broker (contracts/control-plane/vectors/change-message.json)', () => {
  for (const vector of cases) {
    it(`${vector.id}: topic, key and value`, () => {
      const message = changeEventMessage(vector.event);

      expect(message.topic).to.equal(vector.topic);
      expect(message.key).to.equal(vector.key);
      expect(message.value).to.equal(vector.envelope);
      expect(JSON.stringify(message.value)).to.equal(vector.brokerValue);
    });
  }
});
