# Business Development User Guide

This guide is for the Business Development (BD) team member who works deals in
HubSpot and submits them to Amazon Web Services (AWS) Partner Central for
co-sell. You do everything from a single card on the HubSpot deal record. You do
not need Terraform, the AWS console, or any command line.

If you are looking for the technical or deployment documentation, see the
[README](../README.md) and the [Deployment Guide](deployment-guide.md). This
page is only the day-to-day operator workflow.

---

## 1. What this does

Opportunities you mark for download in Deltek GovWin IQ flow automatically into
HubSpot as deals on the GovWin Pipeline. You review each deal, and when it is
ready you submit it to AWS Partner Central directly from a card on the deal. AWS
reviews the submission; as the AWS review status changes, that status flows back
onto the deal on its own. You never have to log in to AWS Partner Central to
check on a deal: the card shows you where it stands.

In short: GovWin brings the opportunity in, you submit it from the card, and AWS
status comes back to the card automatically.

---

## 2. Before you start

- **Where deals appear.** Synced opportunities land on the **GovWin Pipeline**
  in HubSpot. Each one is a deal record. Open a deal to see its details and the
  submission card.
- **What "marked for sync" means.** Only opportunities you mark for download in
  GovWin IQ are pulled into HubSpot. This is deliberate: it keeps HubSpot focused
  on the opportunities you actually intend to pursue, instead of every record in
  GovWin. If a deal you expected is not in HubSpot, check that it is marked in
  GovWin and wait for the next sync (the pipeline checks GovWin hourly).
- **The card.** On every deal you will see a card titled **Submit to AWS Partner
  Central**. That card is your entire workflow: it shows current status, opens
  the Submit form, and opens the Update form.
- **Who to contact for access.** If you do not see the GovWin Pipeline or the
  card, or a deal is missing, contact your HubSpot administrator or the
  integration owner for your organization.

![The Submit to AWS Partner Central card on a HubSpot deal](images/bd/card-not-submitted.png)
*The card as it appears on a deal that has not yet been submitted.*

---

## 3. Submitting a deal to AWS

When a deal is ready for co-sell, submit it from the card.

1. Open the deal on the GovWin Pipeline.
2. On the **Submit to AWS Partner Central** card, click **Submit to AWS**. The
   Submit form opens.
3. Fill in the form. Three fields are required:
   - **Partner Need from AWS** (pick at least one): what you want AWS to help
     with on this deal, such as deal support.
   - **Delivery Model** (pick at least one): how the solution is delivered.
   - **Customer Use Case**: the primary use case for the customer.
   - There are more fields you will usually want to set, covered below, but
     those three are the ones the form will not let you skip.

   ![Top of the Submit form: source and the required ACE classification fields](images/bd/submit-form-1.png)
   *Top of the Submit form: the deal source and the required classification
   fields.*

4. Review the common optional fields:
   - **Source of the deal.** If the deal came from GovWin IQ, leave the "This
     deal came from GovWin IQ" box checked and confirm the GovWin Opportunity
     ID. If it did not come from GovWin, uncheck the box and build a synthetic
     ID with the helper; the helper walks you through it. This identifier is
     permanent in AWS, so for a non-GovWin deal pick an identifier you have not
     used before.
   - **Customer AWS Account ID.** Twelve digits, used for launch attribution. If
     the customer has not shared it yet, check **Not provided yet** to skip it
     for now and add it later from the Update form.
   - **Industry** and, when Industry is Government, **National Security**.
   - **Deal Name, Description, Amount, Close date.** Description must be at least
     20 characters when you enter one.
   - **AWS Solution.** Pick the solution from the dropdown. The list comes from
     your AWS Partner Central solutions. In the AWS (production) catalog a
     solution is required; in Sandbox it is optional.
   - **AWS Products, Marketing attribution, Notes.** Optional.
5. Click **Submit to AWS**.

![Bottom of the Submit form: customer, project, AWS solution, and the Submit button](images/bd/submit-form-2.png)
*Bottom of the Submit form: customer and project details, AWS solution, and the
Submit to AWS button.*

**What happens next.** When you submit, the deal stage advances on its own to
the submission stage, and the card begins showing AWS status. **Do not drag the
deal stage yourself**; submitting from the card moves it for you. Within a few
minutes the card status changes from Queued to Submitted to AWS.

