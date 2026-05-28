// SubmitForm: the modal form BD fills out to submit a deal to AWS Partner
// Central. Composes the three pickers (SyntheticIdHelper, SolutionPicker,
// AwsProductsPicker) plus inline inputs for the rest of the
// CreateOpportunity payload. Posts to POST /ui-extension/submit.

import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Divider,
  Flex,
  Heading,
  Input,
  MultiSelect,
  NumberInput,
  Select,
  Text,
  TextArea,
  hubspot,
} from "@hubspot/ui-extensions";

import { SolutionPicker } from "./SolutionPicker";
import { AwsProductsPicker } from "./AwsProductsPicker";
import {
  CLOSED_LOST_REASONS,
  COMPETITORS,
  DELIVERY_MODELS,
  LIFECYCLE_STAGES,
  MARKETING_CHANNELS,
  MARKETING_SOURCES,
  OPPORTUNITY_TYPES,
  PARTNER_NEED_OPTIONS,
  SALES_ACTIVITIES,
  USE_CASES,
} from "./enums";

// Mode flag passed by SubmitToAwsCard. "create" is the initial-submission
// path; "update" is the post-submission editor that drives the existing
// AWS opportunity through LifeCycle transitions and field changes.
//
// In "update":
//   - Synthetic GovWin ID builder is hidden; the existing govwin_opp_id is
//     locked and shown read-only (AWS PartnerOpportunityIdentifier is
//     immutable once a successful CreateOpportunity has been recorded).
//   - A LifeCycle section appears with the Stage dropdown and a conditional
//     ClosedLostReason picker.
//   - The submit button posts to POST /ui-extension/update which calls
//     AWS UpdateOpportunity synchronously rather than the dealstage-flip
//     -> webhook -> submit_to_ace pipeline.
//   - Field locks: PartnerOpportunityIdentifier (synthetic id), Origin,
//     OpportunityTeam, and AWS reviewer-controlled LifeCycle.ReviewStatus.
export type SubmitFormMode = "create" | "update";

interface Props {
  apiBaseUrl: string;
  catalog: string;
  dealId: string;
  mode: SubmitFormMode;
  defaultDealName: string;
  defaultCompanyName: string;
  defaultIndustry: string | null;
  defaultAmount: number | null;
  defaultCloseDate: string | null;
  defaultDescription: string | null;
  existingGovwinOppId: string | null;
  // Update-mode only: the AWS opp identifier the form is editing. Locked
  // and displayed read-only at the top of the form so BD always sees which
  // AWS-side opportunity their edits will modify.
  existingAceOpportunityId?: string | null;
  // Update-mode only: the current LifeCycle.Stage as last seen by the
  // card. Used to pre-fill the stage dropdown so a no-op update doesn't
  // accidentally walk the stage backward.
  existingLifecycleStage?: string | null;
  // Update-mode pre-fill: the last known state of the ACE classification
  // fields, sourced from HubSpot deal properties the card already loaded.
  // Without these the form opens with create-mode defaults and BD sees
  // empty pickers for Delivery Model / Partner Need / etc. -- they'd
  // either re-pick (risk of accidental change) or skip (and submit nulls,
  // which the mapper treats as "no change" but is confusing UX).
  defaultPartnerNeed?: string[];
  defaultDeliveryModel?: string[];
  defaultUseCase?: string | null;
  defaultOpportunityType?: string | null;
  defaultSalesActivities?: string[];
  defaultCompetitor?: string | null;
  defaultOtherCompetitorNames?: string | null;
  defaultAwsAccountId?: string | null;
  defaultNationalSecurity?: string | null;
  defaultSolutionId?: string | null;
  defaultAwsProducts?: string[];
  defaultAdditionalComments?: string | null;
  defaultMarketingSource?: string | null;
  defaultMarketingCampaign?: string | null;
  defaultMarketingChannels?: string[];
  defaultMarketingUseCases?: string[];
  defaultMarketingFundingUsed?: string | null;
  onSubmissionQueued: (response: { ace_opportunity_id?: string | null }) => void;
  onCancel: () => void;
}

