MATCH sourcePath=(source:Source)-[:Influences]->(agent:Agent)
WHERE agent.platform = 'Claude Code'
  AND agent.config_path CONTAINS '/blackhat-arsenal/build/acme-saas/.claude'
  AND (
    source.path ENDS WITH '/CLAUDE.md'
    OR source.path ENDS WITH '/.claude/rules/api-conventions.md'
    OR source.path ENDS WITH '/.claude/rules/testing.md'
    OR source.path ENDS WITH '/.claude/commands/triage.md'
    OR source.path ENDS WITH '/.claude/skills/deploy/SKILL.md'
  )
MATCH directPath=(agent)-[:HasCapability]->(capability:Capability)
WHERE (
    capability.cap_kind = 'BuiltInTool'
    AND capability.internal_name IN ['Read', 'Bash']
  )
  OR (
    capability.cap_kind = 'MCPServer'
    AND capability.internal_name IN ['Playwright', 'Supabase', 'Sentry']
  )
OPTIONAL MATCH exposedPath=(capability)-[:Exposes]->(tool:Capability)
WHERE tool.cap_kind = 'MCPTool'
  AND (
    (tool.mcp_server_name = 'Playwright'
      AND tool.tool_name IN ['browser_navigate', 'browser_click', 'browser_take_screenshot'])
    OR (tool.mcp_server_name = 'Supabase'
      AND tool.tool_name IN ['list_tables', 'execute_sql'])
    OR (tool.mcp_server_name = 'Sentry'
      AND tool.tool_name = 'get_issue_details')
  )
MATCH accessPath=(agent)-[:HasCapability]->(accessCapability:Capability)-[:CanAccess]->(asset:Asset)
WHERE accessCapability.cap_kind = 'BuiltInTool'
  AND accessCapability.internal_name IN ['Read', 'Bash']
  AND asset.asset_kind IN ['EnvFile', 'SourceCode']
  AND asset.path CONTAINS '/blackhat-arsenal/build/acme-saas'
RETURN DISTINCT sourcePath, directPath, exposedPath, accessPath
