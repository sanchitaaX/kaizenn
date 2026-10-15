"""Demo data used to render every screen before the real backend is wired in.

The shapes here are the team contract (see BRIEF.md section 12). The backend
must produce the same keys, so templates work with fixtures and real data alike.
"""

USER = {
    "login": "dhruv-carvelle",
    "name": "Dhruv",
    "avatar_url": "/avatar/dhruv-carvelle.svg",
    "role": "student",           # "student" | "professional"
    "report_mode": False,        # professional default
    "learn_mode": True,          # student default
    "repos": [
        "carvelle/marks-portal",
        "carvelle/hostel-booking",
        "carvelle/canteen-api",
        "carvelle/library-app",
        "carvelle/attendance-tracker",
    ],
}

REPO = {
    "full_name": "carvelle/marks-portal",
    "default_branch": "main",
    "last_synced": "2 minutes ago",
}

# More demo repositories for the demo user, so the chat can show a "top 3" and an "All repositories" list.
# Their commits are served by the demo sync (db.demo_sync) the first time a repo is opened, as New commits,
# and a scan of any of them replays a calm "safe" result (engine.replay.fixture_report).
EXTRA_REPOS = [
    {
        "full_name": "carvelle/hostel-booking", "default_branch": "main",
        "commits": [
            {"sha": "a1c93e7", "author": "rahul-dev", "message": "Add room availability calendar", "time": "25 min ago"},
            {"sha": "3be84d0", "author": "priya-codes", "message": "Send a booking confirmation email", "time": "2 hr ago"},
            {"sha": "d702f5a", "author": "aman-k", "message": "Add a warden approval step", "time": "Yesterday"},
            {"sha": "6f1b2c9", "author": "dhruv-carvelle", "message": "Set up the Flask project and login", "time": "3 days ago"},
        ],
    },
    {
        "full_name": "carvelle/canteen-api", "default_branch": "main",
        "commits": [
            {"sha": "8e5d0a1", "author": "aman-k", "message": "Add the menu endpoint with daily specials", "time": "1 hr ago"},
            {"sha": "24c7b9f", "author": "priya-codes", "message": "Add order status updates", "time": "5 hr ago"},
            {"sha": "b90e3d6", "author": "rahul-dev", "message": "Cache the menu for faster loads", "time": "Yesterday"},
        ],
    },
    {
        "full_name": "carvelle/library-app", "default_branch": "main",
        "commits": [
            {"sha": "17d8f4c", "author": "dhruv-carvelle", "message": "Add book search by title", "time": "50 min ago"},
            {"sha": "c5a6e82", "author": "priya-codes", "message": "Show due dates on the borrowed list", "time": "4 hr ago"},
            {"sha": "0d39b7a", "author": "aman-k", "message": "Add return and renew buttons", "time": "2 days ago"},
            {"sha": "e8f2c14", "author": "rahul-dev", "message": "Import the starting book catalogue", "time": "4 days ago"},
        ],
    },
    {
        "full_name": "carvelle/attendance-tracker", "default_branch": "main",
        "commits": [
            {"sha": "5a07d3e", "author": "rahul-dev", "message": "Mark attendance from the class list", "time": "3 hr ago"},
            {"sha": "f46b8c2", "author": "aman-k", "message": "Export weekly attendance as CSV", "time": "Yesterday"},
            {"sha": "92e1a0d", "author": "priya-codes", "message": "Add a low-attendance warning", "time": "2 days ago"},
        ],
    },
]

MEMBERS = [
    {"login": "dhruv-carvelle", "name": "Dhruv", "avatar_url": "/avatar/dhruv-carvelle.svg"},
    {"login": "rahul-dev", "name": "Rahul", "avatar_url": "/avatar/rahul-dev.svg"},
    {"login": "priya-codes", "name": "Priya", "avatar_url": "/avatar/priya-codes.svg"},
    {"login": "aman-k", "name": "Aman", "avatar_url": "/avatar/aman-k.svg"},
]

