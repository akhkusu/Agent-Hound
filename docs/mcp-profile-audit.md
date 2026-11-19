# MCP profile implementation audit

Baseline: `/srv/Agent-Hound`, branch `new-source`, before this change. The checkout
already contained a user edit to `README.md` and an untracked `work/` directory.
Both were preserved. The baseline suite passed **333 tests**; Ruff lint passed;
repository-wide formatting reported **62 existing files** requiring formatting.

## Original implementation

- `discovery/profiles.py` contained nine hard-coded dataclass profiles and a
  package/alias dictionary. Known npm-style version suffixes were stripped.
  Direct executable basenames and the first positional argument of `npx`,
  `bunx` and `uvx` were considered; known option values were skipped. Docker,
  Python module, pipx and exact remote endpoint identities were not supported.
- `discovery/config_parser.py` assigned legacy dimensions from known profiles,
  otherwise suspected token heuristics used server labels and executed names.
  A shell launcher alone supplied a suspected shell dimension. Arguments were
  excluded from graph commands, while endpoint userinfo/query/fragment were
  stripped. Tool filters were recorded but did not constrain profile dimensions.
- `models/nodes.py` had `file_access`, `file_readonly`, `shell_exec`,
  `network_send`, `git_write`, file/repository scopes and per-dimension evidence.
  It had MCP configuration metadata and flat live annotations, but no normalized
  claims or separate identity/implementation/advertisement/access confidence.
- `collectors/capabilities.py` merged same-name capabilities across observations,
  widening scopes and combining dimension confidence. Profiles had no typed
  per-operation provenance to preserve across that merge.
- `--discover-mcp` used initialize and tools/resources/prompts listing. It did
  not call tools or read advertised content. Tool-name heuristics were suspected;
  descriptions were already not a source of confirmed capability. Exact names,
  policy filtering, provenance and redaction needed strengthening.
- `collectors/assets.py` inferred read reachability from file/shell dimensions.
  File scopes constrained paths; unknown scope could still produce suspected
  access edges. `collectors/impact.py` inferred shell, network and repository
  impacts from legacy dimensions, internet connectivity and repository remotes.
  Consequently implementation capability could be confused with resource access.
- `output/opengraph.py` delegated to each model's `to_opengraph`. Capability
  properties were scalars and flat lists. Official OpenGraph property rules
  reject nested objects and arrays of objects, so normalized claims now serialize
  as JSON strings in a homogeneous string list.

## Resulting boundaries

See [methodology](mcp-profile-methodology.md) and the generated
[catalog](mcp-profile-catalog.md). Exact implementation support and live
advertisement are distinct from effective access. Existing node/edge kinds,
legacy property names and stable object IDs remain. Directory metadata and a
shell launcher no longer imply reading file contents or arbitrary execution.
Profile-only capabilities do not infer unscoped asset access or impact triggers;
explicit filesystem roots retain only suspected scoped relationships.

Four existing regression cases changed their expected security semantics: a shell
launcher no longer grants shell execution; a GitHub profile no longer proves
access to a local repository; unknown file keywords no longer prove host-asset
access; a Codex allowlist containing only directory listing no longer grants file
content reads. Other existing test behavior remains covered by the full suite.

## Integration before publication

Before committing, remote `main` at `3074f4a` was pulled with a fast-forward.
Conflicts were resolved while retaining upstream safe configuration readers,
64 KiB and symlink boundaries, sanitized errors, hook-body privacy, source
metadata and the `scan` CLI. Valid filesystem roots before an unknown option
remain scoped; unknown option values never become roots. The integrated full
suite passed **1,531 tests**, Ruff lint passed and the generated catalog remained
current. The user's pre-existing README edit remains outside this change.
