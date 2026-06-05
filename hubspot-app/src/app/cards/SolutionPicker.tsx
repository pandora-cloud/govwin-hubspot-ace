// SolutionPicker: single-select dropdown of the partner's registered AWS
// Solutions. Calls the backend GET /ui-extension/solutions endpoint which
// proxies ListSolutions. Hidden when the catalog has zero Active solutions
// (e.g. Sandbox); the form caller passes an isCatalogSandbox flag.

import React, { useEffect, useState } from "react";
import { LoadingSpinner, Select, Text, hubspot } from "@hubspot/ui-extensions";

interface SolutionSummary {
  Id: string;
  Name: string;
  Category: string;
  Status: string;
}

interface Props {
  apiBaseUrl: string;
  catalog: string;
  value: string;
  onChange: (solutionId: string) => void;
  required: boolean;
}

export const SolutionPicker: React.FC<Props> = ({
  apiBaseUrl,
  catalog,
  value,
  onChange,
  required,
}) => {
  const [solutions, setSolutions] = useState<SolutionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await hubspot.fetch(
          `${apiBaseUrl}/ui-extension/solutions?catalog=${encodeURIComponent(catalog)}`,
          { method: "GET" }
        );
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        if (!cancelled) setSolutions(data.solutions ?? []);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, catalog]);

  if (solutions === null && !error) {
    return <LoadingSpinner label="Loading registered solutions..." />;
  }

  if (error) {
    return (
      <Text variant="microcopy" format={{ color: "error" }}>
        Could not load Solutions: {error}
      </Text>
    );
  }

  if ((solutions ?? []).length === 0) {
    // Sandbox catalog or no Active solutions registered. The mapper falls
    // back to OtherSolutionDescription, so just hide the picker.
    return (
      <Text variant="microcopy">
        No registered AWS Solutions in this catalog ({catalog}). The
        submission will use the deal title as the solution description.
      </Text>
    );
  }

  const options = (solutions ?? []).map((s) => ({
    label: `${s.Name} (${s.Category})`,
    value: s.Id,
  }));

  return (
    <Select
      name="ace_solution_id"
      label={`AWS Solution${required ? " (required)" : ""}`}
      description="The AWS Solution that reviewers will see attached to this opportunity."
      value={value}
      onChange={(v) => onChange(String(v ?? ""))}
      options={options}
      required={required}
    />
  );
};