# States: new | safe | fixed | attention
COMMITS = [
    {
        "sha": "e41b9d2", "author": "dhruv-carvelle", "message": "Add student name search to admin panel",
        "time": "4 min ago", "state": "fixed", "bug_type": "SQL_INJECTION", "repeat_count": 2,
    },
    {
        "sha": "c7f0a13", "author": "priya-codes", "message": "Add /marks/<id> endpoint for report cards",
        "time": "38 min ago", "state": "fixed", "bug_type": "IDOR", "repeat_count": 1,
    },
    {
        "sha": "9a2e6f4", "author": "aman-k", "message": "Let students download their report card PDF",
        "time": "1 hr ago", "state": "attention", "bug_type": "PATH_TRAVERSAL", "repeat_count": 1,
    },
    {
        "sha": "5d8c1b0", "author": "rahul-dev", "message": "Search students by name",
        "time": "2 hr ago", "state": "fixed", "bug_type": "SQL_INJECTION", "repeat_count": 1,
    },
    {
        "sha": "b3e7712", "author": "priya-codes", "message": "Style the marks table",
        "time": "3 hr ago", "state": "safe", "bug_type": None, "repeat_count": 0,
    },
    {
        "sha": "0f4a9c8", "author": "rahul-dev", "message": "Add logout button",
        "time": "5 hr ago", "state": "new", "bug_type": None, "repeat_count": 0,
    },
    {
        "sha": "71c2e05", "author": "aman-k", "message": "Initial Flask app with login",
        "time": "Yesterday", "state": "safe", "bug_type": None, "repeat_count": 0,
    },
]

BUG_TYPES = {
    "SQL_INJECTION": {"name": "SQL injection", "plain": "Typed text can rewrite the database query."},
    "PATH_TRAVERSAL": {"name": "Path traversal", "plain": "A file name can reach files outside the allowed folder."},
    "IDOR": {"name": "Broken access control", "plain": "A user can open data that belongs to someone else."},
}

# Full reports keyed by sha. Steps: find, hack, fix, prove.
# Step status: idle | running | attack | pass | fail
REPORTS = {
    "e41b9d2": {
        "commit_sha": "e41b9d2",
        "author": "dhruv-carvelle",
        "state": "fixed",
        "bug_type": "SQL_INJECTION",
        "file": "app/admin.py",
        "line": 42,
        "explanation": "The admin search box puts whatever you type straight into the database query. "
                       "Typing ' OR '1'='1 makes the query return every student, without any check.",
        "steps": {
            "find": {"status": "pass", "note": "Found in admin.py, line 42"},
            "hack": {"status": "attack", "note": "Attack worked: all 50 students returned"},
            "fix": {"status": "pass", "note": "Patched on attempt 1"},
            "prove": {"status": "pass", "note": "Attack blocked, 12/12 tests pass"},
        },
        "attack": {
            "before": "SUCCESS",
            "payload": "' OR '1'='1",
            "evidence": "GET /admin/search?q=' OR '1'='1  →  200 OK, 50 rows (expected 0)",
        },
        "attack_after": "BLOCKED",
        "fix_diff": (
            "--- a/app/admin.py\n"
            "+++ b/app/admin.py\n"
            "@@ -40,5 +40,5 @@ def search_students():\n"
            "     q = request.args.get(\"q\", \"\")\n"
            "-    sql = f\"SELECT * FROM students WHERE name LIKE '%{q}%'\"\n"
            "-    rows = db.execute(sql).fetchall()\n"
            "+    sql = \"SELECT * FROM students WHERE name LIKE ?\"\n"
            "+    rows = db.execute(sql, (f\"%{q}%\",)).fetchall()\n"
            "     return render_template(\"search.html\", rows=rows)\n"
        ),
        "tests": {"passed": 12, "total": 12},
        "attempts": 1,
        "duration": "1 min 48 s",
        "repeat_count": 2,
        "last_seen": {"sha": "5d8c1b0", "time": "2 hr ago"},
        "rule": None,
        "pull_request": {"number": 14, "url": "#", "status": "open"},
        "learning": [
            {"kind": "Video", "title": "SQL injection explained in 7 minutes", "source": "Computerphile", "url": "https://www.youtube.com/watch?v=ciNHn38EyRc", "minutes": 7},
            {"kind": "Guide", "title": "Query parameterization cheat sheet", "source": "OWASP", "url": "https://cheatsheetseries.owasp.org/cheatsheets/Query_Parameterization_Cheat_Sheet.html", "minutes": 10},
            {"kind": "Practice lab", "title": "SQL injection: retrieve hidden data", "source": "PortSwigger Academy", "url": "https://portswigger.net/web-security/sql-injection/lab-retrieve-hidden-data", "minutes": 15},
        ],
    },
    "c7f0a13": {
        "commit_sha": "c7f0a13",
        "author": "priya-codes",
        "state": "fixed",
        "bug_type": "IDOR",
        "file": "app/marks.py",
        "line": 18,
        "explanation": "Any logged-in student can open /marks/<id> for any id. The code checks that you are "
                       "logged in, but not that the marks are yours. Scanners missed this; it breaks the rule "
                       "\"A student can only see their own marks\" from your project brief.",
        "steps": {
            "find": {"status": "pass", "note": "Breaks rule 2 from your brief"},
            "hack": {"status": "attack", "note": "Logged in as Rahul, opened Priya's marks"},
            "fix": {"status": "pass", "note": "Ownership check added"},
            "prove": {"status": "pass", "note": "Attack blocked, 12/12 tests pass"},
        },
        "attack": {
            "before": "SUCCESS",
            "payload": "GET /marks/7 as student id 3",
            "evidence": "Logged in as rahul (id 3) → GET /marks/7 → 200 OK, showed Priya's marks",
        },
        "attack_after": "BLOCKED",
        "fix_diff": (
            "--- a/app/marks.py\n"
            "+++ b/app/marks.py\n"
            "@@ -16,4 +16,6 @@ def view_marks(student_id):\n"
            "     if not current_user():\n"
            "         return redirect(\"/login\")\n"
            "+    if current_user().id != student_id and not current_user().is_teacher:\n"
            "+        abort(403)\n"
            "     marks = get_marks(student_id)\n"
        ),
        "tests": {"passed": 12, "total": 12},
        "attempts": 1,
        "duration": "2 min 05 s",
        "repeat_count": 1,
        "last_seen": None,
        "rule": "A student can only see their own marks.",
        "pull_request": {"number": 13, "url": "#", "status": "merged"},
        "learning": [
            {"kind": "Guide", "title": "Broken access control", "source": "OWASP Top 10", "url": "https://owasp.org/Top10/A01_2021-Broken_Access_Control/", "minutes": 8},
            {"kind": "Practice lab", "title": "User ID controlled by request parameter", "source": "PortSwigger Academy", "url": "https://portswigger.net/web-security/access-control/lab-user-id-controlled-by-request-parameter", "minutes": 15},
        ],
    },
    "9a2e6f4": {
        "commit_sha": "9a2e6f4",
        "author": "aman-k",
        "state": "attention",
        "bug_type": "PATH_TRAVERSAL",
        "file": "app/reports.py",
        "line": 27,
        "explanation": "The download link takes a file name from the URL and opens it directly. "
                       "Asking for ../../app/config.py downloads the app's secret settings.",
        "steps": {
            "find": {"status": "pass", "note": "Found in reports.py, line 27"},
            "hack": {"status": "attack", "note": "Attack worked: config.py downloaded"},
            "fix": {"status": "fail", "note": "2 patches tried, both broke tests"},
            "prove": {"status": "idle", "note": "Waiting for a working fix"},
        },
        "attack": {
            "before": "SUCCESS",
            "payload": "../../app/config.py",
            "evidence": "GET /reports/download?file=../../app/config.py → 200 OK, SECRET_KEY exposed",
        },
        "attack_after": "SUCCESS",
        "fix_diff": "",
        "tests": {"passed": 10, "total": 12},
        "attempts": 3,
        "duration": "4 min 12 s",
        "repeat_count": 1,
        "last_seen": None,
        "rule": None,
        "pull_request": None,
        "learning": [
            {"kind": "Guide", "title": "Path traversal", "source": "OWASP", "url": "https://owasp.org/www-community/attacks/Path_Traversal", "minutes": 6},
            {"kind": "Practice lab", "title": "File path traversal, simple case", "source": "PortSwigger Academy", "url": "https://portswigger.net/web-security/file-path-traversal/lab-simple", "minutes": 10},
        ],
    },
}

