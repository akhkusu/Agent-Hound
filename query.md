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
| `M365Principal` | `name`, `tenant_id`, `resource_id`, `principal_kind` |

## Edge Types

| Kind | Direction | Meaning |
|------|-----------|---------|
| `Influences` | Source → Agent | IPI vector |
| `HasCapability` | Agent → Capability | Tool is enabled |
| `CanAccess` | Capability → Asset | Tool can reach this file |
| `Triggers` | Capability → Impact | Tool produces this consequence |
| `MemberOf` | M365Principal → M365Principal | Observed group membership |
| `CanRead` / `CanWrite` / `Owns` | M365Principal → Source + Asset | Observed SharePoint ACL grant |
| `RunsAs` | Agent → M365Principal | Observed cloud account binding |

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

# Microsoft 365 and SharePoint

Find principals who can change a document explicitly referenced by a Copilot agent that has a candidate route to a restricted document. The capability and source edges carry confidence and evidence; the path does not prove exploitation.

```cypher
MATCH p=(editor:M365Principal)-[:CanWrite]->(entry:Source)-[:Influences]->(agent:Agent)-[:HasCapability]->(cap:Capability)-[:CanAccess]->(target:Asset)
WHERE entry.asset_kind = 'SharePointDocument'
  AND target.asset_kind = 'SharePointDocument'
  AND target.classification <> 'unclassified'
  AND entry <> target
RETURN p
```

`editor` can be a group. Show its observed members separately:

```cypher
MATCH p=(member:M365Principal)-[:MemberOf*1..8]->(group:M365Principal)-[:CanWrite]->(document:Source)
WHERE document.asset_kind = 'SharePointDocument'
RETURN p
```

Find SharePoint inventory that has no explicit Copilot source-reference edge:

```cypher
MATCH (document:Source)
WHERE document.asset_kind = 'SharePointDocument'
  AND NOT (document)-[:Influences]->(:Agent)
RETURN document
```
