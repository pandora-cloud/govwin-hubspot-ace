# Maintainers

This project is maintained by [Pandora Cloud](https://pandoracloud.net).

## Current maintainers

| Name | GitHub | GitLab | Areas | Time zone |
|---|---|---|---|---|
| Isi Lawson | @isi-pandora | @isi-pandora | All; primary maintainer | US Eastern |
| Kim | _pending GitHub account_ | @kim-pandora | Maintainer | US Eastern |

## Response expectations

- **Bugs and PRs**: 5 business days for first response.
- **Security disclosures** (`SECURITY.md`): 7 calendar days for first response, 30 days for fix or mitigation.
- **GitHub Discussions**: best-effort, no SLA.

If a thread goes more than two weeks without a maintainer reply, ping it once and move on. We'll get back to it.

## Release ownership

Releases are cut by a current maintainer via a scripted local flow. See ADR [0011](docs/decisions/0011-manual-release-flow.md) for the rationale; the day-to-day procedure:

1. Confirm the `[Unreleased]` section of `CHANGELOG.md` lists every notable change since the last tag. Edit if it does not.
2. Pick the next version per semver. `feat` commits since the last tag mean a minor bump; `fix` commits alone mean a patch.
3. Run `make release VERSION=X.Y.Z`. The script bumps `pyproject.toml`, promotes the changelog heading, refreshes the compare-URL footnotes, commits, and tags. It does not push.
4. Review locally:
   ```
   git log --stat -1
   git show vX.Y.Z
   ```
5. Push: `git push origin main --follow-tags`.
6. The tag flows through the GitLab to GitHub mirror within a few minutes. On the GitHub side, the `sbom.yml` and `slsa.yml` workflows attach release artifacts to the new tag.

If you need to back out a release that has not been pushed yet:
```
git tag -d vX.Y.Z
git reset --hard HEAD~1
```
After push, back out is not safe; cut the next release with the fix instead.

## Becoming a maintainer

We're a small project and don't have a formal escalation path. The route is:

1. Open meaningful PRs - bug fixes, well-tested features, documentation that holds up to scrutiny.
2. Help triage issues and answer Discussions questions.
3. After demonstrated sustained contribution (several months, multiple merged PRs), an existing maintainer can propose adding you to `MAINTAINERS.md` and `CODEOWNERS`. Decision requires unanimous consent of current maintainers.

We will not add anyone whose primary affiliation is with a competing CRM-integration product or a paid-CRM-connector vendor.

## Reviewing external pull requests

External contributors fork the GitHub repo and open pull requests against `pandora-cloud/govwin-hubspot-ace:main`. Because the canonical source is GitLab (push-mirrored to GitHub one-way), a maintainer cannot merge on GitHub directly: the next mirror push would clobber the merge commit. Instead, the maintainer replays the PR's commits onto GitLab `main` and lets the mirror reflect them back.

The mechanical part is automated by `scripts/merge_github_pr.sh` (run via `make merge-pr PR=<number>`):

1. Review the PR on GitHub as usual. Once you're ready to merge:
2. Locally, from a clean working tree on `main`:
   ```
   make merge-pr PR=42
   ```
   The script fetches the PR via `gh pr checkout`, rebases its commits onto the latest GitLab `main`, and fast-forwards local `main`. It does not push.
3. Run the test suite if the PR touched code: `make test`.
4. Push when satisfied: `git push origin main`.
5. The mirror pushes the new commits to GitHub within a few minutes. The PR on GitHub auto-closes as "merged" once the commits land.

If the PR's commits do not rebase cleanly, the script aborts mid-rebase and leaves you on the PR branch. Resolve conflicts, finish the rebase, then re-run the merge step manually.

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
