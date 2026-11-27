MATCH path=(source:Source)-[:Influences]->(agent:Agent)-[:HasCapability]->(read:Capability)-[:CanAccess]->(asset:Asset)
WHERE agent.platform = 'Claude Code'
  AND agent.config_path CONTAINS '/blackhat-arsenal/build/acme-saas/.claude'
  AND source.path ENDS WITH '/CLAUDE.md'
  AND read.cap_kind = 'BuiltInTool'
  AND read.internal_name = 'Read'
  AND asset.asset_kind = 'EnvFile'
  AND asset.path ENDS WITH '/blackhat-arsenal/build/acme-saas/.env'
RETURN path
