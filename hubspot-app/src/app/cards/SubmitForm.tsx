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
import { SyntheticIdHelper, SyntheticIdValue } from "./SyntheticIdHelper";
import {
  COMPETITORS,
  DELIVERY_MODELS,
  MARKETING_CHANNELS,
  MARKETING_SOURCES,
  OPPORTUNITY_TYPES,
  PARTNER_NEED_OPTIONS,
  SALES_ACTIVITIES,
  USE_CASES,
} from "./enums";
import {
  COMPETITORS,
  DELIVERY_MODELS,
  MARKETING_CHANNELS,
  MARKETING_SOURCES,
  OPPORTUNITY_TYPES,
  PARTNER_NEED_OPTIONS,
  SALES_ACTIVITIES,
  USE_CASES,
} from "./enums";

interface Props {
  apiBaseUrl: string;
  catalog: string;
  dealId: string;
  defaultDealName: string;
  defaultCompanyName: string;
  defaultIndustry: string | null;
  defaultAmount: number | null;
  defaultCloseDate: string | null;
  defaultDescription: string | null;
  existingGovwinOppId: string | null;
  onSubmissionQueued: (response: { ace_opportunity_id?: string | null }) => void;
  onCancel: () => void;
}

interface FormState {
  fromGovWin: boolean;
  govwinOppId: string;
  syntheticParts: SyntheticIdValue | null;
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
  defaultDealName,
  defaultCompanyName,
  defaultIndustry,
  defaultAmount,
  defaultCloseDate,
  defaultDescription,
  existingGovwinOppId,
  onSubmissionQueued,
  onCancel,
}) => {
  const [state, setState] = useState<FormState>({
    fromGovWin: Boolean(existingGovwinOppId),
    govwinOppId: existingGovwinOppId ?? "",
    syntheticParts: null,
    partnerNeed: ["Deal Support"],
    deliveryModel: [],
    useCase: "",
    opportunityType: "Net New Business",
    salesActivities: ["Initialized discussions with customer"],
    competitor: "",
    otherCompetitorNames: "",
    industry: defaultIndustry ?? "Government",
    awsAccountId: "",
    nationalSecurity: "",
    awsAccountUnknown: false,
    dealName: defaultDealName ?? "",
    description: defaultDescription ?? "",
    amount: defaultAmount != null ? String(defaultAmount) : "",
    closeDate: defaultCloseDate ?? TODAY_PLUS_180(),
    solutionId: "",
    awsProducts: [],
    marketingEnabled: false,
    marketingSource: "None",
    marketingChannels: [],
    marketingCampaign: "",
    marketingFundingUsed: "",
    additionalComments: "",
    nextSteps: "",
  });
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setState((s) => ({ ...s, [key]: value }));
  };

  const validate = (): FieldError[] => {
    const errs: FieldError[] = [];
    // Either the real GovWin ID or the synthetic builder must produce a
    // value. We don't enforce one-or-the-other UI-wise; the user fills
    // whichever fits.
    const hasGovwin = Boolean(state.govwinOppId && state.govwinOppId.trim());
    const hasSynthetic = Boolean(
      state.syntheticParts && state.syntheticParts.customer
    );
    if (!hasGovwin && !hasSynthetic) {
      errs.push({
        field: "govwin_opp_id",
        message: "Enter a GovWin Opportunity ID or fill in the synthetic builder below.",
      });
    }
    if (state.partnerNeed.length === 0) {
      errs.push({ field: "partner_need", message: "Pick at least one Partner Need." });
    }
    if (state.deliveryModel.length === 0) {
      errs.push({ field: "delivery_model", message: "Pick at least one Delivery Model." });
    }
    if (!state.useCase) {
      errs.push({ field: "use_case", message: "Pick a Customer Use Case." });
    }
    if (!state.awsAccountUnknown && state.awsAccountId && !/^\d{12}$/.test(state.awsAccountId)) {
      errs.push({ field: "aws_account_id", message: "Must be exactly 12 digits." });
    }
    if (!state.awsAccountUnknown && !state.awsAccountId) {
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
    // Real GovWin ID wins if the user filled it. Otherwise compose from
    // the synthetic builder. validate() already ensured at least one is set.
    const govwin_opp_id = state.govwinOppId.trim()
      ? state.govwinOppId.trim()
      : state.syntheticParts
        ? [
            state.syntheticParts.source,
            state.syntheticParts.customer,
            state.syntheticParts.project,
            state.syntheticParts.sequence,
          ]
            .filter(Boolean)
            .join("-")
        : "";

    const payload: Record<string, unknown> = {
      deal_id: dealId,
      govwin_opp_id,
      govwin_agency: defaultCompanyName,
      govwin_industry: state.industry,
      dealname: state.dealName,
      description: state.description,
      amount: state.amount ? parseFloat(state.amount) : null,
      closedate: state.closeDate,
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
      ace_next_steps: state.nextSteps || null,
    };
    if (state.marketingEnabled) {
      payload.marketing = {
        Source: state.marketingSource,
        Channels: state.marketingChannels,
        CampaignName: state.marketingCampaign || null,
        AwsFundingUsed: state.marketingFundingUsed || null,
      };
    }

    try {
      const response = await hubspot.fetch(`${apiBaseUrl}/ui-extension/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (response.status === 202) {
        onSubmissionQueued({ ace_opportunity_id: body.ace_opportunity_id ?? null });
        return;
      }
      if (response.status === 409) {
        setSubmitError(
          `This deal already has an opportunity in AWS Partner Central (${body.ace_opportunity_id}). Refresh the card to see its current state.`
        );
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
      <Heading>Submit deal to AWS Partner Central</Heading>

      {submitError ? (
        <Alert title="Submission error" variant="error">{submitError}</Alert>
      ) : null}

      <Divider />

      {/* Section 1: Source. Fill EITHER the GovWin ID input (if the deal
          came from GovWin IQ) OR the synthetic builder below. The submit
          handler picks whichever has a value; the GovWin input wins when
          both are filled. */}
      <Input
        name="govwin_opp_id"
        label="GovWin Opportunity ID (if this deal came from GovWin)"
        description="Paste the real ID from GovWin IQ (e.g. OPP123456). Leave blank to build a synthetic ID below."
        value={state.govwinOppId}
        onChange={(v) => update("govwinOppId", String(v ?? ""))}
        error={Boolean(errorFor("govwin_opp_id"))}
        validationMessage={errorFor("govwin_opp_id")}
        readOnly={Boolean(existingGovwinOppId)}
      />
      <Text variant="microcopy">
        Or, if this deal didn't come from GovWin, build a synthetic ID:
      </Text>
      <SyntheticIdHelper
        defaultCompanyName={defaultCompanyName}
        onChange={(_id, parts) => update("syntheticParts", parts)}
      />

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

      <Flex direction="row" gap="sm" justify="end">
        <Button onClick={onCancel} disabled={submitting}>Cancel</Button>
        <Button variant="primary" onClick={submit} disabled={submitting}>
          {submitting ? "Submitting..." : "Submit to AWS"}
        </Button>
      </Flex>
    </Flex>
  );
};
