# Runbook: deploy the UI Extension Lambda split

This is a one-time apply runbook for the `feat(security): split
submit_form_to_ace into ui_extension_reads + ui_extension_writes` change.

## What changed

* `src/lambdas/submit_form_to_ace.py` is gone. It is replaced by:
  * `src/lambdas/_ui_extension_common.py` -- shared signature
    validation, CORS preflight, path allowlisting, replay protection.
  * `src/lambdas/ui_extension_reads.py` -- GET /solutions, GET
    /aws-products. Tight IAM (signing secret + ListSolutions only).
  * `src/lambdas/ui_extension_writes.py` -- POST /submit, POST
    /update. Same IAM scope as the old monolith for the write paths.
* Terraform:
  * `submit_form_lambda.tf` + `submit_form_role.tf` deleted.
  * `ui_extension_lambdas.tf` + `ui_extension_reads_role.tf` +
    `ui_extension_writes_role.tf` added. Two new Lambdas
    (`<prefix>-ui-ext-reads`, `<prefix>-ui-ext-writes`), two new
    integrations, two new roles, four routes (same route keys as
    before, retargeted).
  * `monitoring` module's `monitored_lambda_names` adds the two new
    Lambdas so the existing Errors / Throttles alarms cover them.

## Apply sequence

```bash
cd terraform
terraform init   # picks up the moved files
terraform plan
```

Expected plan:

* Destroy:
  * `aws_lambda_function.submit_form_to_ace`
  * `aws_cloudwatch_log_group.submit_form_to_ace`
  * `aws_apigatewayv2_integration.submit_form`
  * `aws_apigatewayv2_route.submit_form_submit`
  * `aws_apigatewayv2_route.submit_form_update`
  * `aws_apigatewayv2_route.submit_form_solutions`
  * `aws_apigatewayv2_route.submit_form_products`
  * `aws_lambda_permission.submit_form_api_invoke`
  * `aws_iam_role.submit_form` + `aws_iam_role_policy.submit_form`

* Create:
  * `aws_lambda_function.ui_extension_reads` + log group + integration
    + 2 routes + permission + role + role_policy
  * `aws_lambda_function.ui_extension_writes` + log group + integration
    + 2 routes + permission + role + role_policy

The route keys (`POST /ui-extension/submit`, etc.) are identical
before and after, but the underlying integration changes. API Gateway
requires the old route to be deleted before the new one with the same
key is created, so there is a brief (seconds) window during apply
where any of the four UI Extension routes return 404. For a card with
low BD traffic this is acceptable; apply outside of demo windows.

```bash
terraform apply
```

### Apply-time gotcha: HubSpot CLIENT_SECRET stays the same

The HubSpot signing secret is unchanged; the new Lambdas read the
same Secrets Manager secret the old Lambda read. No HubSpot-side
work is needed; the new Lambdas pick up the signature validation
automatically.

### Post-apply smoke test

1. **GET /solutions on the reads Lambda**: open the Submit-to-AWS
   card on any deal. The SolutionPicker dropdown should populate
   exactly as before. Confirms the reads Lambda's
   `partnercentral:ListSolutions` IAM works under the new tight role.

2. **POST /submit on the writes Lambda**: pick a test deal, click
   Submit. Same end-to-end behavior as before the split. Confirms
   the writes Lambda's HubSpot PAT + DDB + signature validation.

3. **POST /update on the writes Lambda**: open the update form on a
   previously-submitted deal, change `dealname`, click Update.
   Confirms the writes Lambda's UpdateOpportunity + HubSpot writeback.

4. **CloudWatch logs**: confirm the new log groups
   `/aws/lambda/<prefix>-ui-ext-reads` and `<prefix>-ui-ext-writes`
   are receiving entries; the old `<prefix>-submit-form-to-ace`
   group will be retained per the existing retention policy
   (`var.log_retention_days`) but stops receiving new entries.

## Rollback

If something goes wrong:

```bash
git revert <this-commit-sha>
terraform plan   # reverse of the above: destroy new, recreate old
terraform apply
```

There is no AWS-side data tied to either Lambda, so the rollback is
clean. The brief route-gap during the rollback's destroy+create is
the same shape as the forward apply.
