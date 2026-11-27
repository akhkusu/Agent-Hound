# Repository Agent Guidance

## README Policy

- Keep `README.md` concise, easy to scan, and focused on essential setup, basic usage, and a small number of headline features.
- Do not add a README section for every new collector, source type, option, or implementation detail unless the user explicitly requests it.
- Put detailed or optional feature documentation under `docs/` and link it only when that detail is important to the main user flow.
- Preserve the README's existing editorial intent; ask before materially expanding its scope.

## Source Inclusion Policy

- Emit a `Source` only when a documented mechanism automatically loads it, explicitly configures/references it, makes it invocable, advertises it through an explicitly discovered integration, or records observed use.
- File existence, readability, repository location, extension, account-level access, or filename similarity alone is not evidence that a file is an agent input.
- Apply this boundary equally to general documents, code/comments, cloud files, and platform-specific files for an unobserved agent. Keep access-only objects as Assets without `Influences`.
- Preserve `activation`, `confidence`, and `evidence`; configured or invocable does not mean invoked. See `docs/source-discovery.md`.
