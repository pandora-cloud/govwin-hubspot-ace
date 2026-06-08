# AWS Partner Network prerequisites

The integration submits opportunities into AWS Partner Central's APN Customer Engagements (ACE) co-sell program. Before you deploy, your organization needs to be a registered AWS Partner with an Approved Solution in Partner Central. This page covers what that means, how to confirm you have it, and how to get there if you do not.

## Who this is for

You need this if your organization plans to submit AWS-side co-sell opportunities through the integration in either the `Sandbox` or production (`AWS`) ACE catalog. The integration runs without any of this if you only intend to sync GovWin opportunities into HubSpot; the ACE half remains dormant until a deal moves into a configured trigger stage.

## What you need

- **AWS Partner Central account.** Distinct from your AWS commercial account. Created at https://partnercentral.awspartner.com by an authorized representative of your organization. Free.
- **AWS Partner Network membership.** Joining APN is also free. Membership tier (Registered, Select, Advanced, Premier) is assigned by AWS based on revenue, certifications, and partner activity. Submitting to ACE requires at least **Select** tier (achieved by registering at least 10 AWS-validated opportunities).
- **At least one Approved Solution registered in Partner Central.** A "Solution" in Partner Central terms is a packaged offering (your software, your services, or a combination) that AWS has validated. Approval is a manual review by an AWS Partner Development Manager (PDM) or Partner Solutions Architect (PSA). Each `CreateOpportunity` call must associate the opportunity with at least one Solution.
- **A signed ACE program agreement.** Once you have an Approved Solution, AWS gates ACE participation behind a separate program agreement. Your PDM walks you through this.

## How to verify before deploying

The fastest sanity check: confirm an Approved Solution exists in your account.

```bash
aws partnercentral-selling list-solutions \
  --catalog AWS \
  --region us-east-1 \
  --query 'SolutionSummaries[?Status==`Active`].{Id:Id,Name:Name,Category:Category}'
```

What the output means:

- **Empty array `[]`** with no error: your account can call the Selling API but has no Approved Solution registered. Register one through Partner Central before deploying. The integration will not function in the production catalog without this. (In `Sandbox` the integration falls back to a `_NONE_REGISTERED_` placeholder so you can still smoke-test the wiring.)
- **One or more `{Id, Name, Category}` entries**: pick the Solution ID you want as the default and set `ace_default_solution_id` in `terraform.tfvars` to that value. BD users can override per-deal on the Submit card.
- **`AccessDeniedException`**: your AWS account does not have the IAM permissions to call `partnercentral:ListSolutions`. Confirm you are authenticated as a principal that has the `AWSPartnerCentralFullAccess` policy (or an equivalent custom policy), and that the Partner Central account is linked to this AWS account through the Partner Central UI's account-linking flow.
- **`UnauthorizedOperation`** with a message about catalog access: your Partner Central account is set up but has no ACE program agreement on file. Contact your PDM to get the ACE terms signed.

## How to get there if you do not have it

1. **Register at Partner Central.** Visit https://partnercentral.awspartner.com and create an account on behalf of your organization. AWS will ask for your business identifiers (DUNS, CAGE if applicable, EIN), primary contact, and AWS account ID for linking.
2. **Complete the APN Self-Service Form.** Under "Account Settings -> Company Profile", fill in your service offerings, vertical focus, and certifications. This is what AWS uses to assign your initial APN tier.
3. **Register Solutions.** Under "Sell -> My Solutions", click "Create" and submit each solution you offer. AWS reviews each Solution; typical turnaround is 7-14 business days. Approved Solutions show `Status = Active`.
4. **Sign the ACE program agreement.** Once you have at least one Active Solution, your PDM (assigned automatically) reaches out about ACE eligibility. You sign the ACE Terms and Conditions and AWS enables `partnercentral-selling:*` API access for your account.
5. **Link your AWS account.** Under "Account Settings -> Linked AWS Accounts", link the AWS account where you will deploy this integration. The Selling API only works from linked AWS accounts.

The full setup takes anywhere from a couple of weeks (if you have an existing Partner Central account and just need to register a Solution) to two or three months (if you are a brand-new partner). Start this process before you start the Terraform deployment; the integration is fast to deploy but useless if your ACE account is not ready when a buyer purchases.

## Where to confirm membership tier

Tier status is visible in the Partner Central console under "Account Settings -> Tier and Benefits". Submitting to ACE requires Select. If you are at Registered and want to move to Select, the fastest path is logging the first 10 AWS-validated opportunities (which the integration will start doing automatically once deployed).

## Related reading

- [AWS Partner Central Selling API overview](https://docs.aws.amazon.com/partner-central/)
- [APN tier benefits and requirements](https://aws.amazon.com/partners/programs/)
- [Pre-install checklist](pre-install-checklist.md) for the operator-facing deployment decisions
- [ACE Integration Guide](ace-integration.md) for the submission workflow once everything is in place
