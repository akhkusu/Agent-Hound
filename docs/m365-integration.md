# Windows Copilot and SharePoint inventory

Agent-Hound separates local application observations, cloud permissions, and evidence that Copilot actually references a document. Finding a profile or a policy cache does not establish a licensed capability or an attack path.

## Inventory Windows application data

For live cloud file metadata, use [SharePoint file listing](m365-files.md). Its [authentication reference](copilot-sharepoint-auth.md) describes account selection and service-specific token requests. The inventory below observes local configuration and does not authenticate or retrieve cloud files.

```powershell
agenthound copilot-windows --local-app-data "$env:USERPROFILE\AppData\Local" `
  --include-registry -o windows_inventory.json --graph-output windows_graph.json
```

Without `--local-app-data`, the command uses `LOCALAPPDATA`. Sandboxed hosts may virtualize this variable; pass the actual authorized profile root or a copied profile when necessary. The command also works on an offline copy on other OSes.

The known roots are:

| Application data | Path relative to LocalAppData |
| --- | --- |
| Copilot desktop | `Microsoft/Copilot/User Data` |
| Authentication WebView | `Microsoft/Copilot/User Data/OneAuth/WebView2/EBWebView` |
| OfficeHub WebView | `Packages/Microsoft.MicrosoftOfficeHub_8wekyb3d8bbwe/LocalState/EBWebView` |

Only `Local State` and each `Default` or `Profile N` directory's `Preferences` are read. Output includes file-read status and known boolean values beneath `copilot.ocps_policies`: policy-response success, work/web file-upload flags, and work/web web-search flags. Values are cached observations, with no claim that a current policy allows an action. Malformed/missing/unreadable files are identified separately. Other preference data is discarded.

File upload is different from autonomous file search/read. The collector also checks the known `wcp.search_app_data.is_file_search_allowed` and `is_file_read_allowed` boolean settings in Local State and Preferences. It reports `cached_allowed`, `cached_denied`, or `unknown`, and prints a file-access assessment in the terminal. A missing key is **not** an allowed/denied default. No file retrieval is performed, so runtime local read and SharePoint retrieval remain `unverified`. This command does not enumerate readable documents or retrieve document contents. A cached flag does not produce a `CanAccess` edge.

Version 2 of the inventory also reports the presence of Cookies, Device Bound Sessions, Login Data, and shared OneAuth/IdentityCache/TokenBroker directories. These files are only checked for existence; their contents are never opened. The authentication WebView's allowlisted Preferences are read separately, and this root does not create another Agent node. Shared stores are not attributed to Copilot merely because they exist.

With `--include-registry`, the collector observes known keys under `HKCU\Software\Microsoft\Copilot\SharedStorage`: Auth, FeatureConfigs, FeatureGates, FeatureSettings, LocalSettings, Profiles and UserEligibility. Only key status, subkey/value counts and three allowlisted DWORD boolean flags are exported. Arbitrary registry values, account identifiers and credentials are not enumerated. Missing and unreadable keys are distinct. This option reads the **current Windows user's live registry**, even when filesystem paths point to an offline copy; omit it for a purely offline inventory. Other operating systems report `unsupported_platform`. Flags remain cached observations.

The collector never opens History, IndexedDB, Local Storage, Secure Preferences, credential bodies or conversation content. It does not export account identifiers, secrets, arbitrary preference strings, or tokens. It does not edit settings, execute binaries, or contact Microsoft services. The graph contains data-presence Agent observations and no inferred capability edges. Application-data leftovers may remain after uninstalling the application.

Bundled implementation strings and cached feature flags do not establish that MCP tools are configured or enabled.

`mcp_storage_presence` checks known MCP roots and their `accounts` directories for the Copilot, Copilot Dev and Copilot Internal channels, plus the legacy `Microsoft/Undocked/Autopilot/Mcp` root. The current layout is `Microsoft/<channel>/User Data/Autopilot/Mcp/accounts/<partition>/`, with `user-servers.json`, `registry.json`, `runtime-projection.json` and a `Credentials` root. Account partitions are derived rather than assumed to equal account IDs. These layouts can vary by version. Only path presence is exported: definition files, partition names and credential bodies are not read. Missing local storage does not establish that cloud MCP is absent, and present storage adds no capability or access edges.

For authored M365 declarative agents, Microsoft's documented configuration is `appPackage/declarativeAgent.json`, including instructions and selected knowledge sources. This is distinct from the Windows host's telemetry `appsettings.json`. SharePoint scope in a manifest still depends on the signed-in user's access. See [Microsoft's knowledge-source configuration guide](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/build-declarative-agents-add-knowledge).

## Import SharePoint observations

```powershell
agenthound m365-import --snapshot examples/m365_snapshot.json -o m365_graph.json
```

The example is a **synthetic scenario**, not a live tenant export. Its five documents mirror the ORION dataset: overview, meeting minutes, acquisition, customer pricing and staffing. An editor can modify the minutes. An executive can read the restricted documents and uses an agent configured with the minutes. The resulting graph is a candidate IPI path, not evidence of exploitation.

The version-1 JSON schema is implemented by `M365Snapshot` in `discovery/m365_snapshot.py`. It rejects extra fields, invalid roles, duplicate entity IDs and unresolved references. It contains:

- `tenant_id`: one canonical tenant namespace, preferably the Entra tenant GUID. Use the same identifier in all exports; aliases are not automatically merged.
- `observed_at`: a timestamp including UTC offset.
- `evidence_source`: `sharepoint_ui`, `graph_export` or `synthetic`.
- `principals`: stable IDs, display names and `user`, `group`, or `site_group`. Site-scoped SharePoint IDs must include the site scope. Never join by names alone.
- `memberships`: observed member and group IDs, with evidence. Group cycles are supported; directory membership must be collected separately from item ACLs.
- `documents`: stable resource IDs, names, canonical HTTPS URLs without query strings, declared classification, ACL completeness and positive grants. A grant has `read`, `write` and/or `owner` roles, inheritance status and evidence.
- `agents`: optional observed user-account bindings and explicit document references. A reference is `configured` or `retrieved` and needs evidence. `retrieval_route` distinguishes `explicit_document_context` from `autonomous_retrieval`; omit it or use `unknown` when the route was not observed. Memberships, grants, agent bindings and references can each carry their own timezone-aware `observed_at`, so an older ACL export is not presented as a new observation when a later retrieval is added.

Keep secrets, full document bodies and browser session data out of the snapshot. The importer performs no network requests and does not authenticate to Microsoft.

For example, pasting a SharePoint link into Copilot can resolve the file into chat document context. If an answer cites that file and matches known contents, record `mode: retrieved` with `retrieval_route: explicit_document_context`. The graph marks that edge `requires_explicit_document_context: true`. This proves reading the supplied document in that observed context; it does not prove arbitrary SharePoint browsing or unattended retrieval. Never use the assistant's claim of access alone as proof: verify the cited identity and contents against the intended document.

## Graph semantics

| Relation | Meaning |
| --- | --- |
| `M365Principal → MemberOf → M365Principal` | Observed membership |
| `M365Principal → CanRead/CanWrite/Owns → Source + Asset` | Observed positive permission |
| `Agent → RunsAs → M365Principal` | Explicit observed work-account binding |
| `Agent → HasCapability → Capability → CanAccess → Source + Asset` | ACL-supported retrieval candidate (suspected), or observed document retrieval (confirmed) |
| `Source + Asset → Influences → Agent` | Accessible document explicitly configured as a source (suspected), or observed retrieved (confirmed) |

Documents use one node with both Source and Asset kinds. IDs are tenant scoped and based on resource IDs, so file rename does not replace the node. Register the `M365Principal` icon with `agenthound register-icons` alongside existing types.

Write and owner grants imply read. An empty grant list or incomplete collection does not prove denial. `classification` is supplied metadata; the collector does not infer confidentiality or enforce a sensitivity label. No exfiltration or shell-impact edge is inferred from a SharePoint read permission. Local desktop inventory is not automatically joined with a cloud agent by display name.

Observed retrieval remains positive access evidence when ACL collection is incomplete. A configured source without a matching positive ACL grant does not create an influence edge. Repeated references to the same document are combined, with observed retrieval taking precedence over configuration-only evidence. Retrieval confirmation applies to that document, not all files the user can read. An agent's `unresolved_reference_count` records configured references with no positive ACL or retrieval observation. This indicates missing access evidence, not a confirmed denial.

## Collect reliable observations

Use SharePoint's document **Shared With → Advanced** view to inspect inheritance and role assignments. Keep an administrative owner, restrict only the lab documents, and record changes before and after. Viewing as site admin does not test access as an ordinary employee. Also inspect group memberships and sharing links; permission assignments alone do not enumerate every effective user.

Microsoft Graph's sharing-permission API can be used as an input source for a separate authenticated export. It returns more information to an owner than a non-owner. SharePoint/OneDrive for Business may omit `inheritedFrom`: its absence must remain `unknown`, not be normalized to `direct`. A live Graph collector and credential provisioning are outside this metadata-import stage.

References:

- [Permissions and inheritance](https://learn.microsoft.com/en-us/sharepoint/manage-permission-scope)
- [List driveItem sharing permissions](https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0)
- [Permission resource caveats](https://learn.microsoft.com/en-us/graph/api/resources/permission?view=graph-rest-1.0)
- [Copilot on Windows](https://support.microsoft.com/en-us/microsoft-365-copilot/access-microsoft-365-copilot-on-windows)
