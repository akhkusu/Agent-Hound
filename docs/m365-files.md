# Live SharePoint file listing

`m365-files` performs read-only Microsoft Graph requests and prints the immediate children of a SharePoint document-library folder. It is separate from `copilot-windows`, which inventories the Windows host's local settings.

The Graph HTTP collector is implemented, but **a Copilot authentication adapter is not connected yet**. The command does not inspect OneAuth, decrypt browser cookies, use Azure CLI, or initiate login/consent. Without an explicit provider it fails with that limitation rather than writing an empty success result.

## Use an explicitly supplied existing Graph token

An authorized authentication provider can put its existing Graph token in an environment variable in the current process. Never paste a token into the command line, this repository, a chat, or a log. The CLI takes the variable's **name**, not its value:

```powershell
agenthound m365-files `
  --tenant-id TENANT_GUID `
  --site-url https://contoso.sharepoint.com/sites/Research `
  --folder Project_ORION `
  --token-env AGENTHOUND_GRAPH_TOKEN `
  -o orion_files.json
```

`Project_ORION` is relative to the site's **default document library root**. Do not prefix it with `Shared Documents`. The command is not recursive and does not download document contents. Use `--json` for JSON on stdout; `-o` optionally saves the same metadata. The terminal table shows name, kind, bytes, modified time, and web URL. JSON also includes stable item, parent and drive IDs.

With a known Graph drive ID, skip site resolution:

```powershell
agenthound m365-files --tenant-id TENANT_GUID --drive-id DRIVE_ID `
  --folder Project_ORION --token-env AGENTHOUND_GRAPH_TOKEN --json
```

Microsoft documents `Sites.Read.All` as the least delegated permission for [resolving a site by path](https://learn.microsoft.com/en-us/graph/api/site-getbypath?view=graph-rest-1.0). The [drive children API](https://learn.microsoft.com/en-us/graph/api/driveitem-list-children?view=graph-rest-1.0) and [drive metadata API](https://learn.microsoft.com/en-us/graph/api/drive-get?view=graph-rest-1.0) list `Files.Read` as their least delegated permission. Knowing a drive ID can therefore avoid the broader site-resolution permission. The caller still needs access to the target resources; having a permission scope alone does not grant access to another user's files.

This explicit token path is an integration interface and testable HTTP path. It does **not** demonstrate that Copilot's own authentication or capabilities were used. Do not report the Copilot integration complete until that adapter has been connected and live retrieval verified.

## Authentication adapter interface

The Python collector accepts a `token_provider` callback separately from its HTTP implementation:

```python
collect_m365_files(
    tenant_id=tenant_id,
    site_url=site_url,
    folder="Project_ORION",
    token_provider=existing_authorized_auth_provider,
)
```

The callback takes the requested tenant GUID and returns a token in memory. The collector uses readable JWT `tid` and `aud` metadata only to reject accidental wrong-tenant/wrong-resource routing. It does not verify the JWT signature itself; Microsoft Graph authenticates and authorizes the request. Opaque/encrypted tokens cannot pass this current routing guard and require a provider integration that can establish the equivalent target binding.

## Failure behavior and collection limits

- Missing authentication fails before any Graph GET and writes no listing.
- HTTP 401 means Graph rejected authentication; 403 means access/scopes/consent need checking. HTTP 404 can mean an incorrect path or a resource hidden from the caller, not proven absence. HTTP 429 requests a later retry.
- Every request is a GET to `https://graph.microsoft.com/v1.0/`. Redirects are rejected. Pagination must remain on the exact requested children endpoint; the bearer token is not sent to a download URL or a foreign next link.
- Each response is bounded to 4 MiB with a 20-second request timeout. The default is at most 100 pages and 10,000 items (`--max-items` can adjust the item limit). Exceeding a limit, repeated pages, duplicate IDs or unexpected parents fails instead of presenting a partial result as complete.
- Only selected metadata is returned. Download URLs, token bodies, raw API error bodies and authentication responses are not printed or saved. Normal SharePoint paths and Office `Doc.aspx` links with known document identity parameters are supported; authentication query parameters are rejected.
- A folder can change between pages; the listing is not a transactional snapshot. It does not prove complete ACLs, document-content retrieval, or Copilot access.
