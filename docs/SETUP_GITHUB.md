# Set up GitHub sign-in

KAIZEN uses a GitHub **OAuth App**. It takes about five minutes.

Without it, the app still runs: **Sign in with GitHub** signs you in as the demo user, and the demo data stays in place.

## 1. Create the OAuth App

1. Open <https://github.com/settings/developers> and choose **OAuth Apps**, then **New OAuth App**.
   For a team account, use the organisation's settings page instead: **Settings**, **Developer settings**, **OAuth Apps**.
2. Fill in the form:

   | Field | Value |
   |---|---|
   | Application name | KAIZEN (local) |
   | Homepage URL | `http://localhost:8000` |
   | Authorization callback URL | `http://localhost:8000/auth/callback` |
   | Enable Device Flow | Leave off |

3. Choose **Register application**.
4. Copy the **Client ID**.
5. Choose **Generate a new client secret** and copy the secret straight away. GitHub only shows it once.

Make one OAuth App for each place you run the app, for example one for `localhost:8000` and one for the deployed URL. A GitHub OAuth App has a single callback URL.

## 2. Configure KAIZEN

```bash
cp .env.example .env
```

Then fill in `.env`:

```dotenv
GITHUB_CLIENT_ID=<client id>
GITHUB_CLIENT_SECRET=<client secret>
GITHUB_REDIRECT_URI=http://localhost:8000/auth/callback
SESSION_SECRET=<output of: .venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(32))">
```

- `GITHUB_REDIRECT_URI` must match the callback URL from step 1 exactly.
- Open the app on the same host as the callback. If the callback says `localhost`, browse to `http://localhost:8000`, not `http://127.0.0.1:8000`. The browser keeps cookies per host, so a mismatch shows "state" errors after GitHub sends you back.
- Set `SESSION_SECRET` to a fixed value, or everyone is signed out each time the server restarts.
- `GITHUB_SCOPES` defaults to `read:user repo`. `repo` is needed for private repositories and for opening fix pull requests. For public repositories only, use `read:user public_repo`.
- Set `DEMO_MODE=0` if you do not want the demo account and demo reset to be available.

## 3. Run it

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/auth/login>. GitHub asks you to authorise the app, then sends you back to:

- `/onboarding` the first time, to pick your role, your repository and describe the app.
- `/dashboard` after that.

## How sign-in works

1. `/auth/login` makes a random `state` value, keeps it in the signed session cookie and sends you to GitHub.
2. GitHub sends you back to `/auth/callback` with a `code` and the same `state`. A missing or different `state` is rejected, which stops cross-site request forgery.
3. The server swaps the `code` for an access token and reads your GitHub profile.
4. The token is stored in the `tokens` table in SQLite and never sent to the browser. The session cookie only holds your GitHub login.
5. `/auth/logout` deletes the stored token and clears the session.

Your GitHub login is matched against commit authors automatically, so your commits show up as yours.

## Troubleshooting

| What you see | What to do |
|---|---|
| GitHub says "The redirect_uri is not associated with this application" | `GITHUB_REDIRECT_URI` does not match the OAuth App's callback URL. |
| You land on `/login?error=state` | You started on a different host (see step 2), or the server restarted without a fixed `SESSION_SECRET`. Sign in again. |
| You land on `/login?error=github` | The client secret is wrong or expired. Generate a new one. |
| You land on `/login?error=denied` | You chose Cancel on GitHub. |
| Sync says the repository was not found | The account cannot see that repo, or the token lacks the `repo` scope for a private repo. Sign out and in again after changing `GITHUB_SCOPES`. |
| An organisation repository is missing | The organisation may restrict OAuth Apps. An owner can approve KAIZEN under the organisation's **Third-party access** settings. |
