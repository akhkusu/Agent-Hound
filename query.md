# Agent-Hound Cypher Query Reference

Run these in the **Cypher** tab of BloodHound CE after ingesting Agent-Hound output.

## Node Types

| Label | Key Properties |
|-------|---------------|
| `Agent` | `name`, `platform`, `config_path` |
| `Source` | `name`, `source_kind`, `path`, `is_external` |
| `Capability` | `name`, `cap_kind`, `command`, `transport`, `has_shell` |
| `Asset` | `name`, `asset_kind`, `path`, `readable`, `writable` |
| `Impact` | `name`, `impact_kind`, `reachable`, `description` |

## Edge Types

| Kind | Direction | Meaning |
|------|-----------|---------|
| `Influences` | Source → Agent | IPI vector |
| `HasCapability` | Agent → Capability | Tool is enabled |
| `CanAccess` | Capability → Asset | Scope-limited access; inspect `access_scope`, confidence and evidence |
| `Triggers` | Capability → Impact | Tool produces this consequence |

---

## Full Attack Chain

```cypher
MATCH path = (s:Source)-[:Influences]->(a:Agent)-[:HasCapability]->(c:Capability)-[:CanAccess]->(asset:Asset)
RETURN path
```

## IPI Source to Exfiltration

```cypher
MATCH path = (s:Source)-[:Influences]->(a:Agent)-[:HasCapability]->(c:Capability)-[:Triggers]->(i:Impact {impact_kind: "Exfiltration"})
RETURN path
```

## All Assets Reachable via Shell

```cypher
MATCH (c:Capability {has_shell: true})-[:CanAccess]->(a:Asset)
RETURN c.name AS capability, a.name AS asset, a.asset_kind, a.path
ORDER BY a.asset_kind
```

## Choke Points: Capabilities in Most Impact Paths

```cypher
MATCH (c:Capability)-[:Triggers]->(i:Impact)
RETURN c.name, c.cap_kind, count(i) AS impact_count
ORDER BY impact_count DESC
```

## SSH Keys Reachable

```cypher
MATCH (c:Capability)-[:CanAccess]->(a:Asset {asset_kind: "SSHKey"})
RETURN c.name, a.path
```

## Agents with Both Shell Capability and Internet Access

```cypher
MATCH (a:Agent)-[:HasCapability]->(c:Capability)-[:Triggers]->(i:Impact)
WHERE c.has_shell = true AND i.impact_kind = "Exfiltration"
RETURN a.name, c.name, i.name
```

## All Impact Paths by Kind

```cypher
MATCH path = (c:Capability)-[:Triggers]->(i:Impact)
RETURN i.impact_kind, count(path) AS path_count
ORDER BY path_count DESC
```

## Microsoft 365 and SharePoint

Copilot uses the existing Agent, Capability, Source and Asset kinds. ACL identities remain analysis inputs. These Cypher queries display the exported graph; they do not register standard BloodHound Pathfinding support.

Display candidate document-content paths with individual document names:

```cypher
MATCH p=(agent:Agent)-[:HasCapability]->(cap:Capability)-[access:CanAccess]->(document:Asset)
WHERE agent.platform = 'Microsoft 365 Copilot'
  AND access.access_scope = 'document_content'
RETURN p
```

Show names, confidence and whether retrieval was observed:

```cypher
MATCH (agent:Agent)-[:HasCapability]->(:Capability)-[access:CanAccess]->(document:Asset)
WHERE document.asset_kind = 'SharePointDocument'
RETURN agent.displayname, document.name, document.path,
       access.access_scope, access.confidence, access.retrieval_verified,
       access.retrieval_route, access.requires_explicit_document_context
```

Show complete candidate IPI paths through Copilot. The live collector marks these suspected and conditional on explicitly supplying the document:

```cypher
MATCH p=(source:Source)-[:Influences]->(agent:Agent)-[:HasCapability]->(:Capability)-[:CanAccess]->(document:Asset)
WHERE agent.platform = 'Microsoft 365 Copilot'
RETURN p
```

Show a referenced document influencing Copilot and its candidate route to a different restricted document:

```cypher
MATCH p=(entry:Source)-[:Influences]->(agent:Agent)-[:HasCapability]->(:Capability)-[access:CanAccess]->(target:Asset)
WHERE entry.asset_kind = 'SharePointDocument'
  AND target.asset_kind = 'SharePointDocument'
  AND target.classification <> 'unclassified'
  AND entry <> target
  AND NOT (target)-[:Influences]->(agent)
  AND access.access_scope = 'document_content'
RETURN p
```

Use `access.retrieval_verified = true` when filtering for observed retrieval. Even then, `explicit_document_context` requires supplying that particular document; it does not establish autonomous browsing.

## GitHub Writers Who Can Supply Agent Sources

Requires GitHound data and the Agent-Hound GitHub bridge.

```cypher
MATCH path = (u:GH_User)-[:GH_HasRole|GH_MemberOf|GH_HasBaseRole*1..]->
  (:GH_RepoRole)-[:GH_CanWriteBranch]->(:GH_Branch)-[:AH_SuppliesSource]->
  (:Source)-[:Influences]->(:Agent)
RETURN path
```
