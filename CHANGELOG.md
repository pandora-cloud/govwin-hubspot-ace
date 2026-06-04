# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.0](https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v2.1.0...v2.2.0) (2026-06-04)


### Features

* **ace lambdas:** consolidate writeback paths and tighten event handling ([44cc4c6](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/44cc4c6c16e55bfa822d8c16a1a0307dc1fa6365))
* **ace:** add list_active_solutions convenience wrapper ([04de05d](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/04de05d84dff5ce71f9a175356b55571f9b7b091))
* **ace:** codify CreateOpportunity closed enums and add aws account id validator ([612597f](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/612597fff5b61880ba4d2a77cd1f5dc52df56c98))
* **ace:** expand mapper and client for UpdateOpportunity round-trip ([5f6300d](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/5f6300d492072051f37239989cd2abc84a66cd45))
* **ace:** explicit HubSpot lifecyclestage -&gt; ACE LifeCycle.Stage table ([dbd9eec](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/dbd9eecd7ce1f45b7b4b6d9bd2cd89236f6de9e5)), closes [#7](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/7)
* **ace:** parameterize ExpectedCustomerSpend.TargetCompany ([05155c9](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/05155c9e95a179a8a514d9ea3b1a437ab329421f))
* **ace:** pull aws_products.json from AWS canonical source + enforce association quotas ([a4b71ab](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a4b71ab899c7322208b3f1eb8ef88f77ae1c933b))
* **ace:** reconcile HubSpot edits deferred during AWS review ([4bf6f8e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/4bf6f8e5c12f42544ca61482f990024400a7dc84))
* **ace:** ship initial aws_products.json catalog and refresh script ([416b03d](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/416b03d2c2a9fbd278c55260ec58280e56ebd323))
* **ace:** submit_form_to_ace Lambda for UI Extension callback endpoint ([941580a](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/941580a00cc06aeccd0b6297c327056c5ed222bf))
* **ace:** Terraform routes and outputs for submit_form_to_ace ([e59c9c2](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/e59c9c2a9a3fe8b0a891b5cf59bf1ac88134ef0c))
* **alerts:** shared SNS publish helper with body redaction ([8208f50](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/8208f50537d2144342b2be985bd26d48a93803bb))
* **hubspot-app:** activate webhook subscriptions and expand scopes ([e4d862e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/e4d862ec5812856caa2a8f60d97951c11f5e2417))
* **hubspot-app:** products syncing pill, 409 status branching, webhooks ([93de698](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/93de698cfcaf73210fbc4ebecbeee085facd5207))
* **hubspot-app:** scaffold Submit-to-AWS CRM card ([fee3c60](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/fee3c600a08c93c06a367e6916b4f491d35acf55))
* **hubspot-app:** SubmitForm modal + status polling ([a99a18c](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a99a18cc74540b894606c33a6b53e9b07b24a828))
* **hubspot-app:** SyntheticIdHelper + SolutionPicker + AwsProductsPicker ([94c5a63](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/94c5a63cf18e09c1e4f684ae2bc2e5836d5eb5dc))
* **hubspot:** declare BD-editable update properties and granular options ([14acd6b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/14acd6be9686568fff9f4e8c862a0e8e59e88be6))
* **hubspot:** migrate ACE properties to closed-enum dropdowns + dynamic seeding ([7890573](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/7890573c6a1757952c8af140a91ff16411f8ed39))
* **observability:** alarm + runbook for update_in_ace fan-out ([bf1f033](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/bf1f0339f55f8328ec2b00f4b62656aeaecd349d))
* **security:** enforce FIPS endpoints and pin partnercentral-selling to us-east-1 ([010154b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/010154bcc53e01555af383265ab0d61b2639faa5))
* **security:** hoist pipeline KMS into its own module + flip SNS/DDB onto CMK ([6f13c86](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/6f13c866a90c8b73d2dd11119e7f9b512a4a989c))
* **security:** SNS alert on hand-edits of govwin_aws_cosell_id ([5e9053c](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/5e9053cc51c53089189fee11be5d98698867bb04))
* **security:** split submit_form_to_ace into reads + writes Lambdas ([aa0379f](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/aa0379fb275cf75fc1a7eb2a174a7d7708e732f3))
* **setup_hubspot_webhooks:** add dryRun mode for config-change validation ([831d5a3](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/831d5a3f869374f0880e72f3bbe1c1a759a5f48c)), closes [#2](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/2)
* **ui-extension:** /update endpoint, replay-409 status, CORS allowlist ([f8fe3fc](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/f8fe3fc309562dada0e79ad70e5aea9a8d8da6a0))
* **update_in_ace:** Closed-Lost race, self-heal verify, dispatch refactor ([e683039](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/e6830396c96b8d74f8cc0fb082e3bbf7fb330a53))


### Bug Fixes

* **ace:** align CreateOpportunity payload with boto3 contract ([d7f613b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/d7f613b953f6538af256d9bb2f0adb8e84e51b3e))
* **ace:** always emit Marketing.Source on UpdateOpportunity payloads ([8f51f42](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/8f51f42ef46b482c82431165cd63ac6832d92695)), closes [#19](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/19)
* **ace:** include raw query string in signature URL reconstruction ([a095144](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a095144b72455f0fb784fe95de9db822212675f2))
* **ace:** make get_owner failure non-fatal in submit_to_ace ([cd27dfd](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/cd27dfd010ae260a3ff3d44ce67c7720945e7c77))
* API Gateway timeout matches Lambda + govwin_worker SNS pre-warm ([5c6dfe0](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/5c6dfe0b97974854a12e8f67d62770d83e29bd55))
* **bootstrap:** grant deployer kms:CreateGrant for DDB CMK SSE updates ([453ff03](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/453ff033eda781d444b19ae72964a1f8f3ebf6c7))
* **ci:** terraform fmt alignment + FIPS verify honors no-FIPS exceptions ([d1ae49f](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/d1ae49f8ef06539d59901f25f160286178f1680a))
* **config:** only table names need to be required, not secret names ([bb43192](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/bb43192aff86c34705da9bdb27059dfe621305e5))
* **config:** only the webhook path needs HUBSPOT_WEBHOOK_SECRET_NAME ([def437e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/def437ee34eb67cb5ce251ebd3bacd60d32de0c2))
* **config:** require table and secret env vars at load time ([a2dfe03](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a2dfe03095437f693ff7b8953bda2eeec8c49c82))
* **deps:** bump idna and urllib3 for CVE-2026-45409 + PYSEC-2026-141/142 ([973cd12](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/973cd12d9131c8b0eba61cc46b7f157f77380ddf))
* **handle-ace-event:** let terminal LifeCycle.Stage win over ReviewStatus ([0614890](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/0614890495523ee1530409674d09ef89f99412a8))
* harden the patterns surfaced by the E2E audit ([ae52eb5](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/ae52eb5cca24028a978123ba7b5684099e3b59b0))
* **hubspot-app:** break infinite-render loop in SyntheticIdHelper ([7b5b197](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/7b5b197e74e6157a9e7db417a1a34674315f8179))
* **hubspot-app:** closedate epoch parsing + bisect-rebuild full form ([c23a221](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/c23a221380884e1337d8c689794e53251eabcfa2))
* **hubspot-app:** conform form components to @hubspot/ui-extensions 0.14.0 prop contracts ([9c66f1b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/9c66f1be18bbd333d9e2fdf74f59426d8bc004dc))
* **hubspot-app:** drop raw HTML tags inside Text components ([23bb24b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/23bb24b47eaa4123d652ba8f15d2e52438b157b2))
* **hubspot-app:** drop redundant filter Input; use MultiSelect built-in typeahead ([a79ebf2](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a79ebf2198783e25655224c3e13ebbd5708e377a))
* **hubspot-app:** drop yes/no toggle; show both GovWin ID inputs side by side ([e44feaf](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/e44feaf9885ee92e559415504d7779874db19dda))
* **hubspot-app:** flatten cards directory + drop ModalDialog + handle empty solutions ([21ed343](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/21ed3432c64dfe82199ae502fde3705562375c07))
* **hubspot-app:** Link.external is part of href object, not a top-level prop ([bfad82b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/bfad82b7320a15727945c232c1bc1893ce7ae3c6))
* **hubspot-app:** restore SyntheticIdHelper + GovWin toggle in Section 1 ([ad35475](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/ad35475e192afb40b08d9a8c6de1009871b77b19))
* **hubspot-app:** restore SyntheticIdHelper for non-GovWin deals in create mode ([769c19e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/769c19e0dc1daf5bcebbc133955bf9cafea6617d))
* **hubspot-app:** strip SyntheticIdHelper of all useEffects and async work ([d56939a](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/d56939aa4659befb714677d2d916ba77cfc1c2e4))
* **hubspot-app:** swap inline ToggleGroup for Select on GovWin source ([46d0b6d](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/46d0b6d528780d0ff8617eba8b7e0dc00d504a06))
* **hubspot-app:** swap Toggle for Checkbox in GovWin source picker ([0631b29](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/0631b29296f38a5c965040864f258f35b6adc318))
* **hubspot-app:** use fetchCrmObjectProperties from CrmHostActions ([a8ff53a](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a8ff53ad925bbdc0e4f8313ceed4ce2dd942df32))
* **hubspot-app:** use hubspot.fetch instead of hubspot.serverless ([2f02c57](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/2f02c570b9256e20c2562283d9abd9b1a661463c))
* **hubspot-app:** use Toggle for GovWin/synthetic source picker ([07ddb8b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/07ddb8b1c6874730b8361dc996c297c6a0cf42fb))
* **iam:** grant webhook_receiver role sns:Publish + KMS for the audit alert ([e742889](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/e7428891c79223b08ec70c6e8695c045dab9b454))
* **monitoring:** require notification_email when alerts enabled ([bfa171e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/bfa171e253b64f519161a370349ea541b5b72405))
* **receiver:** break the integration write -&gt; webhook -&gt; rewrite feedback loop ([5b47a4e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/5b47a4e2cc109d8271025cce9ead984b5a995040))
* **receiver:** timeout 5s -&gt; 10s + pre-warm SNS for the audit-event path ([bc82527](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/bc82527889a91188c373d3ba61d08d65d4e48082))
* **reconcile:** list_opportunities pagination + Makefile threads secret names ([ca5fdf7](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/ca5fdf770090a0f0cfe9ef89ea2c55f677311d2a))
* **security:** refuse cross-deal self-heal rebind + SNS leaf comment ([b0ff01c](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/b0ff01ce671ae93a09e01e07e9d5bcc2e35ce509))
* **security:** tighten redactor + replay TTL + DDB IAM + catalog echo + sourceId ([8778165](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/8778165deb343b035b11afe112726c788789a1cc))
* **setup:** wire ACE catalog into setup_hubspot and fail loud on empty prod solutions ([05c6453](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/05c64536506a0d669c9375d8dd8d9e61e1e19c2f))
* **state,ui-ext:** refresh reverse-index TTLs and replace assert with guard ([32113fe](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/32113febe391c0725999b38b53db0a6cc6650523))
* **ui-extension:** clear-on-update + truthful product message + fail-loud trigger config ([112248b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/112248bd881ab2272f169b3a242e9eab794954f7))


### Documentation

* Docs:  ([dcb300a](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/dcb300a0ba2a3f9ced56280bf6db055fb7927080))
* address audit blockers + merge testing docs + write operations runbook ([cc14bbe](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/cc14bbe86bc934b5b5ac31337f6a05998d8af6f3))
* **adr:** record license decision (Apache 2.0) ([be04c78](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/be04c783892ad975d333fc93b726f4e7a7797ca8))
* AI tooling disclosure (AI_USE.md) ([b437b9c](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/b437b9c6f875c017196555a929936891d115b57a))
* **ai_use:** correct cross-link to discussion issue [#22](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/22) ([120357b](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/120357b4be1b082baee3e05d3140d004ccb63de6))
* **contributing:** explain GitLab-as-source, GitHub-as-mirror to external contributors ([8edd591](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/8edd591197d9ea07fd8d2fb8888dd08cd1ebdbce))
* extended HubSpot-to-ACE field mapping matrix ([33e6b49](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/33e6b491ba223ab483db8cdd69345109e512f256))
* field-mapping reference scaffold (GovWin -&gt; HubSpot -&gt; ACE) ([9d8b4d5](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/9d8b4d5b157d02667b6068e9cf0f3fb3ec080783))
* **ops:** UI Extension deployment + verification runbook ([70f755f](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/70f755fb4349617b19b67bcbd409234982f7b348))
* OSS / curated-install readiness pass ([2b52c4e](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/2b52c4efd74acdb8fc5941d7ac29f06730af0f61))
* sphinx docstrings on ACE/HubSpot clients + lock-in WHY-comments ([a176338](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/a176338ac2e527521b21609995595cef6080f34c))
* trim engineering-journal language and migration runbooks ([59c9c8f](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/59c9c8f9b35b7e3745613b0e4821a16f0d16b2b8))


### CI / Build

* add fips job + commit-signing setup script + readme anchor fix ([7639b01](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/7639b01359d81d9579b2749481dcdb255dbdfd64))
* drop duplicate --fail from trufflehog extra_args (action already passes it) ([7efad69](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/7efad692f06dc8688107e507c1ca31104fe8839e))
* **release-please:** repair workflow + add config + bump pyproject version to 2.1.0 ([e5a5614](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/e5a5614a2bfbebd64e37e18a43cd61d3aa262190))
* renovate auto-labels per ecosystem (terraform / python / actions / docker) ([b868f8c](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/b868f8c817b919fac924b0f421a093d83f9e977d)), closes [#8](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/8)
* replace gitleaks-action with trufflehog for secret scanning ([39dd677](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/39dd6775c39e817b6bac3e9a7646e77223331def))
* supply-chain workflows + pre-commit + renovate + dependabot ([ce21948](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/ce2194815e3c3b00ca50736d39264bb5b941dc06))


### Tests

* **ace:** doctest the _split_csv helper and wire doctest runner into pytest ([9257d8d](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/9257d8d9386c54321a28766dd25bf8bcd07805fc)), closes [#4](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/4)
* cover the new audit / catalog-echo / clear-on-update / DRY paths ([aaeee96](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/aaeee961eaf814bb37ba068030172b273fe8ea6b))
* hubspot signature fixture for offline development ([7c4cf55](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/7c4cf559dd214eace53958d91a28b967456e0cdc)), closes [#9](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/9)
* **mapping:** lock in NAICS 541330 -&gt; Professional Services routing ([6552d69](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/6552d693e5e433c271433f470fa8a56216310c70)), closes [#1](https://github.com/pandora-cloud/govwin-hubspot-ace/issues/1)
* scripts/fault_inject.py exercises DLQ + webhook + EventBridge + SNS paths ([04d69ac](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/04d69acc913b1c476be24efa27141bb0e8aed023))


### Refactor

* **code-quality:** DRY validator + named MRR constant + trim WHAT-comments ([7029979](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/7029979558c19d788cb461e4d3a49e904df6f6d8))
* drop legacy non-atomic mark_event_seen ([889acdd](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/889acddb432aa4c98968c5c689156a2235f6bf32))
* **hubspot:** extract X-HubSpot-Signature-v3 validator to shared module ([eeb4775](https://github.com/pandora-cloud/govwin-hubspot-ace/commit/eeb4775f21acb1858d8e8946a213b50f40f6cc77))

## [Unreleased]

### Added
- Apache License 2.0 (replacing MIT) with explicit patent grant.
- Centralized boto3 client construction (`src/aws_clients.py`) with FIPS endpoint enforcement and `us-east-1` pinning for the `partnercentral-selling` API.
- FIPS endpoint policy: `AWS_USE_FIPS_ENDPOINT=true` on every Lambda and `use_fips_endpoint = true` on the Terraform AWS provider. Local tests opt out via the same env var since moto and LocalStack do not implement FIPS-suffixed hostnames.
- Customer-managed KMS key (`module.kms`): SNS topic, both DynamoDB tables, and every SQS queue (operational and DLQs) encrypt at rest under one auditable key.
- UI Extension split into separate read and write Lambdas. The reads role is minimal (`ListSolutions` plus signing-secret read); the writes role excludes `CreateOpportunity` and `StartEngagementFromOpportunityTask` so those calls only run from the trusted shared role behind the SQS pipeline.
- Audit handling for `govwin_aws_cosell_id`: hand-edits from any source other than the integration token fire a real-time SNS alert.
- `update_in_ace` self-heal verify: refuses to write to AWS when the deal's recovered ACE id resolves to a foreign `PartnerOpportunityIdentifier`, and publishes a mismatch alert.
- `update_in_ace` Closed-Lost race fix: when a `LifeCycle.Stage = Closed Lost` change arrives without its companion `ClosedLostReason` (or vice versa), the Lambda reads the missing companion from the HubSpot deal so AWS gets both fields in one `UpdateOpportunity`.
- `update_in_ace` dispatch table for property delta handlers: `govwin_ace_lifecycle_stage`, `govwin_ace_closed_lost_reason`, `govwin_ace_solution_id`, `govwin_ace_partner_need`, `govwin_ace_delivery_model`, `govwin_ace_sales_activities`, `govwin_ace_national_security`, `govwin_ace_opportunity_type`, `govwin_industry`, and the AWS Products diff handler.
- `/update` UI Extension endpoint: synchronous `GetOpportunity` + `UpdateOpportunity` + Associate/Disassociate from the Submit-to-AWS card. Replay protection distinguishes `status=replay_detected` from `status=already_submitted`.
- CORS allowlist for the OPTIONS preflight reflection (`https://app.hubspot.com`, regional `app-naN`/`app-euN`/`app-jpN`/`app-apN`, sandbox variants). Unrecognized origins fall back to the NA1 default rather than echoing the request value.
- CloudWatch alarm `<prefix>-update-in-ace-high-rate` for sustained fan-out detection. Threshold via `var.update_in_ace_fanout_threshold` (default 30/min averaged over 15 min; set to 0 to disable). New "Scaling and webhook fan-out" runbook in `docs/operations.md`.
- `docs/pre-install-checklist.md` and `docs/cost-model.md`.
- `scripts/reconcile.py` and `make reconcile GOVWIN_ID=<id>`: read-only 4-way state dump (DDB, HubSpot deal, AWS `GetOpportunity`, AWS `ListOpportunities` collision check) for triaging self-heal mismatch alerts.
- `make dlq-status` and `make dlq-redrive QUEUE=<name>`: depth across every project DLQ plus an SQS message-move-task wrapper.
- `ACEClient.get_opportunity` asserts the response `Catalog` matches the configured catalog and raises `CrossCatalogResponse` on mismatch; defense in depth on top of the IAM `Catalog` condition.
- `HUBSPOT_INTEGRATION_APP_ID` env var (set from `var.hubspot_webhook_app_id`). The audit-alert handler cross-checks `ev.sourceId` against this value; INTEGRATION-source events from a different app installed on the same HubSpot portal fire a distinct "foreign HubSpot integration" alert.
- DynamoDB `LeadingKeys` IAM condition on the `ui_extension_reads` and `hubspot_webhook_receiver` `PutItem` grants, restricting both roles to the `WHK#` prefix.

### Changed
- HubSpot subscription registration is manifest-driven (`hubspot-app/src/app/webhooks/webhooks-hsmeta.json` deployed by `hs project upload`); the legacy `setup_hubspot_webhooks` Lambda is retired.
- `partnercentral-selling` boto3 client is hard-pinned to `us-east-1` regardless of the operator-configured `aws_region`. This reflects an AWS-side endpoint constraint; non-`us-east-1` deployments previously failed silently.
- HubSpot 4xx error bodies and SNS alert bodies pass through a redactor that strips `propertyValue` and `localizedErrorMessage` before logging or publishing, and fully redacts `CompanyName`, `Email`, `Phone`, `WebsiteUrl`, and `Reason`. `message` / `Message` / `ErrorMessage` are trimmed to 200 characters so operators retain diagnostic context.
- AWS error strings written back to HubSpot deal properties pass through the same redactor; HubSpot deal properties are visible to anyone with deal-read.
- Webhook signature replay reservation TTL halved from `2 * webhook_max_age_seconds` to match the freshness window; replays past the window already fail the timestamp check, so the doubled TTL provided no marginal protection.
- Webhook signature 4xx responses gate detailed mismatch context behind `LOG_LEVEL=DEBUG` so a CloudWatch ingest leak does not give an attacker a precise oracle.
- `_trigger_stage_id` in `ui_extension_writes.py` raises on missing `ACE_TRIGGER_STAGES` instead of falling back to a hardcoded sandbox stage id.
- LocalStack pinned to `localstack/localstack:3.8` (community edition).
- README configuration table: added `ace_default_solution_id`, `ace_trigger_stages`, `hubspot_webhook_app_id`, `hubspot_webhook_client_secret`. Default `sync_schedule` corrected to `rate(1 hour)`.
- `hubspot-app/src/app/webhooks/webhooks-hsmeta.json`: every subscription ships `"active": false` so the first `hs project upload` does not flood a placeholder URL.

### Fixed
- `submit_to_ace` consolidates the dual writeback path into a single outer handler that records the actual AWS error string (trimmed to 480 chars) on permanent failure.
- `handle_ace_event` writes `govwin_ace_lifecycle_stage` only when the value actually changes, preventing the AWS-event -> HubSpot-PATCH -> AWS-event feedback loop.
- `handle_ace_event` writes `govwin_aws_cosell_products` (AWS-side mirror) so the Submit-to-AWS card can render a "Products syncing..." pill without burning a Partner Central read quota call.
- `_handle_aws_products_diff` collects non-Conflict failures into a `failures` list and surfaces them so partial product-association failures are visible rather than silent.
- Multi-value `_apply_delta` handlers (`govwin_ace_partner_need`, `govwin_ace_delivery_model`, `govwin_ace_sales_activities`, `govwin_ace_national_security`, `govwin_ace_opportunity_type`) return `False` on empty, garbage, or invalid-enum values so the DDB mapping is not marked "updated" on a no-op.
- `govwin_industry` handler clears `OtherIndustry` when the new industry maps to a closed-enum value.

## [v2.1.0] - 2026-04-30

### Added
- X-Ray Active tracing on every Lambda for end-to-end observability.
- CloudWatch alarms on DLQ depth, orchestrator/worker error counts, ACE submission failure counts.
- IAM bootstrap module (`terraform/bootstrap/`) with MFA-gated deployer role and a separate one-time bootstrap-operator policy.
- Sandbox MFA escape hatch (`require_mfa_to_assume_deployer = false` + `acknowledge_no_mfa_for_sandbox_only = true`) with a mandatory expiry date.

### Changed
- **Architecture**: replaced the v2.0 Step Functions chain with EventBridge Scheduler + SQS fan-out + reserved-concurrency-governed Lambdas. Removes the 256KB inter-state payload limit and lets each opportunity batch retry independently.
- ACE submission path now atomically reserves ClientTokens in DynamoDB via conditional writes; concurrent SQS retries cannot mint duplicate ACE opportunities.

## [v2.0.0] - 2026-04-28

### Added
- HubSpot to AWS Partner Central submission half: `submit_to_ace`, `update_in_ace`, `handle_ace_event`, `hubspot_webhook_receiver`, `setup_hubspot_webhooks` Lambdas.
- AWS Partner Central Selling API direct integration (`src/ace/`) replacing the prior dependency on a paid third-party connector.
- HubSpot developer-platform 2025.2+ webhook app (`hubspot-app/`).
- Three-call submission flow: `CreateOpportunity` -> `AssociateOpportunity` -> `StartEngagementFromOpportunityTask`.
- Optimistic locking on `UpdateOpportunity` via `LastModifiedDate` with `ConflictException` retry.
- EventBridge subscription on `aws.partnercentral-selling` to mirror AWS-side state changes back to HubSpot.
- 11-scenario sandbox smoke matrix and `scripts/sandbox_smoke.py` automation for scenarios 1-10.
- HubSpot `govwin_ace_*` BD-editable property surface for the three ACE-required fields and supporting marketing/use-case context.

### Changed
- ACE catalog defaults to `Sandbox`. Production deployments must explicitly set `ace_catalog = "AWS"`. IAM policy adds a `partnercentral:Catalog: Sandbox` condition when in Sandbox mode.

## [v1.0.0] - 2026-04-08

### Added
- GovWin to HubSpot sync: hourly Step Function (in v1; superseded by v2.1's EventBridge Scheduler + Lambda + SQS).
- GovWin WSAPI V3 client with OAuth2, rate limiting (4,000/hr), and discovery modes (marked / saved-search / bookmarked / date-range).
- HubSpot CRM v3 API client with batch upsert, custom properties (`govwin_*`), pipeline mapping, and contact/company associations.
- DynamoDB-backed sync state (cursors, opportunity update dates, entity mappings).
- 130 unit tests including production-data quirk regression tests.
- Pre-deployment validation script (`scripts/validate.py`).
- Dry-run script (`scripts/dry_run.py`).
- LocalStack integration test suite.

[Unreleased]: https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v2.1.0...HEAD
[v2.1.0]: https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v2.0.0...v2.1.0
[v2.0.0]: https://github.com/pandora-cloud/govwin-hubspot-ace/compare/v1.0.0...v2.0.0
[v1.0.0]: https://github.com/pandora-cloud/govwin-hubspot-ace/releases/tag/v1.0.0
