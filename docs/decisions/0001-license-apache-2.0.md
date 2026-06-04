# 1. License: Apache License 2.0

Date: 2026-06-03

## Status

Accepted

## Context

The govwin-hubspot-ace repository is being released publicly on GitHub
and will also be listed on the Amazon Web Services (AWS) Marketplace as a
deployable solution. Two distribution channels share the same codebase:

1. A free, public, source-available open-source repository for partners and
   contributors who want to self-host.
2. A paid AWS Marketplace listing for partners who prefer a packaged,
   supported deployment.

Pandora Cloud LLC does not treat this product as a primary revenue line.
Strategic value is in AWS Partner Network (APN) credibility, community goodwill,
and reinforcing Pandora Cloud's positioning as an AWS Advanced Tier Services
Partner. The question was which license best supports that posture, balancing:

- Preventing a competitor from repackaging the software and reselling it.
- Acceptance by enterprise and government legal teams.
- Compatibility with an AWS Marketplace listing.
- Long-term goodwill in the open-source and APN communities.

Two finalists were considered:

- **Apache License 2.0.** A genuine Open Source Initiative (OSI) approved
  license. Permits any use including resale. Includes an explicit patent grant.
- **Business Source License (BSL) 1.1.** A source-available (not OSI
  open-source) license. Permits everything except a defined competing use,
  with automatic conversion to a true open-source license after a defined
  Change Date.

## Decision

Adopt the **Apache License 2.0** for the public repository.

The AWS Marketplace listing will operate under its own End User License
Agreement (EULA), either the AWS Standard Contract for Marketplace or a
custom EULA, independent of the repository license.

## Consequences

Positive:

- Genuine "open source" status. Strengthens APN credibility and lowers
  adoption friction for enterprise and government legal review.
- Explicit patent grant. Defensive protection for Pandora Cloud and downstream
  users.
- Maximum surface area for community contribution and visibility.
- Familiar to every legal team; near-zero review friction.

Negative:

- A competitor may legally repackage and resell the software. This risk was
  judged theoretical for a niche GovWin to HubSpot to AWS Partner Central
  integration. Differentiation comes from brand, support, deployment
  expertise, and roadmap velocity rather than from source exclusivity.

Operational:

- The repository's LICENSE file and source headers remain Apache License 2.0.
- Public-facing material (README, APN listing copy, Marketplace listing copy)
  may describe the project as "open source" without qualification.
- If a real competitor-reseller scenario materializes, or if the economics of
  the product change such that resale exclusivity becomes load-bearing,
  this decision should be revisited via a superseding Architecture Decision
  Record (ADR).
