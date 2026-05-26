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

// Wire the card up as a HubSpot CRM extension. Per HubSpot's UI Extensions
// docs, the file must call hubspot.extend(...) instead of exporting a
// default component.
hubspot.extend(({ context, runServerlessFunction, actions }) => (
  <SubmitToAwsCard context={context} actions={actions} />
));

// Trigger stage id the backend listens for to fire submit_to_ace. Mirrors
// the ACE_TRIGGER_STAGES env var on the Lambda. Hardcoded here because the
// UI Extension can't read Lambda env vars; if this ever drifts, the card
// will still render the right state from govwin_aws_cosell_status.
const TRIGGER_STAGE_ID = "3590200042";

// HubSpot deal properties the card reads for its status display.
const READ_PROPERTIES: string[] = [
  "dealstage",
  "govwin_aws_cosell_id",
  "govwin_aws_cosell_status",
  "govwin_ace_next_steps",
  "govwin_opp_id",
  "dealname",
];

interface DealSnapshot {
  dealId: string;
  dealName: string | null;
  dealstage: string | null;
  awsCosellId: string | null;
  awsCosellStatus: string | null;
  govwinOppId: string | null;
  aceNextSteps: string | null;
}

const SubmitToAwsCard: React.FC<{ context: any; actions: any }> = ({
  context,
  actions,
}) => {
  const [snapshot, setSnapshot] = useState<DealSnapshot | null>(null);
  const [status, setStatus] = useState<CardStatus>("not_submitted");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
        const response = await hubspot.serverless("get-deal-properties", {
          propertiesToSend: READ_PROPERTIES,
        });
        if (cancelled) return;
        const props = (response?.properties as Record<string, any>) ?? {};
        const snap: DealSnapshot = {
          dealId,
          dealName: props.dealname ?? null,
          dealstage: props.dealstage ?? null,
          awsCosellId: props.govwin_aws_cosell_id ?? null,
          awsCosellStatus: props.govwin_aws_cosell_status ?? null,
          govwinOppId: props.govwin_opp_id ?? null,
          aceNextSteps: props.govwin_ace_next_steps ?? null,
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
  }, [context]);

  const openSubmitForm = () => {
    // SubmitForm is wired in the next iteration. For now we wire the click
    // through to a placeholder so the card structure is reviewable.
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

  return (
    <Flex direction="column" gap="md">
      <Flex direction="row" gap="md" align="center" justify="between">
        <Text format={{ fontWeight: "bold" }}>AWS Partner Central</Text>
        <StatusBadge status={status} awsCosellId={snapshot?.awsCosellId} />
      </Flex>

      {status === "action_required" && snapshot?.aceNextSteps ? (
        <Text variant="microcopy">
          <strong>AWS Next Steps: </strong>
          {snapshot.aceNextSteps}
        </Text>
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
