"""The org the simulator-mode suite syncs: small enough to check by eye.

Roles CEO > Sales Rep. Users: the integration admin (the connector's account), maya and raj
(reps), olga (no role, no access), gone (inactive). OWD Private. maya owns Acme and its
renewal deal (with contract.pdf and a Chatter thread); raj owns Globex and its deal; a case
on Acme owned by the admin; tasks under each deal and one under nothing; two products; a file
on the Acme account; a file on both raj's deal and the case.
"""

DOMAIN = "acme.com"


def scenario(admin_email: str | None = None) -> dict:
    """``admin_email``: the PipesHub user the suite signs in as, so that the API answers it as
    the Salesforce admin (who reads everything) when it streams record content."""
    return {
        "domain": DOMAIN,
        "roles": [{"key": "ceo", "name": "CEO"}, {"key": "rep", "name": "Sales Rep", "parent": "ceo"}],
        "users": [
            {"key": "admin", "first": "admin", "last": "Integration", "role": "ceo", "email": admin_email,
             "view_all": True, "modify_all": True},
            {"key": "maya", "first": "maya", "last": "Rao", "role": "rep"},
            {"key": "raj", "first": "raj", "last": "Iyer", "role": "rep"},
            {"key": "olga", "first": "olga", "last": "Novak"},
            {"key": "gone", "first": "gone", "last": "Former", "active": False},
        ],
        "accounts": [{"key": "acme", "name": "Acme Corp", "owner": "maya"},
                     {"key": "globex", "name": "Globex", "owner": "raj"}],
        "products": [{"key": "widget", "name": "Widget", "price": 99.0},
                     {"key": "gadget", "name": "Gadget", "price": 49.0}],
        "opportunities": [
            {"key": "renewal", "name": "Acme renewal", "owner": "maya", "account": "acme",
             "description": "Renewal for 2027"},
            {"key": "expansion", "name": "Globex expansion", "owner": "raj", "account": "globex"},
        ],
        "cases": [{"key": "login", "subject": "Login broken", "owner": "admin", "account": "acme",
                   "description": "Cannot sign in"}],
        "tasks": [{"key": "call", "subject": "Call Acme", "owner": "maya", "what": "renewal", "subtype": "Call"},
                  {"key": "followup", "subject": "Follow up", "owner": "raj", "what": "expansion"},
                  {"key": "reminder", "subject": "Personal reminder", "owner": "maya"}],
        "files": [{"key": "contract", "title": "contract.pdf", "text": "%PDF-1.4 renewal terms",
                   "owner": "maya", "links": ["renewal"]},
                  {"key": "brochure", "title": "brochure.txt", "text": "Acme brochure", "owner": "maya",
                   "links": ["acme"]},
                  {"key": "notes", "title": "notes.txt", "text": "Notes on the expansion and the login case",
                   "owner": "admin", "links": ["expansion", "login"]}],
        "posts": [{"key": "kickoff", "parent": "renewal", "text": "Kickoff done", "author": "maya",
                   "comments": [{"text": "Great work", "author": "raj"}]}],
        "connector_user": "admin",
        # PipesHub keeps its own time and syncs minutes apart in real time: Salesforce must too.
        "clock": "wall",
    }


def email(key: str) -> str:
    return f"{key}@{DOMAIN}"
