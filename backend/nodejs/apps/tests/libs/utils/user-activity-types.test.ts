import { expect } from 'chai';
import fs from 'fs';
import path from 'path';
import mongoose from 'mongoose';
import {
  USER_ACTIVITY_TYPES,
  USER_LOGIN_MODES,
  userActivitiesType,
} from '../../../src/libs/utils/userActivities.utils';
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema';
import { checkActivity } from '../../../src/libs/repositories/activity/pg';

// R46FIX-1: WP-73 added ACCOUNT DISABLED / ENABLED to the utils but not to the
// document schema, so every deactivation answered 503 on Mongo. Each store must
// accept every type and login mode the code can write.

const MIGRATIONS = path.resolve(__dirname, '../../../../../db/versions');

function sqlFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return sqlFiles(p);
    return e.name.endsWith('.sql') ? [p] : [];
  });
}

function row(activityType: string, loginMode?: string) {
  return {
    email: 'a@b.test',
    userId: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(),
    activityType,
    ipAddress: '127.0.0.1',
    ...(loginMode ? { loginMode } : {}),
  };
}

describe('R46FIX-1: user activity types are accepted by every store', () => {
  it('lists every type the utils define', () => {
    expect([...USER_ACTIVITY_TYPES].sort()).to.deep.equal(
      Object.values(userActivitiesType).sort(),
    );
    expect(USER_ACTIVITY_TYPES).to.include.members(['ACCOUNT DISABLED', 'ACCOUNT ENABLED']);
  });

  for (const type of Object.values(userActivitiesType)) {
    it(`the document schema accepts ${type}`, () => {
      const err = new UserActivities(row(type)).validateSync();
      expect(err, String(err)).to.equal(undefined);
    });

    it(`the PostgreSQL store accepts ${type}`, () => {
      expect(() => checkActivity(row(type) as never)).not.to.throw();
    });
  }

  for (const mode of USER_LOGIN_MODES) {
    it(`both stores accept login mode ${mode}`, () => {
      expect(new UserActivities(row('LOGIN', mode)).validateSync()).to.equal(undefined);
      expect(() => checkActivity(row('LOGIN', mode) as never)).not.to.throw();
    });
  }

  it('both stores still refuse a type nobody defined', () => {
    expect(new UserActivities(row('NOT A TYPE')).validateSync()).to.not.equal(undefined);
    expect(() => checkActivity(row('NOT A TYPE') as never)).to.throw(/unknown activity type/);
  });

  it('a database constraint on activity_type, if a migration adds one, names every type', () => {
    const files = sqlFiles(MIGRATIONS);
    expect(files.length).to.be.greaterThan(0);
    for (const file of files) {
      const sql = fs.readFileSync(file, 'utf8');
      const checks = sql.match(/CHECK\s*\([^;]*activity_type[^;]*\)/gi) ?? [];
      for (const check of checks) {
        for (const type of USER_ACTIVITY_TYPES) {
          expect(check, `${path.basename(file)} omits ${type}`).to.include(`'${type}'`);
        }
      }
    }
  });
});
