---
paths: src/api/**/*.mjs
---

# API conventions

- Validate request data before calling the data layer.
- Return JSON errors with a stable `code` and a short `message`.
- Keep database access in the API module that owns the resource.
