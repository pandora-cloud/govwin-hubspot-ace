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

import React, { useEffect, useState, useCallback } from "react";
import {
  Box,
  Flex,
  Input,
  Select,
  Text,
  hubspot,
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

/** Search HubSpot for deals whose govwin_opp_id starts with `prefix`. Returns the matched ids. */
async function searchExistingIds(prefix: string): Promise<string[]> {
  if (!prefix) return [];
  const response = await hubspot.fetch(
    "https://api.hubapi.com/crm/v3/objects/deals/search",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filterGroups: [
          {
            filters: [
              {
                propertyName: "govwin_opp_id",
                operator: "CONTAINS_TOKEN",
                value: `${prefix}*`,
              },
            ],
          },
        ],
        properties: ["govwin_opp_id"],
        limit: 100,
      }),
    }
  );
  if (!response.ok) return [];
  const data = await response.json();
  const ids: string[] = [];
  for (const r of data.results ?? []) {
    const id = r.properties?.govwin_opp_id;
    if (typeof id === "string" && id.startsWith(prefix)) ids.push(id);
  }
  return ids;
}

/** Given an array of ids matching the prefix, suggest the next zero-padded sequence. */
export function nextSequence(prefix: string, existing: string[]): string {
  let maxSeq = 0;
  for (const id of existing) {
    const tail = id.slice(prefix.length);
    const match = tail.match(/^(\d{1,4})$/);
    if (match) {
      const n = parseInt(match[1], 10);
      if (!Number.isNaN(n) && n > maxSeq) maxSeq = n;
    }
  }
  return String(maxSeq + 1).padStart(3, "0");
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

/** SyntheticIdHelper: emits a unique synthetic govwin_opp_id via onChange. */
export const SyntheticIdHelper: React.FC<Props> = ({ defaultCompanyName, onChange }) => {
  const [source, setSource] = useState<string>("DIRECT");
  const [customer, setCustomer] = useState<string>(deriveCustomerSlug(defaultCompanyName));
  const [project, setProject] = useState<string>("");
  const [sequence, setSequence] = useState<string>("001");
  const [suggested, setSuggested] = useState<string>("001");

  const emit = useCallback(
    (parts: SyntheticIdValue) => {
      onChange(composeId(parts), parts);
    },
    [onChange]
  );

  useEffect(() => {
    let cancelled = false;
    const prefix = [source, customer, project].filter(Boolean).join("-") + "-";
    if (!source || !customer) {
      setSuggested("001");
      return;
    }
    searchExistingIds(prefix).then((ids) => {
      if (cancelled) return;
      const next = nextSequence(prefix, ids);
      setSuggested(next);
      setSequence((current) => (current === "001" || current === "" ? next : current));
    });
    return () => {
      cancelled = true;
    };
  }, [source, customer, project]);

  useEffect(() => {
    emit({ source, customer, project, sequence });
  }, [source, customer, project, sequence, emit]);

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
            onChange={(v) => setSource(String(v ?? "DIRECT"))}
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
            onChange={(v) => setCustomer(String(v ?? "").toUpperCase().replace(/\s+/g, "-"))}
          />
        </Box>
      </Flex>
      <Flex direction="row" gap="sm">
        <Box flex={2}>
          <Input
            name="project_slug"
            label="Project slug (optional)"
            value={project}
            onChange={(v) => setProject(String(v ?? "").toUpperCase().replace(/\s+/g, "-"))}
          />
        </Box>
        <Box flex={1}>
          <Input
            name="sequence_number"
            label={`Sequence (suggested: ${suggested})`}
            value={sequence}
            onChange={(v) => setSequence(String(v ?? "").padStart(3, "0"))}
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
