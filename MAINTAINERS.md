# Maintainers

This project is maintained by [Pandora Cloud](https://pandoracloud.net).

## Current maintainers

| Name | GitHub | GitLab | Areas | Time zone |
|---|---|---|---|---|
| Isi Lawson | @isi-pandora | @isi-pandora | All; CTO and primary maintainer | US Eastern |
| Kim | @kim-pandora | @kim-pandora | Maintainer | US Eastern |

## Response expectations

- **Bugs and PRs**: 5 business days for first response.
- **Security disclosures** (`SECURITY.md`): 7 calendar days for first response, 30 days for fix or mitigation.
- **GitHub Discussions**: best-effort, no SLA.

If a thread goes more than two weeks without a maintainer reply, ping it once and move on. We'll get back to it.

## Release ownership

Releases are cut by a current maintainer. The process is currently manual:

1. Aggregate conventional commits since the last tag into `CHANGELOG.md` under a new version heading.
2. Tag the commit with `vX.Y.Z` and push the tag.
3. The SBOM and SLSA provenance workflows attach release artifacts automatically when a tag is published.

A future move to an automated release flow (semantic-release on GitLab CI, or a successor to release-please that runs on the GitLab side) is tracked in the ADR backlog.

## Becoming a maintainer

We're a small project and don't have a formal escalation path. The route is:

1. Open meaningful PRs - bug fixes, well-tested features, documentation that holds up to scrutiny.
2. Help triage issues and answer Discussions questions.
3. After demonstrated sustained contribution (several months, multiple merged PRs), an existing maintainer can propose adding you to `MAINTAINERS.md` and `CODEOWNERS`. Decision requires unanimous consent of current maintainers.

We will not add anyone whose primary affiliation is with a competing CRM-integration product or a paid-CRM-connector vendor.

## Token rotation schedule

The integration itself depends on several long-lived credentials. Maintainers track expiry here so rotation does not catch us by surprise. GitLab and GitHub both email warnings 30 days before expiry, but the table is the durable record.

| Token | Where it lives | Used by | Expires | Renewal |
|---|---|---|---|---|
| GitLab project access token `renovate-bot` | GitLab project access tokens (`gitlab.com/pandora-cloud/oss/govwin-hubspot-ace/-/settings/access_tokens`); stored as masked CI variable `RENOVATE_TOKEN` | Renovate scheduled pipeline | 2027-06-04 | Create a new project access token (same scopes: `api`, `read_repository`, `write_repository`; Developer role), update the CI variable, revoke the old token. |
| GitHub `GITHUB_COM_TOKEN` CI variable | GitLab CI variable, masked | Renovate (release-notes fetching from GitHub-hosted upstreams) | rotates with the human's GitHub OAuth | Run `gh auth refresh -h github.com -s repo,workflow,gist,read:org` to rotate the underlying token; copy the new value into the CI variable. |
| GitHub repository access for the GitLab → GitHub push mirror | GitLab project mirror config (`Settings → Repository → Mirroring`) | The push mirror | rotates with the human's GitHub OAuth | Same as above; update the mirror config's password field. |
| GitLab user PATs (maintainer-personal) | `gitlab.com/-/user_settings/personal_access_tokens` | Individual maintainer day-to-day | per maintainer | Each maintainer maintains their own. |

When GitLab emails an expiry warning, treat it as a P2 work item, not a "later." Renovate goes silent without warning if its token lapses, and the next vulnerability advisory will sit in a dashboard nobody is watching.

## Commercial support

For commercial support, see [SUPPORT.md](SUPPORT.md).