BRIEF = {
    "summary": "A marks portal for our college. Students log in to see their marks and download report cards. Teachers enter marks.",
    "users": "Student, Teacher, Admin",
    "permissions": "Students see only their own marks. Teachers can edit marks for their own classes. Only admins can add or delete users.",
    "updated": "Today, 10:42",
}

RULES = [
    {"id": 1, "text": "Only admins can add or delete users.", "enabled": True, "hits": 0},
    {"id": 2, "text": "A student can only see their own marks.", "enabled": True, "hits": 1},
    {"id": 3, "text": "Teachers can only edit marks for classes they teach.", "enabled": True, "hits": 0},
    {"id": 4, "text": "Report cards are only downloadable by their owner or a teacher.", "enabled": True, "hits": 0},
    {"id": 5, "text": "Passwords and secret keys are never sent to the browser.", "enabled": False, "hits": 0},
]

MISTAKES = [
    {"bug_type": "SQL_INJECTION", "count": 2, "last_sha": "e41b9d2", "last_time": "4 min ago"},
    {"bug_type": "PATH_TRAVERSAL", "count": 1, "last_sha": "9a2e6f4", "last_time": "1 hr ago"},
]


def member(login):
    return next((m for m in MEMBERS if m["login"] == login), {"login": login, "name": login, "avatar_url": f"/avatar/{login}.svg"})


def extra_repo(full_name):
    return next((r for r in EXTRA_REPOS if r["full_name"] == full_name), None)


def state_counts(commits=None):
    commits = commits if commits is not None else COMMITS
    counts = {"new": 0, "safe": 0, "fixed": 0, "attention": 0}
    for c in commits:
        counts[c["state"]] += 1
    return counts
