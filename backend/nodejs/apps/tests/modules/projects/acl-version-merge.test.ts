import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { ACL_VERSION_INC } from '../../../src/modules/authz/cache/acl-version';
import { memberPrincipalKey, projectFactsOf } from '../../../src/modules/authz/loaders/project.loader';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';

const oid = () => new mongoose.Types.ObjectId();

describe('PH-03 merge: member shape and aclVersion bumps', () => {
  it('memberPrincipalKey reads user ObjectIds, string team ids and legacy ObjectId team rows', () => {
    const user = oid();
    const legacy = oid();
    expect(memberPrincipalKey({ principalType: 'user', principalId: user } as any)).to.equal(user.toString());
    expect(memberPrincipalKey({ principalType: 'team', teamId: 'all_org' } as any)).to.equal('all_org');
    expect(memberPrincipalKey({ principalType: 'team', principalId: legacy } as any)).to.equal(legacy.toString());
  });

  it('projectFactsOf carries the H2 ceiling and defaults an unset one to null', () => {
    const base = { orgId: oid(), userId: oid(), visibility: 'private', members: [] } as any;
    expect(projectFactsOf({ ...base, projectChatAccess: 'editor' }).projectChatAccess).to.equal('editor');
    expect(projectFactsOf(base).projectChatAccess).to.equal(null);
  });

  it('an update that mixes plain fields with ACL_VERSION_INC casts to $set plus $inc (share/unshare)', () => {
    const id = oid();
    const query: any = ChatSession.findOneAndUpdate(
      { _id: id },
      { sharedWith: [{ userId: id, accessLevel: 'read' }], isShared: true, ...ACL_VERSION_INC },
      { new: true },
    );
    const cast = query._castUpdate(query._update);
    expect(cast.$inc).to.deep.equal({ aclVersion: 1 });
    expect(cast.$set.isShared).to.equal(true);
    expect(cast.$set.sharedWith).to.have.length(1);
  });
});
