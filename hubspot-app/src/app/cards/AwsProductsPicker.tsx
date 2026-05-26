// AwsProductsPicker: multi-select dropdown of AWS Products consumed by the
// opportunity. Calls the backend GET /ui-extension/aws-products endpoint
// which serves the bundled resources/aws_products.json catalog (513
// canonical entries plus a local Other escape hatch).
//
// Enforces the AWS-published cap of 20 products per opportunity client-side
// so BD gets immediate feedback rather than a 400 from the server. The
// backend re-checks at submit time.

import React, { useEffect, useMemo, useState } from "react";
import {
  Input,
  LoadingSpinner,
  MultiSelect,
  Text,
  Flex,
  hubspot,
} from "@hubspot/ui-extensions";

import { MAX_AWS_PRODUCTS_PER_OPPORTUNITY } from "./enums";

interface AwsProductSummary {
  Identifier: string;
  Name: string;
  Family: string;
  Description?: string;
}

interface Props {
  apiBaseUrl: string;
  value: string[];
  onChange: (identifiers: string[]) => void;
}

export const AwsProductsPicker: React.FC<Props> = ({ apiBaseUrl, value, onChange }) => {
  const [products, setProducts] = useState<AwsProductSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await hubspot.fetch(`${apiBaseUrl}/ui-extension/aws-products`, {
          method: "GET",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!cancelled) setProducts(data.products ?? []);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl]);

  const filtered = useMemo(() => {
    if (!products) return [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return products;
    return products.filter(
      (p) =>
        p.Identifier.toLowerCase().includes(needle) ||
        p.Name.toLowerCase().includes(needle) ||
        p.Family.toLowerCase().includes(needle)
    );
  }, [products, filter]);

  if (products === null && !error) {
    return <LoadingSpinner label="Loading AWS products catalog..." />;
  }
  if (error) {
    return (
      <Text variant="microcopy" format={{ color: "error" }}>
        Could not load AWS Products: {error}
      </Text>
    );
  }

  const options = filtered.map((p) => ({
    label: p.Family && p.Family !== "Other" ? `${p.Name} (${p.Family})` : p.Name,
    value: p.Identifier,
  }));

  const overLimit = value.length > MAX_AWS_PRODUCTS_PER_OPPORTUNITY;

  return (
    <Flex direction="column" gap="sm">
      <Input
        name="aws_products_filter"
        label="Filter products"
        description="Type to filter by name, identifier, or family."
        value={filter}
        onChange={(v) => setFilter(String(v ?? ""))}
      />
      <MultiSelect
        name="ace_aws_products"
        label={`AWS Products consumed (${value.length} / ${MAX_AWS_PRODUCTS_PER_OPPORTUNITY})`}
        description="Each selected product creates an AssociateOpportunity call after CreateOpportunity. AWS limit is 20 per opportunity."
        value={value}
        onChange={(v) => {
          const next = (v ?? []) as string[];
          // Cap client-side; user feedback is immediate.
          if (next.length > MAX_AWS_PRODUCTS_PER_OPPORTUNITY) {
            onChange(next.slice(0, MAX_AWS_PRODUCTS_PER_OPPORTUNITY));
          } else {
            onChange(next);
          }
        }}
        options={options}
      />
      {overLimit ? (
        <Text variant="microcopy" format={{ color: "error" }}>
          You exceeded the AWS limit of {MAX_AWS_PRODUCTS_PER_OPPORTUNITY} products
          per opportunity. Extra selections will be dropped.
        </Text>
      ) : null}
    </Flex>
  );
};
