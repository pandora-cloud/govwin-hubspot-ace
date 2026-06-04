# Screenshot checklist for the Business Development User Guide

The [BD User Guide](../../bd-user-guide.md) references the PNG files listed
below. Capture each one and drop it in this folder using the exact filename.
Until then, the guide renders broken-image placeholders, which is expected.

## Capture rules

This repository is public. Every screenshot must use the labeled **DEMO** deal
or masked data. Before capturing:

- Use a deal whose name clearly marks it as a demo (for example, a deal named
  with a DEMO prefix), not a real customer opportunity. The demo opportunity
  uses the "Customer demo" source prefix, so its identifier is obviously
  synthetic (for example `DEMO-EXAMPLE-ZTA-001`).
- Mask or avoid any real customer name, real GovWin Opportunity ID, real AWS
  account ID (12 digits), real AWS opportunity ID, and any internal email
  address or person's name.
- Do not show portal IDs, account IDs, or URLs that identify your organization.

The Submit form is longer than one screen. Capture it as two shots
(`submit-form-1.png` and `submit-form-2.png`) at 100 percent zoom rather than
one zoomed-out shot, so the text stays readable.

## Shots

| Filename | What to capture | What to mask |
|---|---|---|
| `card-not-submitted.png` | The **Submit to AWS Partner Central** card on a deal that has not been submitted yet, showing the **Submit to AWS** button and the Not submitted status. | Deal name (use the DEMO deal), associated company name. |
| `submit-form-1.png` | The top of the open Submit form: the deal source (synthetic ID builder) and the required ACE classification fields, Partner Need from AWS, Delivery Model, and Customer Use Case. | Any pre-filled GovWin Opportunity ID, customer name. |
| `submit-form-2.png` | The bottom of the Submit form: Customer and Project sections, the AWS Solution picker, and the **Submit to AWS** button. | AWS account ID, customer name. |
| `card-in-review.png` | The card after submission, showing the status badge reading **Under AWS review** (or **Submitted to AWS**) and the AWS opportunity identifier area. | The AWS opportunity ID, GovWin ID. |
| `update-closed-lost.png` | The Update form's **LifeCycle** section with the **Stage** dropdown set to **Closed Lost** and the **Closed Lost reason** dropdown showing. | The locked GovWin ID and AWS opportunity ID at the top of the form. |
