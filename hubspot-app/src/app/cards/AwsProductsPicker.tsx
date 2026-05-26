// AwsProductsPicker: multi-select dropdown of AWS Products consumed by the
// opportunity. Calls the backend GET /ui-extension/aws-products endpoint
// which serves the bundled resources/aws_products.json catalog (513
// canonical entries plus a local Other escape hatch).
//
// Enforces the AWS-published cap of 20 products per opportunity client-side
// so BD gets immediate feedback rather than a 400 from the server. The
// backend re-checks at submit time.

import React, { useEffect, useState } from "react";
import {
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

  // MultiSelect has its own typeahead search built in; rely on it instead
  // of a separate filter Input (Input.onChange fires only on blur which
  // makes a custom filter feel broken on every keystroke).
  const options = (products ?? []).map((p) => ({
    label: p.Family && p.Family !== "Other" ? `${p.Name} (${p.Family})` : p.Name,
    value: p.Identifier,
  }));

  const overLimit = value.length > MAX_AWS_PRODUCTS_PER_OPPORTUNITY;

  return (
    <Flex direction="column" gap="sm">
      <MultiSelect
        name="ace_aws_products"
        label={`AWS Products consumed (${value.length} / ${MAX_AWS_PRODUCTS_PER_OPPORTUNITY})`}
        description="Type to search 513 AWS products. Each pick adds an AssociateOpportunity call after CreateOpportunity (AWS limit: 20 per opportunity)."
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
