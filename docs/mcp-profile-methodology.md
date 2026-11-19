# MCP profile methodology

Agent-Hound recognizes configured MCP implementations and records security-relevant
operations established by official source code, exact tool schemas or specific
maintainer documentation. A profile is a statement about an implementation. It is
not evidence that an agent used a tool, that credentials work, that policy permits
an operation or that access to a particular resource succeeds.

## Detection, identity and capability

| Observation | Meaning |
| --- | --- |
| Configured | An MCP entry exists in an agent configuration; unknown implementations remain generic `MCPServer` capabilities. |
| Recognized | An executed package, executable, Python module, container image or exact remote endpoint matches a bundled identity. |
| Capability verified | Official evidence defines the specific resource/action operation recorded in a claim. |

The registry counts distinct implementations. Package aliases, version suffixes,
container and executable distributions of the same implementation count once.
Current and archived reference implementations remain distinct from independently
maintained replacements only where the source and distribution identities differ.
The catalog is generated from registry data and includes every implementation,
identity, status, source reference, claim, limitation and review date.

The research inventory combines a general group of 55 implementations and a group
of 55 AWS-maintained implementations. These are separate server implementations
with independent package identities, not versions or aliases counted again. The
result covers the requested domains, but AWS contributes half the inventory; this
is coverage of a significant ecosystem, not a balanced popularity ranking. No
profile implies that its implementation is malicious.

## Official evidence and statuses

Review order is source implementation, official MCP tool schema, specific official
documentation, then official package/registry metadata. Third-party catalogs can
suggest candidates but cannot establish a capability. A README with an explicit
tool name, parameters and operation can corroborate a claim; introductory marketing
language cannot. Repositories, manifests and tool definitions are pinned to the
reviewed commit or version. Verification dates record that static review.

- `verified`: both machine identity and at least one explicit capability claim have
  official evidence.
- `recognized_only`: machine identity has official evidence, but no capability is
  assigned. Recognition does not need a forced mapping into an existing dimension.
- `experimental`: provisional claims cannot use `confirmed` confidence.

The old `@modelcontextprotocol/server-git` and
`@modelcontextprotocol/server-fetch` strings remain compatibility aliases. Their
npm distributions were not verified; recognizing those historical strings alone
is `suspected` and cannot confirm implementation capabilities. Official Python
packages and native executables retain their independently verified identities.

## Exact identity matching

The resolver examines the launched identity in a known syntactic position. It
supports `npx`, `bunx`, `uvx`, `pipx run`, `python -m`, native executable paths and
`docker run`. npm and PyPI namespaces stay separate; package versions, container
tags and valid SHA-256 image digests are normalized without substring matching.
Windows and POSIX path separators and common executable suffixes are recognized.

Known launcher flags have explicit value arity. Unknown option arity fails closed.
A package supplied with `--package`, `--from` or `--spec` does not independently
prove which implementation executes; the command and its distribution provenance
must agree. Install helpers and evaluators are not registered as server binaries.

Server display names, workspace/repository paths, option values, environment
values, credential strings and URL query parameters never establish identity.
Remote matching uses the full sanitized endpoint, including exact host and path;
a hostname suffix or a query embedding a known URL does not match. URL userinfo,
query and fragment are removed before storing an endpoint.

## Claims and the legacy fields

Each normalized claim has a resource and action, confidence, evidence, scope,
locality (`local`, `remote` or `unknown`), read-only status, official source URL,
verification status and exact tool names. The small reusable resource/action
vocabulary is defined in `src/agenthound/models/claims.py`; the generated catalog
lists the current vocabulary and coverage. Product names do not become taxonomy
terms.

| Existing field | Compatible normalized operations |
| --- | --- |
| `file_access` | Agent-selected `filesystem/read` or `filesystem/write`. |
| `file_readonly` | True only when every retained filesystem operation is read-only. |
| `shell_exec` | Agent-controlled `process/execute`; a shell launcher is insufficient. |
| `network_send` | Arbitrary-URL network retrieval or browser navigation. Fixed search APIs remain domain-specific claims. |
| `git_write` | Repository write, commit, push, merge, creation or deletion, with locality/scope retained. |
| `scoped_paths`, `repo_scoped` | Only implementation-specific argument formats with verified scope extraction. |

A server's internal persistence or configuration-file reads do not grant arbitrary
filesystem access. Directory listing is `filesystem/search` and does not imply reading file contents. Database query/write operations do not become filesystem or
shell capabilities. Browser JavaScript, container control and execution inside a
Kubernetes pod do not become unrestricted host-process execution. A cloud API
connection does not establish `cloud/administer`.

Local repository commit support is distinct from remote push or repository API
writes. Secret listing or metadata is distinct from retrieving a secret value;
`secrets/read` requires evidence of the latter. Fixed search APIs are distinct
from requests to arbitrary URLs. Unsupported domains remain normalized claims or
explicit model gaps.

`readonly` describes the verified operation, not current authorization. A SQL tool
that can support writes under a configuration switch is not statically marked
read-only merely because its default has a guard. Query support alone does not
establish administrator access, a specific database connection, or successful SQL.
Browser navigation can change browser state, so its read-only status may be false
even when the network operation reads a page.

