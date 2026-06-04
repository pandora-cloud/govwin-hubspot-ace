# 10. ARM64 (Graviton2) Lambda runtime

Date: 2026-06-04

## Status

Accepted

## Context

AWS Lambda supports two CPU architectures: `x86_64` (Intel) and `arm64` (Graviton2). The functional capabilities are identical; the differences are pricing, performance characteristics, and the availability of native dependencies.

- **Price**: `arm64` Lambda invocations are billed at roughly 20% lower per millisecond than `x86_64` invocations as published by AWS at the time of this decision. The discount applies to both compute duration and provisioned concurrency.
- **Performance**: Graviton2 typically matches or beats Intel for I/O-bound Python workloads, which is exactly what every Lambda in this integration is. Benchmarks for boto3-heavy workloads consistently favor Graviton2.
- **Dependencies**: the Lambda layer must be built for the target architecture. Python pure-Python packages are architecture-neutral; native wheels (`*.whl`) for packages with C extensions must be the `manylinux2014_aarch64` variant when targeting `arm64`. The project's dependencies (boto3, pydantic, httpx, tenacity, python-dateutil) all publish `aarch64` wheels.

## Decision

All Lambdas in the integration run on `arm64`. The Lambda layer is built with:

```
pip install \
  --platform manylinux2014_aarch64 \
  --only-binary=:all: \
  --implementation cp \
  --python-version 3.12 \
  -r requirements.txt \
  -t package/python/
```

The build runs identically on `x86_64` developer machines (Intel Macs, Linux desktops) and on `arm64` developer machines (Apple Silicon Macs); pip resolves the cross-architecture wheels by virtue of the `--platform` flag.

The Terraform module sets `architectures = ["arm64"]` on every Lambda resource. The same setting is used for the LocalStack-mocked Lambdas in the integration test suite, even though LocalStack does not actually run them; the consistency avoids surprises when a Lambda is touched by both paths.

## Consequences

Positive:

- The Lambda line item in the AWS bill is approximately 20% lower than the equivalent `x86_64` deployment. For a small-tier deployment this rounds to pennies per month; for a large-tier deployment it is meaningfully more.
- Cold start times for Python on Graviton2 are comparable to or slightly faster than `x86_64` for this workload; not a regression.
- Graviton2 is the direction AWS is investing in; new Lambda features (SnapStart for Python, when it ships, plus future runtime improvements) land first on `arm64`.

Negative:

- Any future contributor who adds a Python dependency must verify that an `aarch64` wheel exists for it before merging. The `make package` step will fail loudly with a `Could not find a version` error if it cannot resolve a wheel, but the error is at build time, not at code-review time.
- A developer running `pip install -r requirements.txt` locally on an Intel machine will get `x86_64` wheels into their virtual environment, not `aarch64`. This is intentional: local development and testing run on the developer's native architecture, only the deployed Lambda layer is cross-built. The two paths are documented in `docs/architecture.md`.

Operational:

- If a future dependency lacks an `aarch64` wheel and the project still wants it, the options are: build the wheel from source in the layer (slower build, larger image), find an alternative package that has the wheel, or fall back to `x86_64` for that one Lambda. Each option has tradeoffs and would warrant its own ADR.
- The 20% saving is a published AWS pricing differential at the time of this decision. Pricing can change; the decision rests primarily on the parity of capability and the AWS investment direction, not on the specific discount.
