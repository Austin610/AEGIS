# GitHub sign-in and GitHub Actions

The project is published at https://github.com/Austin610/AEGIS on `main`, with
AEGIS at the repository root. GitHub Actions is connected and all four jobs passed on 2026-09-22.
[Verification evidence](https://github.com/Austin610/AEGIS/actions/runs/35709166879) covers commit `b953bf8`. Live GitHub OAuth acceptance still requires the configuration below.

## Sign-in configuration

Register a GitHub OAuth App and configure these environment variables on the
machine running AEGIS before starting the server:

| Variable | Required value |
| --- | --- |
| `AEGIS_GITHUB_CLIENT_ID` | OAuth App client ID |
| `AEGIS_GITHUB_CLIENT_SECRET` | OAuth App secret, supplied through the environment; never commit it |
| `AEGIS_GITHUB_CALLBACK` | `http://127.0.0.1:8766/api/auth/github/callback`, matching the OAuth App callback |
| `AEGIS_GITHUB_ROLES_FILE` | Absolute path to a local JSON file mapping GitHub numeric account IDs to AEGIS roles |

Example roles file (replace the sample ID and workspace UUID):

```json
{
  "123456": {
    "role": "analyst",
    "workspace_ids": ["11111111-1111-4111-8111-111111111111"]
  }
}
```

Allowed roles are `reader`, `analyst`, and `admin`. Administrators use an empty
workspace list and have global access. Other roles require explicit workspace IDs.
Use stable numeric account IDs, not changeable usernames. Membership in a GitHub
repository or organization does not automatically grant access to AEGIS.

When all four settings are present, the sign-in page offers GitHub login. The flow
uses PKCE S256, a browser-bound one-use state with a ten-minute expiry, and requests
only `read:user`. Provider tokens are used to identify the account, then discarded.
Local sessions expire after eight hours. The roles file is checked on every
authenticated request: removing an account or changing its role applies to its
next request. GitHub-side OAuth revocation does not automatically invalidate an
already issued local session; remove the local role or restart the server for
immediate local revocation. This flow does not claim to enforce GitHub MFA.

The current server is loopback-only. A shared hosted deployment with an HTTPS
callback, trusted proxy configuration and secure cookies is separate deployment
work; do not expose this loopback configuration as a public team server.

Implementation follows [GitHub's OAuth authorization flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps).
Tests mock only the two fixed GitHub HTTPS endpoints; a real OAuth application is
still required for live acceptance.

## Connected repository and remaining setup

- Repository: [Austin610/AEGIS](https://github.com/Austin610/AEGIS).
- Default and publication branch: `main`.
- Layout: AEGIS occupies the repository root; no working-directory variable is needed.
- Authenticated push access and GitHub Actions execution have been confirmed.

No further repository details are required for CI. Optional branch protection still
requires choosing required status checks and approving the repository policy.
Live sign-in requires the OAuth client ID, locally configured client secret,
matching callback URL, and numeric account-to-role/workspace mappings described above.
Keep the client secret out of chat and source control.

`.github/workflows/ci.yml` is repository-independent and runs on pushes, pull
requests and manual dispatch. It includes Windows/Linux Python and browser checks,
PostgreSQL checks, and container verification. The default assumes AEGIS is the
repository root. For a monorepo, set the repository variable `AEGIS_WORKING_DIRECTORY`
to the relative project directory and place the workflow under the repository's
root `.github/workflows` directory. No deployment secrets or OAuth secrets are
needed for the test workflow; GitHub sign-in tests use disposable mock values.

The workflow requests only `contents: read`, per [GitHub's permission guidance](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token).
Enabling CI means publishing the prepared workflow to the selected repository and
observing a successful remote run. Local success is not remote CI verification.
