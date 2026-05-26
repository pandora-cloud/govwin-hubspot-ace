# Submit-to-AWS UI Extension: deployment and verification

End-to-end procedure for shipping the Submit-to-AWS HubSpot CRM card
plus its backend Lambda + API Gateway routes. Roughly 20 minutes of
hands-on work the first time; subsequent deploys are mostly `terraform apply`
+ `hs project upload`.

## Prerequisites

- AWS profile `pcmgmt` configured locally and authenticated.
- HubSpot CLI (`hs`) authenticated to the Pandora Cloud production portal
  (244239851). Verify with `hs accounts list`.
- Local checkout of `govwin-hubspot-integration` on the branch carrying
  the UI Extension commits.
- AWS Partner Central access in the same browser session you use for
  HubSpot (re-installing the app needs both).

## Step 1: Deploy the new Lambda + routes (Terraform)

The submit-form Lambda, three API Gateway routes, and CloudWatch log
group all live in `terraform/modules/ace/submit_form_lambda.tf`.

```
cd terraform
terraform plan
terraform apply
```

Expected new resources:

- `aws_lambda_function.submit_form_to_ace`
- `aws_apigatewayv2_integration.submit_form`
- `aws_apigatewayv2_route.submit_form_submit`
- `aws_apigatewayv2_route.submit_form_solutions`
- `aws_apigatewayv2_route.submit_form_products`
- `aws_lambda_permission.submit_form_api_invoke`
- `aws_cloudwatch_log_group.submit_form_to_ace`

Apply also updates the existing webhook receiver and submit_to_ace
Lambda code (today's mapper / aws_clients fixes ship in the source
archive). The legacy webhook flow is unaffected; routes are additive.

After apply, capture the new endpoint URLs:

```
terraform output ui_extension_base_url
terraform output ui_extension_submit_url
terraform output ui_extension_solutions_url
terraform output ui_extension_aws_products_url
```

The base URL should match what `hubspot-app/src/app/app-hsmeta.json`
already lists under `permittedUrls.fetch`
(`https://np1hq84j21.execute-api.us-east-1.amazonaws.com`). If the
API id ever changes, update both `app-hsmeta.json` and the constant
in `SubmitToAwsCard.tsx`.

## Step 2: Seed HubSpot property option sets

The `govwin_ace_solution_id` and `govwin_ace_aws_products` dropdowns
have option sets sourced from outside this repo (ACE ListSolutions
and `resources/aws_products.json`). Run `setup_hubspot` once to push
them to HubSpot:

```
aws --profile pcmgmt --region us-east-1 lambda invoke \
  --function-name govwin-hubspot-prod-setup-hubspot \
  --payload '{}' /tmp/setup.json && cat /tmp/setup.json
```

Expected log line in CloudWatch:

```
seeded 513 AWS Products options
seeded 4 Solution options
HubSpot setup complete: ...
```

Idempotent. Safe to re-run after refreshing `aws_products.json` or
after registering a new solution in Partner Central.

## Step 3: Upload the UI Extension

The Submit-to-AWS card and supporting components live in
`hubspot-app/src/app/cards/submit-to-aws-card/`. Push:

```
cd hubspot-app
hs project upload
```

Expected output: `Deployed build #N`. `auto-deploy` is on, so the new
build becomes the active version immediately. No additional install
step is required; the build inherits the existing app's portal install
from the May 26 re-authorization.

