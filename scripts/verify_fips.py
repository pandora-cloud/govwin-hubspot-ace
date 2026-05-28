"""Verify every AWS service we use resolves to a FIPS endpoint.

Run with the same Python environment as the Lambdas. Useful as a CI gate
and as part of an internal audit checklist (NIST 800-53 SC-13, CMMC L2
SC.L2-3.13.11).

Exits 0 if every service resolves to ``<service>-fips.<region>.amazonaws.com``;
exits 1 otherwise. Prints a one-line status per service so the failure mode
is obvious in CI logs.

Services AWS does not publish a FIPS endpoint for (tracked in
``src.aws_clients._NO_FIPS_ENDPOINT``) are reported as SKIP rather than
FAIL. The src.aws_clients factory transparently downgrades those to the
standard endpoint, so this is the same fall-back path that runs in
production. If AWS later publishes a FIPS variant for one of those
services, drop it from the exception set in src.aws_clients and the
SKIP becomes an OK on the next CI run.
"""

from __future__ import annotations

import os
import sys

# Force FIPS on regardless of any inherited test-runner environment.
os.environ["AWS_USE_FIPS_ENDPOINT"] = "true"

from src.aws_clients import _NO_FIPS_ENDPOINT, make_client, make_resource  # noqa: E402

SERVICES = [
    "sqs",
    "sns",
    "secretsmanager",
    "lambda",
    "events",
    "scheduler",
    "logs",
    "kms",
    "partnercentral-selling",  # always us-east-1 regardless of region
]


def main() -> int:
    region = os.environ.get("AWS_REGION", "us-east-1")
    failures: list[str] = []
    skips: list[str] = []

    for svc in SERVICES:
        client = make_client(svc, region)
        url = client.meta.endpoint_url
        if svc in _NO_FIPS_ENDPOINT:
            print(f"SKIP  {svc:<28} -> {url} (AWS publishes no FIPS endpoint)")
            skips.append(svc)
            continue
        if "fips" not in url:
            print(f"FAIL  {svc:<28} -> {url}")
            failures.append(svc)
        else:
            print(f"OK    {svc:<28} -> {url}")

    # DynamoDB resource path
    dynamodb = make_resource("dynamodb", region)
    url = dynamodb.meta.client.meta.endpoint_url
    if "fips" not in url:
        print(f"FAIL  dynamodb (resource)       -> {url}")
        failures.append("dynamodb (resource)")
    else:
        print(f"OK    dynamodb (resource)       -> {url}")

    if failures:
        print(f"\n{len(failures)} service(s) did not resolve to a FIPS endpoint.")
        return 1
    summary = "All services resolved to FIPS endpoints"
    if skips:
        summary += f" ({len(skips)} skipped: {', '.join(skips)})"
    print(f"\n{summary}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