## Four confidence questions

| Confidence | What can be established |
| --- | --- |
| Identity | An exact configured machine identity names the implementation. |
| Implementation capability | A pinned official schema or handler defines the operation. |
| Live advertisement | The connected server currently advertises the exact tool. |
| Effective access | A permitted operation can reach a particular resource; static profiles and advertisement alone do not establish this. |

A `confirmed` identity, implementation claim or advertisement always refers to its
stated observation. Effective-access confidence stays `none` for profile-derived
and advertised-only observations. Name heuristics remain `suspected`; untrusted
description prose cannot confirm a capability.

## Static and live discovery

Ordinary collection loads only the bundled registry and local configuration. It
performs no runtime network lookup and imports no third-party MCP implementation.
The optional, explicitly requested `--discover-mcp` path performs MCP metadata
discovery and can launch configured servers or connect to their configured
endpoints. That runtime option is separate from the static research procedure.
Neither metadata discovery nor these tests invoke `tools/call` to test an operation.

An exact live advertised tool is stronger evidence of present availability than
static implementation support. For a recognized implementation, live tools inherit
only matching official claims, filtered by configuration and read-only annotations.
A description alone supplies no confirmed mapping. Read-only, destructive,
idempotent and open-world hints are retained as advertised metadata, not treated
as a successful execution or trusted authorization policy.

`enabled_tools`, `disabled_tools` and supported allowlist/denylist settings filter
exact tool names. An explicitly empty allowlist retains no tools. When same-name configuration observations are merged, tool restrictions are intersected conservatively; conflicting implementation identities and their claims become suspected. Disabled write
tools cannot contribute an effective write claim. A successful tool listing is
used to reconcile static claims with the advertised names. If listing fails or
is incomplete, discovery status and evidence retain that limitation; absence in a
partial response is not a general proof that the implementation lacks a tool.
Product-specific CLI switches, policies, server-side tool groups, annotation
accuracy and dynamic authorization are not exhaustively inferred.

Descriptions and schemas from servers remain untrusted data. Existing length
limits, sanitization and sensitive-value redaction apply to live metadata.

## Graph and OpenGraph semantics

The existing `Source`, `Agent`, `Capability`, `Asset` and `Impact` model and the
`Influences`, `HasCapability`, `CanAccess` and `Triggers` edges are retained. Adding
a database, secrets, cloud or repository profile does not identify a particular
accessible asset or generate a corresponding administrative impact.

Implementation-only and advertised-only MCP observations do not grant unscoped
asset access or impact triggers. A configured filesystem path may produce a
conservative, `suspected` path-scoped `CanAccess` relationship where the existing
asset matching also applies; it does not claim a successful runtime file read.
No new mass impact inference is introduced. Service-specific permissions, remote
resource identities and successful execution remain model gaps.

The official [OpenGraph property rules](https://bloodhound.specterops.io/opengraph/developer/nodes#property-rules) permit primitives and homogeneous primitive arrays, and reject nested objects and arrays of objects.

Internal typed claims are serialized as JSON **strings** in a flat string list.
Nested claim dictionaries are not emitted as OpenGraph properties. Existing legacy
fields remain available, and serialized claims retain resource/action, confidence,
source, scope, locality and verification status.

## Privacy and offline verification

Only necessary identity and evidence metadata is exported: package identities,
sanitized endpoints, tool names, environment variable names and header names.
Environment/header values, tokens, API keys, passwords, cookies, secret values,
URL credentials and secret-bearing command arguments are excluded or redacted.
Configured process arguments are used internally only where parsing requires them;
the full argument vector is not added as a graph property.

Research fetched public official text, manifests, source archives and registry
metadata for static inspection. No researched MCP was installed or executed, no
container image was pulled or started, no service credentials were supplied and
no MCP tool was called. Tests use local fixtures and mocks without external MCP
or service connections.

## Maintenance and validation

Add records to `src/agenthound/discovery/data/mcp_profiles.json`; a profile-only
addition requires no resolver changes. The validated package registry rejects
invalid schemas/confidence, duplicate canonical identities, duplicate aliases and
conflicting identities. `recognized_only` records cannot carry claims, and
`experimental` records cannot confirm them. Package data is bundled in the wheel;
loading has no network dependency.

Add identity and claim tests with every batch. Positive and negative matching must
cover real launch positions, versions, module/executable/container/endpoint forms,
namespace confusion, path/option/query decoys and hostname attacks. Live tests
must check exact tools, filters, annotations, redaction and absence of tool calls.
Graph tests must distinguish implementation support from effective access.

Regenerate and verify the catalog from the repository environment:

```sh
python scripts/generate_mcp_catalog.py
python scripts/generate_mcp_catalog.py --check
```

`--check` exits nonzero when the generated catalog differs from the validated
registry. Registry updates should also pass the complete pytest suite, lint and
format checks on changed files. The initial audit recorded **333 passing tests**
and a passing `ruff check`. The repository-wide format baseline already had
**62 files failing `ruff format --check`**; unrelated formatting changes are
excluded from this work. Final validation results belong in the change report.