If you added new required scopes to `app-hsmeta.json`, you must re-install
to grant them: open the project in HubSpot
(https://app.hubspot.com/developer-projects/244239851/project/govwin-hubspot-ace-webhooks),
navigate to the app -> Distribution tab, and click Install now. The
new access token displays after the install. Update Secrets Manager:

```
# Replace value of private_app_token in govwin-hubspot-prod/hubspot
# via the AWS Console (do not echo the token on a CLI you keep history of).
```

## Step 4: Verify the card renders

1. Open any deal on the Government pipeline in HubSpot.
2. Switch to the deal's overview tab. The Submit-to-AWS card should
   appear in the sidebar with a status badge.
3. For a deal that has not been submitted yet, the badge reads
   "Not submitted" and a "Submit to AWS" button is visible.
4. For a deal that already submitted (e.g. USSF SLD45, deal 326365244126),
   the badge reads "Submitted to AWS" and the AWS opportunity id
   (O13740398) appears with a deep link to Partner Central.

## Step 5: End-to-end test on a fresh deal

The cleanest E2E test path:

1. Create a throwaway deal on the Government pipeline named, e.g.,
   `E2E test - DELETE ME`. Associate any company + at least one contact.
2. Open the card and click Submit to AWS.
3. The form opens with the GovWin/synthetic toggle defaulted to "No"
   because the deal has no `govwin_opp_id` yet.
4. Pick a source (DIRECT), the customer slug should auto-fill from the
   associated company name, leave the sequence on whatever was suggested.
5. Pick Partner Need (Deal Support is fine for a no-actual-AWS-help case),
   Delivery Model (Professional Services), Use Case (any).
6. Enter a 12-digit AWS Account ID (or toggle "Not provided yet").
7. Set a description of at least 20 characters.
8. Click Submit to AWS. The toast should read "Submission queued."
9. The card transitions to "Queued" and starts polling every 30 seconds.
10. Within about 90 seconds the card should show "Submitted to AWS" with
    the new AWS opportunity id. Verify in the AWS console.
11. Delete the throwaway deal in HubSpot once verified.

## Step 6: Verify dedup

1. Reopen the same throwaway deal. The card should show "Submitted to AWS"
   with the opportunity id from step 5.
2. (Optional) Force a stage-flip retrigger by editing dealstage off and
   back to Submit to AWS. The submit_to_ace Lambda will reuse the existing
   ClientToken and AWS will return the same opportunity id; no duplicate
   ACE opportunity is created.

If you instead trigger from the form on a deal that already submitted,
the form returns HTTP 409 with the existing opportunity id surfaced
inline.

## Manual followups

Two items still need manual UI work that the codebase cannot automate:

### Granular permission migration (deadline 2026-06-25)

HubSpot is auto-migrating app id 38079082 to granular permissions on
2026-06-25T17:06:35Z. Visit
https://app-na2.hubspot.com/developer-apps/38079082/auth before that
date, review the proposed granular scope mapping, accept or adjust, and
run `hs project upload` to lock the new scope set. Then re-install the
app in the portal to grant the granular scopes. Update memory
`project_hubspot_granular_permissions.md` after completion.

### Legacy private app retirement

Once we've verified the new Developer Platform app's static access token
covers every backend code path (verified by SLD45 on 2026-05-26), the
legacy "GovWin Integration" private app can be retired. Steps:

1. In HubSpot, go to Settings -> Integrations -> Connected apps.
2. Click "GovWin Integration".
3. Confirm there are no other systems holding a token from this app.
4. Delete or disconnect the app.
5. Remove the old token from `.env` and rotate the dev-platform app's
   token in Secrets Manager just to be safe.

## Troubleshooting

If a submission gets stuck in "Queued":

- Check `submit_to_ace` CloudWatch logs for the most recent invocation.
  Look for ValidationException, AccessDeniedException, or ConflictException
  on the AWS API calls.
- Check the SNS topic
  `arn:aws:sns:us-east-1:555049241846:govwin-hubspot-prod-notifications`
  for mapping-error alerts.
- Re-trigger by flipping the dealstage off and back to Submit to AWS.

If the card shows "Status unknown":

- The handle_ace_event Lambda has not yet processed an inbound
  EventBridge state change. Check its CloudWatch log group for errors.
- Verify EventBridge is routing the `aws.partnercentral-selling` event
  bus to the handle_ace_event Lambda (Terraform owns this; should not
  drift).

If the form errors on submit with "Field not in enum":

- A boto3 enum has likely drifted from our codebase. Run
  `pytest tests/unit/test_ace_enums.py` locally; failures show which
  ALLOWED_* constant needs updating in `src/ace/mapper.py`. Then
  refresh the matching client-side list in
  `hubspot-app/src/app/cards/submit-to-aws-card/enums.ts` and re-upload.