---

## 4. Tracking AWS review status

After you submit, the card shows a read-only status badge that follows the deal
through AWS review. You do not refresh anything; the status updates itself as
AWS acts on the submission. The statuses you will see:

| Card status | What it means |
|---|---|
| **Queued** | Your submission was accepted and is being sent to AWS. |
| **Submitted to AWS** | AWS has received the opportunity. |
| **Under AWS review** | AWS is reviewing the opportunity. |
| **Approved by AWS** | AWS approved the co-sell opportunity. |
| **Action required** | AWS needs something from you; the card shows the next steps AWS sent. |
| **Closed lost** | The opportunity ended without a win. |
| **Launched** | The opportunity launched (a win). |

A typical AWS review takes one to three business days. When the status is
**Action required**, the card shows the next steps AWS provided so you know what
to address.

![The card showing an Under AWS review status badge](images/bd/card-in-review.png)
*The status badge tracks the AWS-side review without any action from you.*

---

## 5. Updating a live deal

After a deal is submitted, the card's button changes to **Update opportunity in
AWS**. Use the Update form to change details on a deal that is already in AWS:
amount, close date, deal name, description, the classification fields, and the
customer AWS account ID if you skipped it at submission.

Two things to know:

- The GovWin or synthetic identifier and the AWS opportunity are locked at the
  top of the Update form. You can see them, but you cannot change them. They are
  permanent in AWS for the life of the opportunity. If you truly need a
  different opportunity, clone the deal in HubSpot and submit the clone.
- **Edits are blocked by AWS while a deal is in active review.** While AWS has
  the deal in Submitted or Under review, AWS does not accept changes, including
  closing it. If your edit does not seem to take during that window, that is
  why; wait until the review finishes (Approved or Action required) and then
  make the change.

---

## 6. Closing or launching a deal

You close or launch a deal from the Update form, not by dragging stages.

1. Open the deal and click **Update opportunity in AWS** on the card.
2. In the **LifeCycle** section, open the **Stage** dropdown.
3. To close the deal as lost, pick **Closed Lost**. A **Closed Lost reason**
   dropdown appears; you must pick a reason (AWS requires it).
4. To record a win, pick **Launched**. The deal moves to Closed Won.
5. Click **Update opportunity**.

Once a deal is **Launched** or **Closed Lost**, it is terminal: AWS no longer
accepts further changes, and the card disables the Update button. The deal
settles on the terminal stage automatically, and a Closed Lost reason is
mirrored back onto the deal, so what you see in HubSpot matches AWS.

![The Update form LifeCycle Stage dropdown with Closed Lost selected](images/bd/update-closed-lost.png)
*Closing a deal: pick Closed Lost (with a reason) or Launched from the Stage
dropdown.*

![The Submit to AWS Partner Central card on a closed-lost deal](images/bd/card-closed-lost.png)
*What a terminal Closed Lost deal looks like: the card status badge reads
Closed lost, the Update button is disabled, and the AWS opportunity ID is
locked at the top.*

---

## 7. What NOT to do

- **Do not drag pipeline stages by hand.** Submitting from the card advances the
  stage for you, and AWS status flows back to set the stage. Dragging a stage
  yourself can put the deal out of step with AWS.
- **Do not hand-edit the AWS co-sell properties** (the fields whose names start
  with `govwin_ace_`). The card and the integration own those. Editing them
  directly can trigger an alert and confuse the deal's state.
- **Do not resubmit a deal that is already in review.** It already has an
  opportunity in AWS; the card will tell you so. Use the Update form instead.
- **Do not reuse a synthetic identifier** you have used on another deal. AWS
  keeps these unique forever.

---

## 8. Troubleshooting and who to call

- **A deal I marked in GovWin is not in HubSpot.** Confirm it is marked for
  download in GovWin IQ and give it up to an hour for the next sync.
- **The card says this deal already has an AWS opportunity.** It was already
  submitted. Refresh the card to see its current status, and use the Update form
  for any changes.
- **My edit did not take.** If the deal is in Submitted or Under review, AWS is
  blocking edits during its review. Wait for the review to finish, then retry.
- **The status badge says Status unknown.** Refresh the card. If it persists,
  contact the integration owner.
- **Anything else.** Contact your HubSpot administrator or the integration owner
  for your organization.

---

