# Connecting to an AD Graph

On a domain-joined Windows host, run Claude Code and collect its process identity:

```powershell
agenthound discover -w C:\path\to\project --process-identity -o agent.json
```

Create bridge edges from the observed user and computer SIDs to matching SharpHound objects:

```powershell
agenthound ad-bridge --agent-graph agent.json --ad-zip C:\path\to\sharphound.zip -o ad-bridge.json
```

Import the matching SharpHound ZIP, `agent.json`, and `ad-bridge.json` into BloodHound. The bridge creates `AH_RunsAs` and `AH_RunsOn` edges only for exact SID matches; unmatched identities produce no edge.
