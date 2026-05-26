// The Submit-to-AWS CRM card. Renders on every deal record (location =
// crm.record.tab). Shows the current AWS-side state at a glance plus a
// Submit button that opens the modal form (SubmitForm.tsx).
//
// The card reads HubSpot deal properties via the CrmActionButton SDK and
// hubspot.fetch to call the backend Lambda (src/lambdas/submit_form_to_ace.py)
// for the GET /ui-extension/solutions and GET /ui-extension/aws-products
// catalogs. Submission goes through POST /ui-extension/submit which PATCHes
// the deal and flips dealstage; from there the existing webhook ->
// submit_to_ace pipeline runs unchanged.

import React, { useEffect, useState } from "react";
import {
  Button,
  Divider,
  EmptyState,
  Flex,
  LoadingSpinner,
  Text,
  hubspot,
} from "@hubspot/ui-extensions";

import { CardStatus, StatusBadge, classifyStatus } from "./StatusBadge";
import { SubmitForm } from "./SubmitForm";

// API Gateway base URL for the UI-extension callback endpoints. The
// HubSpot ui-extensions runtime restricts hubspot.fetch to URLs listed
// in app-hsmeta.json -> permittedUrls.fetch, so the value here must
// match that allowlist.
const API_BASE_URL = "https://np1hq84j21.execute-api.us-east-1.amazonaws.com";

// Sandbox vs AWS catalog. Mirrors the ACE_CATALOG env var on the backend.
// When in Sandbox, the SolutionPicker hides itself and the mapper falls
// back to OtherSolutionDescription. Hardcoded here because the UI
// Extension can't read Lambda env vars.
const ACE_CATALOG = "Sandbox";

// Wire the card up as a HubSpot CRM extension. Per HubSpot's UI Extensions
// docs, the file must call hubspot.extend(...) instead of exporting a
// default component.
hubspot.extend<"crm.record.tab">(({ context, actions }) => (
  <SubmitToAwsCard
    context={context}
    actions={actions}
    fetchCrmObjectProperties={actions.fetchCrmObjectProperties}
  />
));

// Trigger stage id the backend listens for to fire submit_to_ace. Mirrors
// the ACE_TRIGGER_STAGES env var on the Lambda. Hardcoded here because the
// UI Extension can't read Lambda env vars; if this ever drifts, the card
// will still render the right state from govwin_aws_cosell_status.
const TRIGGER_STAGE_ID = "3590200042";

// HubSpot deal properties the card reads for its status display + form pre-fill.
const READ_PROPERTIES: string[] = [
  "dealstage",
  "govwin_aws_cosell_id",
  "govwin_aws_cosell_status",
  "govwin_ace_next_steps",
  "govwin_opp_id",
  "govwin_agency",
  "govwin_industry",
  "dealname",
  "amount",
  "closedate",
  "description",
];

interface DealSnapshot {
  dealId: string;
  dealName: string | null;
  dealstage: string | null;
  awsCosellId: string | null;
  awsCosellStatus: string | null;
  govwinOppId: string | null;
  aceNextSteps: string | null;
  companyName: string | null;
  industry: string | null;
  amount: number | null;
  closeDate: string | null;
  description: string | null;
}

interface CardProps {
  context: any;
  actions: any;
  fetchCrmObjectProperties: (props: string[]) => Promise<Record<string, any>>;
}

