# SharePoint file listing

`agenthound m365-files` lists file metadata using the current Windows user's Copilot account. It discovers SharePoint locations and displays up to 20 files by default. It does not download document contents or collect complete ACLs.

## Requirements

The default mode requires Windows, an existing organizational Copilot sign-in, and Agent-Hound's Windows authentication dependencies:

```powershell
python -m pip install -e ".[windows-auth]"
```

Authentication uses the public Windows Web Account Manager (WAM) API and the account references stored by Copilot. The client must already be authorized for the target service. Agent-Hound does not register an application or initiate interactive login or consent. No external authentication CLI is required.

## Discover files

```powershell
agenthound m365-files
agenthound m365-files --json -o files.json
```

The command resolves the tenant and a SharePoint host candidate from local account metadata, verifies the host's tenant, discovers sites through SharePoint search, and walks their document libraries and folders. Default traversal stops after 20 file records or its 45-second traversal budget. Individual network and authentication calls have separate timeouts.

To finish traversal of discovered locations while retaining at most 20 records:

```powershell
agenthound m365-files --enumerate-all --limit 20 -o files.json
```

`--enumerate-all` can take longer on large sites. Search can omit unindexed or search-excluded sites, so finishing this traversal does not establish tenant-wide coverage. Raw library listings can include templates and SharePoint support pages such as `Forms/*.aspx`.

## Select a location

Use `--site-url` when local metadata does not reveal a verifiable SharePoint host, or to restrict discovery to one site:

```powershell
agenthound m365-files --site-url https://contoso.sharepoint.com/sites/Research
```

To list the immediate children of one folder:

```powershell
agenthound m365-files --api sharepoint `
  --site-url https://contoso.sharepoint.com/sites/Research `
  --library "Shared Documents" --folder Project_ORION
```

`--library` is the library's decoded URL path, which defaults to `Shared Documents`. `--folder` is relative to that library root. `--tenant-id` can require a specific tenant; it must match the configured account.

When the configured account has multiple client mappings, specify `--copilot-client-id` using the ID for the installed Copilot configuration. The selected ID must exist in that account's mapping. Account and client IDs are not selected by display name or list order.

## Counts and completeness

| Field | Meaning |
|---|---|
| `displayed_file_count` / `displayed_item_count` | Records retained in the output, bounded by `--limit`; explicit-folder output can include folders. |
| `enumerated_file_count` | File metadata records counted before traversal stopped. This does not verify content access. |
| `total_files_in_discovered_libraries` | Total counted in discovered libraries after traversal finishes without errors or early stopping; otherwise `null`. |
| `libraries[].reported_item_count` | SharePoint's library-reported item count. It can include folders and is not a caller-specific count of readable documents. |
| `content_access_verified_count` | `null`: document bodies are not requested. |
| `enumeration_complete` | The attempted traversal finished without errors or early stopping. |
| `site_discovery_complete` | False for search-based discovery. An explicitly selected site bounds the requested site scope. |
| `errors` | Locations or collection stages that could not be enumerated. |

For an explicit folder, `complete` describes collection before the output limit is applied; `items_truncated` identifies a shortened preview. In automatic discovery, `complete` also requires an explicitly bounded site scope. A partial listing must not be interpreted as an empty site or a complete inventory.

## Microsoft Graph mode

An explicitly supplied Graph token or drive ID selects the Graph backend. This mode lists one folder's immediate children:

```powershell
agenthound m365-files --api graph --tenant-id TENANT_GUID `
  --site-url https://contoso.sharepoint.com/sites/Research `
  --folder Project_ORION --token-env AGENTHOUND_GRAPH_TOKEN
```

`--token-env` takes the environment variable's name, not the token value. It cannot be combined with `--copilot-wam`. `--drive-id` skips site resolution. Graph and SharePoint tokens target different services and are not interchangeable.

Microsoft documents `Sites.Read.All` for [site resolution by path](https://learn.microsoft.com/en-us/graph/api/site-getbypath?view=graph-rest-1.0) and `Files.Read` as the least delegated permission for [drive children](https://learn.microsoft.com/en-us/graph/api/driveitem-list-children?view=graph-rest-1.0). User access to the target resources is also required.

## Failures and limits

Authentication failures stop before an authenticated listing request. Automatic discovery records collection failures in `errors`; it does not convert them into successful empty collections. A denied or hidden resource does not prove that it is absent.

Requests use GET only, reject redirects, and limit each response to 4 MiB. Collection is bounded to 100 pages and 10,000 items per folder by default; `--max-items` adjusts the item limit. Recursive traversal is bounded to 10,000 folders. Repeated pages, duplicate IDs, unexpected parents or exceeded limits fail the affected collection. A folder can change during pagination, so the result is not a transactional snapshot.

See [Copilot authentication and SharePoint collection](copilot-sharepoint-auth.md) for the implementation and trust checks.
