import React from "react";
import { Tag, Link, Text, Flex } from "@hubspot/ui-extensions";

// Maps the AWS-side ReviewStatus + HubSpot dealstage to a compact card badge.
// The status text comes from govwin_aws_cosell_status (written back by
// handle_ace_event) and dealstage (advanced by the same Lambda); both are
// kept in sync so the badge has a single source of truth.

export type CardStatus =
  | "not_submitted"
  | "queued"
  | "submitted"
  | "in_review"
  | "approved"
  | "action_required"
  | "closed_lost"
  | "launched"
  | "unknown";

const VARIANT: Record<CardStatus, "default" | "warning" | "success" | "error"> = {
  not_submitted: "default",
  queued: "default",
  submitted: "warning",
  in_review: "warning",
  approved: "success",
  action_required: "error",
  closed_lost: "default",
  launched: "success",
  unknown: "default",
};

const LABEL: Record<CardStatus, string> = {
  not_submitted: "Not submitted",
  queued: "Queued",
  submitted: "Submitted to AWS",
  in_review: "Under AWS review",
  approved: "Approved by AWS",
  action_required: "Action required",
  closed_lost: "Closed lost",
  launched: "Launched",
  unknown: "Status unknown",
};

/** Classify a deal's AWS-side state into one of the card statuses. */
export function classifyStatus(args: {
  awsCosellStatus: string | undefined | null;
  awsCosellId: string | undefined | null;
  dealstage: string | undefined | null;
  triggerStageId: string;
}): CardStatus {
  const status = (args.awsCosellStatus ?? "").trim();
  const stage = (args.dealstage ?? "").trim();
  if (!args.awsCosellId && stage !== args.triggerStageId) return "not_submitted";
  if (!args.awsCosellId && stage === args.triggerStageId) return "queued";
  switch (status) {
    case "Pending Submission":
    case "Submitted":
      return "submitted";
    case "In review":
      return "in_review";
    case "Approved":
      return "approved";
    case "Action Required":
      return "action_required";
    case "Rejected":
    case "Closed Lost":
    case "Expired":
      return "closed_lost";
    case "Launched":
      return "launched";
    default:
      return args.awsCosellId ? "unknown" : "not_submitted";
  }
}

/** Compact badge with the current AWS-side state and the AWS opportunity id when present. */
export const StatusBadge: React.FC<{
  status: CardStatus;
  awsCosellId?: string | null;
}> = ({ status, awsCosellId }) => (
  <Flex direction="row" gap="sm" align="center">
    <Tag variant={VARIANT[status]}>{LABEL[status]}</Tag>
    {awsCosellId ? (
      <Text variant="microcopy">
        <Link
          href={`https://partnercentral.awspartner.com/partnercentral2/s/opportunity/${awsCosellId}`}
          external
        >
          {awsCosellId}
        </Link>
      </Text>
    ) : null}
  </Flex>
);
