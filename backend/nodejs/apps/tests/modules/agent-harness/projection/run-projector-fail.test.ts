import { expect } from 'chai';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { projectorContext, readJournal } from '../../../contracts/support/durable';

describe('agent-harness RunProjector.fail: Node could not finish a run\'s stream', () => {
  it('emits one RUN_ERROR with the code and message and ends the stream in error', () => {
    const projector = new RunProjector(projectorContext(readJournal('agent_artifact_marker_answer')));
    const frames = projector.fail('conversation_gone', 'This conversation was deleted');
    expect(frames).to.have.length(1);
    expect(JSON.stringify(frames[0])).to.include('RUN_ERROR');
    expect(JSON.stringify(frames[0])).to.include('conversation_gone');
    expect(projector.terminal).to.deep.equal({
      kind: 'error',
      code: 'conversation_gone',
      message: 'This conversation was deleted',
    });
    expect(projector.finish({}, {})).to.deep.equal([]);
  });
});
