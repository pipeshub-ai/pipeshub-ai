import { expect } from 'chai'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'

describe('UserActivities schema', () => {
  it("indexes the session check's lookup in the order it filters and sorts", () => {
    const index = UserActivities.schema
      .indexes()
      .find(([, options]) => options?.name === 'session_ending_activity_lookup')

    expect(index).to.exist
    // Key order matters: equality fields, then the $in field, then the sort.
    expect(Object.entries(index![0])).to.deep.equal([
      ['userId', 1],
      ['orgId', 1],
      ['isDeleted', 1],
      ['activityType', 1],
      ['createdAt', -1],
    ])
  })
})