const SubmitToAwsCard: React.FC<CardProps> = ({
  context,
  actions,
  fetchCrmObjectProperties,
}) => {
  const [snapshot, setSnapshot] = useState<DealSnapshot | null>(null);
  const [status, setStatus] = useState<CardStatus>("not_submitted");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Load deal properties on mount and after a submission. The card polls
  // every 30 seconds once a submission is in flight (status = queued or
  // submitted) so BD can watch AWS-side state advance without reloading.
  useEffect(() => {
    const dealId = String(context?.crm?.objectId ?? "");
    if (!dealId) {
      setError("Card opened outside a deal record context.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    const fetchSnapshot = async () => {
      try {
        // Use HubSpot's built-in CRM SDK helper (signature:
        // fetchCrmObjectProperties(properties: string[] | '*') =>
        // Promise<Record<string, string>>; from @hubspot/ui-extensions
        // 0.14.0 shared/types/actions.d.ts). Passing '*' fetches every
        // property on the deal, which avoids "property does not exist"
        // failures if a property name has drifted. Filter what we care
        // about client-side.
        const props = (await fetchCrmObjectProperties("*")) ?? {};
        if (cancelled) return;
        const snap: DealSnapshot = {
          dealId,
          dealName: props.dealname ?? null,
          dealstage: props.dealstage ?? null,
          awsCosellId: props.govwin_aws_cosell_id ?? null,
          awsCosellStatus: props.govwin_aws_cosell_status ?? null,
          govwinOppId: props.govwin_opp_id ?? null,
          aceNextSteps: props.govwin_ace_next_steps ?? null,
          companyName: props.govwin_agency ?? null,
          industry: props.govwin_industry ?? null,
          amount: props.amount != null ? Number(props.amount) : null,
          // HubSpot returns closedate as an epoch numeric string. The unit
          // varies (ms vs seconds) depending on the iframe transport, so
          // sniff by magnitude: anything < 10^12 (i.e. <= 13 digits) we
          // treat as seconds and multiply, otherwise as milliseconds.
          // Slicing string was the original bug (took the first 10 chars
          // of "1780261104000" and got "1780261104" back). Converts to
          // YYYY-MM-DD for the date input; returns null on garbage so the
          // form's default-of-today+180 fallback kicks in.
          closeDate: (() => {
            const raw = props.closedate;
            if (!raw) return null;
            const n = Number(raw);
            if (!Number.isFinite(n) || n <= 0) return null;
            const ms = n < 1e12 ? n * 1000 : n;
            try {
              return new Date(ms).toISOString().slice(0, 10);
            } catch {
              return null;
            }
          })(),
          description: props.description ?? null,
        };
        setSnapshot(snap);
        setStatus(
          classifyStatus({
            awsCosellStatus: snap.awsCosellStatus,
            awsCosellId: snap.awsCosellId,
            dealstage: snap.dealstage,
            triggerStageId: TRIGGER_STAGE_ID,
          })
        );
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchSnapshot();
    return () => {
      cancelled = true;
    };
  }, [context, reloadKey]);

  // Poll for AWS-side state changes once a submission is in flight. The
  // handle_ace_event Lambda writes govwin_aws_cosell_status back to the
  // deal whenever AWS emits an EventBridge state change, so polling the
  // deal properties is enough to see In review -> Approved transitions.
  useEffect(() => {
    if (status !== "queued" && status !== "submitted" && status !== "in_review") {
      return;
    }
    const tick = setInterval(() => setReloadKey((k) => k + 1), 30_000);
    return () => clearInterval(tick);
  }, [status]);

  const openSubmitForm = () => setFormOpen(true);
  const closeSubmitForm = () => setFormOpen(false);
  const onSubmissionQueued = () => {
    setFormOpen(false);
    setReloadKey((k) => k + 1);
    actions?.refreshObjectProperties?.();
  };

  if (loading) return <LoadingSpinner label="Loading AWS submission state..." />;

  if (error) {
    return (
      <EmptyState title="Could not load AWS state" layout="vertical" imageName="error">
        <Text>{error}</Text>
      </EmptyState>
    );
  }

  if (formOpen && snapshot) {
    return (
      <SubmitForm
        apiBaseUrl={API_BASE_URL}
        catalog={ACE_CATALOG}
        dealId={snapshot.dealId}
        defaultDealName={snapshot.dealName ?? ""}
        defaultCompanyName={snapshot.companyName ?? ""}
        defaultIndustry={snapshot.industry}
        defaultAmount={snapshot.amount}
        defaultCloseDate={snapshot.closeDate}
        defaultDescription={snapshot.description}
        existingGovwinOppId={snapshot.govwinOppId}
        onSubmissionQueued={onSubmissionQueued}
        onCancel={closeSubmitForm}
      />
    );
  }

  return (
    <Flex direction="column" gap="md">
      <Flex direction="row" gap="md" align="center" justify="between">
        <Text format={{ fontWeight: "bold" }}>AWS Partner Central</Text>
        <StatusBadge status={status} awsCosellId={snapshot?.awsCosellId} />
      </Flex>

      {status === "action_required" && snapshot?.aceNextSteps ? (
        <Flex direction="column" gap="xs">
          <Text variant="microcopy" format={{ fontWeight: "bold" }}>
            AWS Next Steps:
          </Text>
          <Text variant="microcopy">{snapshot.aceNextSteps}</Text>
        </Flex>
      ) : null}

      <Divider />

      {status === "not_submitted" ? (
        <Button variant="primary" onClick={openSubmitForm}>
          Submit to AWS
        </Button>
      ) : (
        <Text variant="microcopy">
          {snapshot?.awsCosellId
            ? `Opportunity ${snapshot.awsCosellId} created in AWS Partner Central.`
            : "Submission in flight. The card will update when AWS responds."}
        </Text>
      )}

    </Flex>
  );
};
