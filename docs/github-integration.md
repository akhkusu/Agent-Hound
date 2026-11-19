# Connecting to a GitHound Graph

GitHound remains responsible for collecting all GitHub-native nodes and edges. Agent-Hound records sanitized local Git facts and creates an edge-only bridge to matching `GH_Repository` and `GH_Branch` nodes.

Run Agent-Hound against the local workspace:

```bash
agenthound discover -w /path/to/project -o agent.json
```

Collect the corresponding organization with GitHound, including repositories and branches, then create the bridge:

```bash
agenthound github-bridge \
  --agent-graph agent.json \
  --githound-graph githound_<orgId>.json \
  -o github-bridge.json
```

`--githound-graph` may be repeated when repositories span multiple GitHound outputs. Import `agent.json`, the GitHound output files, and `github-bridge.json` into BloodHound.

The bridge contains no nodes. It creates:

| Edge | Direction | Meaning |
|---|---|---|
| `AH_CloneOf` | `Asset` → `GH_Repository` | The local repository's configured branch remote or `origin` exactly matches the GitHound repository. This edge is structural and non-traversable. |
| `AH_SuppliesSource` | `GH_Branch` → `Source` | The source is committed and clean, and the local HEAD equals GitHound's collected branch commit. This edge is traversable. |

Agent-Hound never exports remote credentials. Remote URLs are normalized to a credential-free `host/owner/repository` identifier. Sources that are dirty, untracked, on a stale commit, detached, or missing an upstream branch do not receive `AH_SuppliesSource`.
