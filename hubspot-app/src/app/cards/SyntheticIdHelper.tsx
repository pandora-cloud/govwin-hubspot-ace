// Build a synthetic govwin_opp_id for deals that did not come from GovWin.
//
// Format: <SOURCE>-<CUSTOMER_SLUG>[-<PROJECT_SLUG>]-<NNN>
// Examples:
//   DEMO-SLD45-WX-001
//   DIRECT-NREL-001
//   AWS-VA-OIT-ZTA-002
//
// The helper searches HubSpot for existing govwin_opp_id values matching
// the assembled prefix and suggests the next available NNN. BD can accept
// or override the suggestion. The backend Lambda performs a final DDB
// uniqueness check at submit time to close the race window.

import React, { useRef, useState } from "react";
import {
  Box,
  Flex,
  Input,
  Select,
  Text,
} from "@hubspot/ui-extensions";

import { SOURCE_PREFIXES } from "./enums";

// Default customer-slug derivation. Drops common corporate / agency
// prefixes/suffixes and replaces whitespace with dashes.
const STRIP_TOKENS = new Set([
  "USSF",
  "USAF",
  "USA",
  "INC",
  "LLC",
  "CORP",
  "LTD",
  "COMPANY",
  "CO",
  "AGENCY",
  "DEPARTMENT",
  "OFFICE",
  "OF",
]);

export function deriveCustomerSlug(companyName: string): string {
  if (!companyName) return "";
  const tokens = companyName
    .toUpperCase()
    .replace(/[^A-Z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter((tok) => tok && !STRIP_TOKENS.has(tok));
  return tokens.join("-").slice(0, 20);
}

export interface SyntheticIdValue {
  source: string;
  customer: string;
  project: string;
  sequence: string;
}

export function composeId(parts: SyntheticIdValue): string {
  const segments = [parts.source, parts.customer];
  if (parts.project) segments.push(parts.project);
  segments.push(parts.sequence);
  return segments.filter(Boolean).join("-");
}

interface Props {
  defaultCompanyName: string;
  onChange: (id: string, parts: SyntheticIdValue) => void;
}

/** SyntheticIdHelper: emits a unique synthetic govwin_opp_id via onChange.
 *
 * Avoids the infinite-render loop that the obvious useEffect-on-state pattern
 * produces in HubSpot's UI Extension iframe: the parent renders a fresh inline
 * onChange function on every render, so any effect that depends on the
 * callback identity ends up re-firing forever (effect -> onChange -> parent
 * setState -> new function reference -> effect re-runs). Instead, stash the
 * latest onChange in a ref and call it directly from each setter. The ref
 * doesn't participate in the dependency graph and the setters are the only
 * code paths that need to notify the parent.
 */
export const SyntheticIdHelper: React.FC<Props> = ({ defaultCompanyName, onChange }) => {
  const [source, setSource] = useState<string>("DIRECT");
  const [customer, setCustomer] = useState<string>(deriveCustomerSlug(defaultCompanyName));
  const [project, setProject] = useState<string>("");
  const [sequence, setSequence] = useState<string>("001");

  // Track onChange via a ref so we never rebuild effects when the parent
  // creates a fresh inline function.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // No useEffects of any kind here. Each setter calls onChange directly
  // and synchronously, so React only re-renders the parent on actual user
  // input. No mount-time emit, no auto-suggest network call. The form's
  // validate() reads state.syntheticParts at submit time; if the user
  // never touched the synthetic builder, syntheticParts stays null and
  // validate falls through to its "either a real GovWin ID or a synthetic
  // builder must be filled" check.
  const updateSource = (v: string) => {
    setSource(v);
    onChangeRef.current(
      composeId({ source: v, customer, project, sequence }),
      { source: v, customer, project, sequence }
    );
  };
  const updateCustomer = (v: string) => {
    setCustomer(v);
    onChangeRef.current(
      composeId({ source, customer: v, project, sequence }),
      { source, customer: v, project, sequence }
    );
  };
  const updateProject = (v: string) => {
    setProject(v);
    onChangeRef.current(
      composeId({ source, customer, project: v, sequence }),
      { source, customer, project: v, sequence }
    );
  };
  const updateSequence = (v: string) => {
    setSequence(v);
    onChangeRef.current(
      composeId({ source, customer, project, sequence: v }),
      { source, customer, project, sequence: v }
    );
  };

  return (
    <Flex direction="column" gap="sm">
      <Text format={{ fontWeight: "bold" }}>Synthetic GovWin ID</Text>
      <Text variant="microcopy">
        For deals that did not come from GovWin IQ. Composed as
        SOURCE-CUSTOMER[-PROJECT]-NNN. The sequence below is the next
        available number for this prefix; you can override it.
      </Text>
      <Flex direction="row" gap="sm">
        <Box flex={1}>
          <Select
            name="source_prefix"
            label="Source"
            value={source}
            onChange={(v) => updateSource(String(v ?? "DIRECT"))}
            options={SOURCE_PREFIXES.filter((p) => p.value !== "GW").map((p) => ({
              label: p.label,
              value: p.value,
            }))}
          />
        </Box>
        <Box flex={2}>
          <Input
            name="customer_slug"
            label="Customer slug"
            value={customer}
            onChange={(v) => updateCustomer(String(v ?? "").toUpperCase().replace(/\s+/g, "-"))}
          />
        </Box>
      </Flex>
      <Flex direction="row" gap="sm">
        <Box flex={2}>
          <Input
            name="project_slug"
            label="Project slug (optional)"
            value={project}
            onChange={(v) => updateProject(String(v ?? "").toUpperCase().replace(/\s+/g, "-"))}
          />
        </Box>
        <Box flex={1}>
          <Input
            name="sequence_number"
            label="Sequence (3 digits)"
            description="Bump up if you've used this prefix before."
            value={sequence}
            onChange={(v) => updateSequence(String(v ?? "").padStart(3, "0"))}
          />
        </Box>
      </Flex>
      <Flex direction="row" gap="sm" align="center">
        <Text variant="microcopy">Final ID:</Text>
        <Text variant="microcopy" format={{ fontWeight: "bold" }}>
          {composeId({ source, customer, project, sequence })}
        </Text>
      </Flex>
    </Flex>
  );
};