interface FormState {
  fromGovWin: boolean;
  govwinOppId: string;
  // ACE classification
  partnerNeed: string[];
  deliveryModel: string[];
  useCase: string;
  opportunityType: string;
  salesActivities: string[];
  competitor: string;
  otherCompetitorNames: string;
  // Customer
  industry: string;
  awsAccountId: string;
  nationalSecurity: string;
  awsAccountUnknown: boolean;
  // Project
  dealName: string;
  description: string;
  amount: string;
  closeDate: string;
  // Solutions / products
  solutionId: string;
  awsProducts: string[];
  // Marketing
  marketingEnabled: boolean;
  marketingSource: string;
  marketingChannels: string[];
  marketingCampaign: string;
  marketingFundingUsed: string;
  // Notes
  additionalComments: string;
  nextSteps: string;
  // LifeCycle (update mode only)
  lifecycleStage: string;
  lifecycleClosedLostReason: string;
}

interface FieldError {
  field: string;
  message: string;
}

const TODAY_PLUS_180 = (): string => {
  const d = new Date();
  d.setDate(d.getDate() + 180);
  return d.toISOString().slice(0, 10);
};

export const SubmitForm: React.FC<Props> = ({
  apiBaseUrl,
  catalog,
  dealId,
  mode,
  defaultDealName,
  defaultCompanyName,
  defaultIndustry,
  defaultAmount,
  defaultCloseDate,
  defaultDescription,
  existingGovwinOppId,
  existingAceOpportunityId,
  existingLifecycleStage,
  defaultPartnerNeed,
  defaultDeliveryModel,
  defaultUseCase,
  defaultOpportunityType,
  defaultSalesActivities,
  defaultCompetitor,
  defaultOtherCompetitorNames,
  defaultAwsAccountId,
  defaultNationalSecurity,
  defaultSolutionId,
  defaultAwsProducts,
  defaultAdditionalComments,
  defaultMarketingSource,
  defaultMarketingCampaign,
  defaultMarketingChannels,
  defaultMarketingUseCases,
  defaultMarketingFundingUsed,
  onSubmissionQueued,
  onCancel,
}) => {
  const isUpdate = mode === "update";
  // In update mode the snapshot from the card carries the last-known ACE
  // classification fields the BD selected when this opportunity was
  // submitted. Seed the form state from those instead of the create-mode
  // defaults so BD sees the current state and can edit incrementally.
  // Falls back to create-mode defaults when the prop is missing (covers
  // both create-mode and update-mode opps that pre-date when these
  // properties were tracked on the deal).
  const [state, setState] = useState<FormState>({
    fromGovWin: Boolean(existingGovwinOppId),
    govwinOppId: existingGovwinOppId ?? "",
    partnerNeed: defaultPartnerNeed && defaultPartnerNeed.length > 0
      ? defaultPartnerNeed
      : ["Deal Support"],
    deliveryModel: defaultDeliveryModel ?? [],
    useCase: defaultUseCase ?? "",
    opportunityType: defaultOpportunityType ?? "Net New Business",
    salesActivities: defaultSalesActivities && defaultSalesActivities.length > 0
      ? defaultSalesActivities
      : ["Initialized discussions with customer"],
    competitor: defaultCompetitor ?? "",
    otherCompetitorNames: defaultOtherCompetitorNames ?? "",
    industry: defaultIndustry ?? "Government",
    awsAccountId: defaultAwsAccountId ?? "",
    nationalSecurity: defaultNationalSecurity ?? "",
    // Infer "Not provided yet" in update mode when there's no account id
    // on the deal. The original toggle state isn't persisted to HubSpot
    // -- only the account id is -- so an empty id in update mode means
    // BD either skipped via the toggle at create time OR the customer
    // hasn't shared an account id yet. Either way the right default is
    // checkbox ON so the form reflects "still don't have one" rather
    // than implying BD just forgot to fill in a number they had.
    awsAccountUnknown: isUpdate && !defaultAwsAccountId,
    dealName: defaultDealName ?? "",
    description: defaultDescription ?? "",
    amount: defaultAmount != null ? String(defaultAmount) : "",
    closeDate: defaultCloseDate ?? TODAY_PLUS_180(),
    solutionId: defaultSolutionId ?? "",
    awsProducts: defaultAwsProducts ?? [],
    marketingEnabled: Boolean(defaultMarketingSource && defaultMarketingSource !== "None"),
    marketingSource: defaultMarketingSource ?? "None",
    marketingChannels: defaultMarketingChannels ?? [],
    marketingCampaign: defaultMarketingCampaign ?? "",
    marketingFundingUsed: defaultMarketingFundingUsed ?? "",
    additionalComments: defaultAdditionalComments ?? "",
    nextSteps: "",
    lifecycleStage: existingLifecycleStage ?? "Qualified",
    lifecycleClosedLostReason: "",
  });
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setState((s) => ({ ...s, [key]: value }));
  };

  const validate = (): FieldError[] => {
    const errs: FieldError[] = [];
    // In update mode the synthetic-id builder is hidden -- the bound
    // govwin_opp_id (PartnerOpportunityIdentifier) is immutable per AWS
    // and we never need to revalidate it. Only check the id machinery
    // when creating a new opportunity.
    if (!isUpdate) {
      if (state.fromGovWin && !state.govwinOppId.trim()) {
        errs.push({ field: "govwin_opp_id", message: "GovWin Opportunity ID is required." });
      }
      // Non-GovWin deals get an auto-minted UUID at submit time -- no
      // user input to validate. The dealname (validated below) is the
      // human-readable label AWS reviewers see.
    }
    if (isUpdate) {
      if (!state.lifecycleStage) {
        errs.push({ field: "lifecycle_stage", message: "Pick a LifeCycle Stage." });
      }
      if (state.lifecycleStage === "Closed Lost" && !state.lifecycleClosedLostReason) {
        errs.push({
          field: "lifecycle_closed_lost_reason",
          message: "Closed Lost requires a reason.",
        });
      }
    }
    if (!isUpdate && state.partnerNeed.length === 0) {
      errs.push({ field: "partner_need", message: "Pick at least one Partner Need." });
    }
    if (!isUpdate && state.deliveryModel.length === 0) {
      errs.push({ field: "delivery_model", message: "Pick at least one Delivery Model." });
    }
    if (!isUpdate && !state.useCase) {
      errs.push({ field: "use_case", message: "Pick a Customer Use Case." });
    }
    // Format check applies in both modes; "required" gate only on create.
    if (!state.awsAccountUnknown && state.awsAccountId && !/^\d{12}$/.test(state.awsAccountId)) {
      errs.push({ field: "aws_account_id", message: "Must be exactly 12 digits." });
    }
    if (!isUpdate && !state.awsAccountUnknown && !state.awsAccountId) {
      errs.push({
        field: "aws_account_id",
        message: "Required for AWS launch attribution. Toggle 'Not provided yet' to skip.",
      });
    }
    if (state.nationalSecurity === "Yes" && state.industry !== "Government") {
      errs.push({
        field: "national_security",
        message: "NationalSecurity=Yes is only valid when Industry=Government.",
      });
    }
    if (state.description && state.description.length < 20) {
      errs.push({
        field: "description",
        message: "Description must be at least 20 characters when provided.",
      });
    }
    if (state.competitor === "*Other" && !state.otherCompetitorNames) {
      errs.push({
        field: "other_competitor_names",
        message: "Specify the competitor when Competitor is *Other.",
      });
    }
    if (state.marketingEnabled && state.marketingSource !== "Marketing Activity") {
      errs.push({
        field: "marketing_source",
        message:
          "Set Source to Marketing Activity to include marketing data, or disable the Marketing section.",
      });
    }
    return errs;
  };

  const submit = async () => {
    setSubmitError(null);
    const errs = validate();
    setErrors(errs);
    if (errs.length > 0) return;

    setSubmitting(true);
    // In update mode the bound govwin_opp_id (PartnerOpportunityIdentifier)
    // is immutable and the form locks it. In create mode: either BD's
    // GovWin id (when fromGovWin=true) or an auto-minted UUID. The UUID
    // replaces the older SOURCE-CUSTOMER-PROJECT-NNN scheme because AWS
    // enforces uniqueness across the catalog FOREVER (see Option C E2E
    // 2026-05-27): a human-readable synthetic id BD might want to
    // recycle was never actually recyclable, and AWS reviewers see the
    // deal name (Project.Title) for human context anyway.
    const govwin_opp_id = isUpdate
      ? (existingGovwinOppId ?? state.govwinOppId.trim())
      : state.fromGovWin
        ? state.govwinOppId.trim()
        : `pc-${crypto.randomUUID()}`;

    const payload: Record<string, unknown> = {
      deal_id: dealId,
      govwin_opp_id,
      govwin_agency: defaultCompanyName,
      govwin_industry: state.industry,
      dealname: state.dealName,
      description: state.description,
      // state.amount is the form-input string; treat any non-empty string
      // as a value (including "0" which is falsy as a string but a valid
      // user-entered amount they may want to clear with). parseFloat("")
      // is NaN; we send null instead so Pydantic + AWS don't see NaN.
      amount: state.amount !== "" && state.amount != null ? parseFloat(state.amount) : null,
      ace_partner_need: state.partnerNeed,
      ace_delivery_model: state.deliveryModel,
      ace_use_case: state.useCase,
      ace_opportunity_type: state.opportunityType,
      ace_sales_activities: state.salesActivities,
      ace_competitor_name: state.competitor || null,
      ace_other_competitor_names: state.otherCompetitorNames || null,
      ace_aws_account_id: state.awsAccountUnknown ? null : state.awsAccountId || null,
      ace_national_security: state.nationalSecurity || null,
      ace_solution_id: state.solutionId || null,
      ace_aws_products: state.awsProducts,
      ace_additional_comments: state.additionalComments || null,
    };
    // Field name differs across endpoints: /submit takes closedate +
    // ace_next_steps; /update takes lifecycle_target_close_date +
    // lifecycle_next_steps and the LifeCycle stage payload.
    if (isUpdate) {
      payload.lifecycle_stage = state.lifecycleStage;
      payload.lifecycle_closed_lost_reason =
        state.lifecycleStage === "Closed Lost" ? state.lifecycleClosedLostReason : null;
      payload.lifecycle_next_steps = state.nextSteps || null;
      payload.lifecycle_target_close_date = state.closeDate || null;
    } else {
      payload.closedate = state.closeDate;
      payload.ace_next_steps = state.nextSteps || null;
    }
    if (state.marketingEnabled) {
      payload.marketing = {
        Source: state.marketingSource,
        Channels: state.marketingChannels,
        CampaignName: state.marketingCampaign || null,
        AwsFundingUsed: state.marketingFundingUsed || null,
      };
    }

    try {
      // HubSpot's proxy rejects any request header other than Authorization
      // (VALIDATION_ERROR: "Only 'Authorization' header is allowed from
      // hubspot.fetch()"). The proxy sets Content-Type: application/json on
      // POSTs with a string body automatically, so we just pass body.
      const endpoint = isUpdate ? "/ui-extension/update" : "/ui-extension/submit";
      const response = await hubspot.fetch(`${apiBaseUrl}${endpoint}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      // hubspot.fetch sometimes returns a Response with an empty body on
      // non-2xx (the upstream Lambda actually sent a JSON error body but
      // the iframe bridge stripped it). Read as text first so an empty
      // response surfaces as a real status-aware error instead of a
      // generic JSON-parse "unexpected end of data".
      const raw = await response.text();
      const body = raw
        ? JSON.parse(raw)
        : { status: "unknown", message: `HTTP ${response.status} (empty body)` };
      // Create returns 202 (queued); Update returns 200 (applied synchronously).
      if (response.status === 202 || (isUpdate && response.status === 200)) {
        onSubmissionQueued({ ace_opportunity_id: body.ace_opportunity_id ?? null });
        return;
      }
      if (response.status === 409) {
        // Two distinct 409 cases the backend distinguishes via body.status:
        //   "already_submitted": dedup -- this govwin id already has an ACE
        //     opportunity. body.ace_opportunity_id is set.
        //   "replay_detected": the request was retried inside the 10-min
        //     signature window (transparent hubspot.fetch retry on a transient
        //     failure). body.ace_opportunity_id is undefined; the original
        //     request is or will be processed; user should reload the card.
        if (body.status === "replay_detected") {
          setSubmitError(
            "Request was retried by HubSpot. The original submission is being processed. " +
            "Reload the card to see the result."
          );
        } else if (body.status === "already_submitted" && body.ace_opportunity_id) {
          setSubmitError(
            `This deal already has an opportunity in AWS Partner Central ` +
            `(${body.ace_opportunity_id}). Refresh the card to see its current state.`
          );
        } else {
          setSubmitError(
            body.message || "This deal already has an active AWS Partner Central submission."
          );
        }
        return;
      }
      if (response.status === 400) {
        setErrors(
          (body.errors ?? []).map((e: any) => ({
            field: String(e.field ?? "form"),
            message: String(e.message ?? "validation failed"),
          }))
        );
        return;
      }
      setSubmitError(body.message ?? `Submission failed with status ${response.status}`);
    } catch (err) {
      setSubmitError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const errorFor = (field: string): string | undefined => {
    const e = errors.find((err) => err.field === field || err.field === `ace_${field}`);
    return e?.message;
  };

  // Bisection v1: Sections 1-4 active. SyntheticIdHelper / SolutionPicker /
  // AwsProductsPicker child components + Marketing/Notes sections still
  // stripped to narrow down the runtime crash.
  return (
    <Flex direction="column" gap="md">
      <Heading>
        {isUpdate
          ? "Update opportunity in AWS"
          : "Submit deal to AWS Partner Central"}
      </Heading>

      {submitError ? (
        <Alert title={isUpdate ? "Update error" : "Submission error"} variant="error">
          {submitError}
        </Alert>
      ) : null}
      {errors.length > 0 ? (
        <Alert title={`Fix ${errors.length} field${errors.length === 1 ? "" : "s"} before ${isUpdate ? "updating" : "submitting"}`} variant="error">
          <Flex direction="column" gap="xs">
            {errors.map((e, i) => (
              <Text key={`${e.field}-${i}`} variant="microcopy">
                {`• ${e.field}: ${e.message}`}
              </Text>
            ))}
          </Flex>
        </Alert>
      ) : null}

      <Divider />

      {isUpdate ? (
        // Update mode: the PartnerOpportunityIdentifier (synthetic GovWin
        // id) is immutable once a successful CreateOpportunity has been
        // recorded; AWS enforces uniqueness for the lifetime of the
        // catalog. Display read-only so BD can see what they're editing.
        <Flex direction="column" gap="xs">
          <Text format={{ fontWeight: "bold" }}>Bound opportunity (locked)</Text>
          <Text variant="microcopy">
            {`GovWin ID: ${existingGovwinOppId ?? "(missing)"}`}
          </Text>
          <Text variant="microcopy">
            {`AWS opportunity: ${existingAceOpportunityId ?? "(missing)"}`}
          </Text>
          <Text variant="microcopy">
            Synthetic IDs cannot be changed after the first successful submission.
            For a different opportunity, clone this deal in HubSpot and submit
            the clone.
          </Text>
        </Flex>
      ) : (
        <>
          {/* Section 1: Source (create mode only). Checkbox picks between
              real GovWin ID (checked) and the synthetic builder (unchecked). */}
          <Checkbox
            name="from_govwin"
            checked={state.fromGovWin}
            onChange={(checked) => update("fromGovWin", checked)}
          >
            This deal came from GovWin IQ
          </Checkbox>
          {state.fromGovWin ? (
            <Input
              name="govwin_opp_id"
              label="GovWin Opportunity ID"
              description="From GovWin IQ. Often looks like OPP123456 or BID987654."
              value={state.govwinOppId}
              onChange={(v) => update("govwinOppId", String(v ?? ""))}
              error={Boolean(errorFor("govwin_opp_id"))}
              validationMessage={errorFor("govwin_opp_id")}
              readOnly={Boolean(existingGovwinOppId)}
            />
          ) : (
            // Non-GovWin deals get an auto-minted UUID for the AWS
            // PartnerOpportunityIdentifier. We don't show it; the deal
            // name (which BD already sets when creating the HubSpot
            // deal) is the human-readable label that AWS reviewers see
            // on the opportunity. Auto-minting avoids the irrevocable-
            // synthetic-id trap (AWS uniqueness is enforced per catalog
            // forever; the older SOURCE-CUSTOMER-NNN scheme made BD
            // responsible for not colliding).
            <Text variant="microcopy">
              A unique identifier will be auto-generated for AWS Partner
              Central tracking. The opportunity name AWS reviewers see is
              the deal name (editable in the Project section below).
            </Text>
          )}
        </>
      )}

      {isUpdate ? (
        <>
          <Divider />
          <Heading>LifeCycle</Heading>
          <Text variant="microcopy">
            Drives the AWS-side opportunity progression. Once Stage is set to
            Launched or Closed Lost the opportunity is terminal and no further
            updates can be applied.
          </Text>
          <Select
            name="lifecycle_stage"
            label="Stage"
            value={state.lifecycleStage}
            onChange={(v) => update("lifecycleStage", String(v ?? ""))}
            options={LIFECYCLE_STAGES.map((s) => ({ label: s, value: s }))}
            error={Boolean(errorFor("lifecycle_stage"))}
            validationMessage={errorFor("lifecycle_stage")}
          />
          {state.lifecycleStage === "Closed Lost" ? (
            <Select
              name="lifecycle_closed_lost_reason"
              label="Closed Lost reason"
              description="Required by AWS when Stage is Closed Lost."
              value={state.lifecycleClosedLostReason}
              onChange={(v) =>
                update("lifecycleClosedLostReason", String(v ?? ""))
              }
              options={CLOSED_LOST_REASONS.map((r) => ({ label: r, value: r }))}
              error={Boolean(errorFor("lifecycle_closed_lost_reason"))}
              validationMessage={errorFor("lifecycle_closed_lost_reason")}
            />
          ) : null}
        </>
      ) : null}

      <Divider />

      {/* Section 2: ACE classification */}
      <Heading>ACE classification</Heading>
      <MultiSelect
        name="ace_partner_need"
        label="Partner Need from AWS"
        value={state.partnerNeed}
        onChange={(v) => update("partnerNeed", (v ?? []) as string[])}
        options={PARTNER_NEED_OPTIONS}
        error={Boolean(errorFor("partner_need"))}
        validationMessage={errorFor("partner_need")}
      />
      <MultiSelect
        name="ace_delivery_model"
        label="Delivery Model"
        value={state.deliveryModel}
        onChange={(v) => update("deliveryModel", (v ?? []) as string[])}
        options={DELIVERY_MODELS.map((m) => ({ label: m, value: m }))}
        error={Boolean(errorFor("delivery_model"))}
        validationMessage={errorFor("delivery_model")}
      />
      <Select
        name="ace_use_case"
        label="Customer Use Case"
        value={state.useCase}
        onChange={(v) => update("useCase", String(v ?? ""))}
        options={USE_CASES.map((u) => ({ label: u, value: u }))}
        error={Boolean(errorFor("use_case"))}
        validationMessage={errorFor("use_case")}
      />
      <Select
        name="ace_opportunity_type"
        label="Opportunity Type"
        value={state.opportunityType}
        onChange={(v) => update("opportunityType", String(v ?? ""))}
        options={OPPORTUNITY_TYPES.map((t) => ({ label: t, value: t }))}
      />
      <MultiSelect
        name="ace_sales_activities"
        label="Sales Activities"
        value={state.salesActivities}
        onChange={(v) => update("salesActivities", (v ?? []) as string[])}
        options={SALES_ACTIVITIES.map((a) => ({ label: a, value: a }))}
      />
      <Select
        name="ace_competitor_name"
        label="Competitor (optional)"
        value={state.competitor}
        onChange={(v) => update("competitor", String(v ?? ""))}
        options={[{ label: "(none)", value: "" }, ...COMPETITORS.map((c) => ({ label: c, value: c }))]}
      />

      <Divider />

      {/* Section 3: Customer */}
      <Heading>Customer</Heading>
      <Text variant="microcopy">
        Company: {defaultCompanyName || "(not associated)"}
      </Text>
      <Input
        name="govwin_industry"
        label="Industry"
        value={state.industry}
        onChange={(v) => update("industry", String(v ?? ""))}
      />
      <Flex direction="row" gap="sm">
        <Box flex={2}>
          <Input
            name="ace_aws_account_id"
            label="Customer AWS Account ID (12 digits)"
            value={state.awsAccountId}
            onChange={(v) => update("awsAccountId", String(v ?? "").replace(/\D/g, ""))}
            error={Boolean(errorFor("aws_account_id"))}
            validationMessage={errorFor("aws_account_id")}
            readOnly={state.awsAccountUnknown}
          />
        </Box>
        <Box flex="initial">
          <Checkbox
            name="aws_account_unknown"
            checked={state.awsAccountUnknown}
            onChange={(checked) => update("awsAccountUnknown", checked)}
          >
            Not provided yet
          </Checkbox>
        </Box>
      </Flex>
      {state.industry === "Government" ? (
        <Select
          name="ace_national_security"
          label="National Security"
          value={state.nationalSecurity}
          onChange={(v) => update("nationalSecurity", String(v ?? ""))}
          options={[
            { label: "No", value: "No" },
            { label: "Yes", value: "Yes" },
          ]}
        />
      ) : null}

      <Divider />

      {/* Section 4: Project */}
      <Heading>Project</Heading>
      <Input
        name="dealname"
        label="Deal Name"
        value={state.dealName}
        onChange={(v) => update("dealName", String(v ?? ""))}
      />
      <TextArea
        name="description"
        label="Description (min 20 chars)"
        value={state.description}
        onChange={(v) => update("description", String(v ?? ""))}
        error={Boolean(errorFor("description"))}
        validationMessage={errorFor("description")}
      />
      <Flex direction="row" gap="sm">
        <Box flex={1}>
          <NumberInput
            name="amount"
            label="Amount (annualized USD)"
            value={state.amount ? parseFloat(state.amount) : undefined}
            onChange={(v) => update("amount", v != null ? String(v) : "")}
          />
        </Box>
        <Box flex={1}>
          <Input
            name="closedate"
            label="Close date (YYYY-MM-DD)"
            value={state.closeDate}
            onChange={(v) => update("closeDate", String(v ?? ""))}
          />
        </Box>
      </Flex>

      <Divider />

      {/* Section 5: Solutions and AWS Products */}
      <Heading>AWS Solutions and Products</Heading>
      <SolutionPicker
        apiBaseUrl={apiBaseUrl}
        catalog={catalog}
        value={state.solutionId}
        onChange={(v) => update("solutionId", v)}
        required={catalog === "AWS"}
      />
      <AwsProductsPicker
        apiBaseUrl={apiBaseUrl}
        value={state.awsProducts}
        onChange={(v) => update("awsProducts", v)}
      />

      <Divider />

      {/* Section 6: Marketing (toggle-gated) */}
      <Checkbox
        name="marketing_enabled"
        checked={state.marketingEnabled}
        onChange={(checked) => update("marketingEnabled", checked)}
      >
        Include marketing attribution
      </Checkbox>
      {state.marketingEnabled ? (
        <Flex direction="column" gap="sm">
          <Select
            name="marketing_source"
            label="Marketing Source"
            value={state.marketingSource}
            onChange={(v) => update("marketingSource", String(v ?? ""))}
            options={MARKETING_SOURCES.map((s) => ({ label: s, value: s }))}
            error={Boolean(errorFor("marketing_source"))}
            validationMessage={errorFor("marketing_source")}
          />
          <MultiSelect
            name="marketing_channels"
            label="Channels"
            value={state.marketingChannels}
            onChange={(v) => update("marketingChannels", (v ?? []) as string[])}
            options={MARKETING_CHANNELS.map((c) => ({ label: c, value: c }))}
          />
          <Input
            name="marketing_campaign"
            label="Campaign Name"
            value={state.marketingCampaign}
            onChange={(v) => update("marketingCampaign", String(v ?? ""))}
          />
          <Select
            name="marketing_funding_used"
            label="AWS Funding Used"
            value={state.marketingFundingUsed}
            onChange={(v) => update("marketingFundingUsed", String(v ?? ""))}
            options={[
              { label: "(unspecified)", value: "" },
              { label: "Yes", value: "Yes" },
              { label: "No", value: "No" },
            ]}
          />
        </Flex>
      ) : null}

      <Divider />

      {/* Section 7: Notes */}
      <Heading>Notes</Heading>
      <TextArea
        name="ace_additional_comments"
        label="Additional comments"
        value={state.additionalComments}
        onChange={(v) => update("additionalComments", String(v ?? ""))}
      />
      <TextArea
        name="ace_next_steps"
        label="Next steps"
        value={state.nextSteps}
        onChange={(v) => update("nextSteps", String(v ?? ""))}
      />

      <Divider />

      {/* Mirror the top-of-form Alert immediately above the Submit button so
          feedback lands in the user's line of sight after they click. HubSpot
          UI Extensions run in a sandboxed iframe with no scrollIntoView, so
          duplicating the status block at the action point is the cleanest
          way to make 409/4xx/5xx responses visible without making the user
          scroll back up. */}
      {submitError ? (
        <Alert title="Submission error" variant="error">{submitError}</Alert>
      ) : null}
      {errors.length > 0 ? (
        <Alert title={`Fix ${errors.length} field${errors.length === 1 ? "" : "s"} before submitting`} variant="error">
          <Flex direction="column" gap="xs">
            {errors.map((e, i) => (
              <Text key={`bot-${e.field}-${i}`} variant="microcopy">
                {`• ${e.field}: ${e.message}`}
              </Text>
            ))}
          </Flex>
        </Alert>
      ) : null}

      <Flex direction="row" gap="sm" justify="end">
        <Button onClick={onCancel} disabled={submitting}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={submitting}>
          {submitting
            ? isUpdate ? "Updating..." : "Submitting..."
            : isUpdate ? "Update opportunity" : "Submit to AWS"}
        </Button>
      </Flex>
    </Flex>
  );
};