## 9. Frequently asked questions

### What do I pick for AWS Solution? It says it is required and the dropdown is huge.

The **AWS Solution** dropdown on the Submit form lists every Solution your organization has registered in AWS Partner Central. A Solution is your packaged offering (software, services, or a bundle) that AWS has validated. Each opportunity has to be associated with at least one Solution so the AWS reviewer knows what is being co-sold.

What to pick:

- **In the AWS (production) catalog**, a Solution is required. Your integration owner or PDM (AWS Partner Development Manager) will tell you which Solution is the default for your team. If you are not sure, pick the one whose name and category most closely match what you are actually selling on this deal.
- **In the Sandbox catalog**, a Solution is optional. If you leave it blank, the integration falls back to a `_NONE_REGISTERED_` placeholder so you can still smoke-test the flow without an Approved Solution registered.
- You can change the Solution on the Update form later if you picked the wrong one, but only while the deal is *not* in Submitted or Under review.

If the dropdown is empty when you open the Submit form, your AWS Partner Central account does not yet have an Approved Solution. Talk to your integration owner; you cannot submit to the production catalog until at least one Solution is registered and approved.

### What is the AWS Co-sell ID property on the deal? Should I edit it?

The `govwin_aws_cosell_id` property is an **audit-only** field. The integration writes the AWS-side opportunity identifier here after a successful submission so anyone looking at the deal in HubSpot can cross-reference it to the opportunity in Partner Central. It is not used for routing or business logic; it is read-only context for humans.

**Never edit it by hand.** The card and the `handle_ace_event` Lambda own the value. Hand-editing can de-sync the HubSpot deal from its AWS counterpart and trigger an SNS alert. If the value looks wrong, contact your integration owner; do not try to fix it from the property panel.

### My deal has been Submitted for days. Is something broken?

AWS reviewer SLAs for co-sell opportunities are typically **5 to 10 business days**, but can stretch longer for complex deals, specific industries, or during AWS reporting weeks. A deal sitting in **Submitted to AWS** or **Under AWS Review** for under two weeks is normal, not stuck.

What to check before escalating:

1. **Card status badge.** If it shows **Action Required**, AWS has come back asking for something. The badge text plus the card's status section tell you what; act on it from the Update form, do not just resubmit.
2. **AWS review status** in the card. **In review** is normal; **Submitted** with no further activity past two weeks is worth a follow-up with your PDM.
3. **The deal's last activity in HubSpot** vs the AWS-side last update. The integration mirrors AWS state every time AWS publishes an EventBridge event; if the dates are weeks apart, AWS has not touched the deal recently.

If the deal is genuinely stuck past two weeks with no Action Required, contact your PDM with the AWS opportunity ID (visible in the card and stored in the `govwin_aws_cosell_id` property). Do not resubmit; the original submission still has the engagement task on AWS's side.

### I changed the deal Amount but the card still shows the old number.

If the deal is currently in **Submitted to AWS** or **Under AWS Review**, AWS is blocking changes to the underlying opportunity. The integration sees your edit, recognizes the review-window block, and parks the edit until review exits. Once AWS responds (Approved, Action Required, or a terminal state), the parked edit replays automatically. You do not need to do anything.

The same parking behavior applies to: Amount, Close Date, Deal Name, Description, Customer Use Case, Delivery Model, and Partner Need from AWS. The Closed Lost reason is a special case (see below).

### I want to Close Lost a deal but the Stage dropdown will not save.

Closing a deal during the active review window is also blocked by AWS, the same way other edits are. The Update form will accept your **Closed Lost** selection and the **Closed Lost reason**, and the integration parks the change until AWS review finishes. Once review exits, the parked Closed Lost replays automatically.

If you need the deal to close immediately and the wait is unacceptable, contact your integration owner; they can manually nudge the AWS-side state through the operator runbook, but that is exceptional.

### I do not see the GovWin Pipeline at all.

Your HubSpot user does not have access to the pipeline, or the pipeline has not been created yet. Both are administrator issues. Confirm with your HubSpot administrator that:

1. The pipeline named **GovWin Pipeline** exists under Settings -> Objects -> Deals -> Pipelines, and
2. Your user has visibility to it (HubSpot scopes pipeline visibility by team and permission).

This is a one-time setup; once your administrator grants access, the pipeline shows up on every deal record.
