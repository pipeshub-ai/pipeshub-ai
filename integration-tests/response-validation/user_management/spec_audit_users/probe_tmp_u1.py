def test_cleanup(users_client):
    uid = "6ac4df9c136650a2bd85879e"
    r = users_client.update_user(uid, role="member")
    print("\n### demote", r.status_code, r.text[:200])
    r = users_client.delete_user(uid)
    print("### delete", r.status_code, r.text[:200])
