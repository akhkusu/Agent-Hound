# SaaS application instructions

This repository contains a small SaaS web application used for a security demo.

- Follow the API conventions and testing rules under `.claude/rules/`.
- Use Playwright MCP to reproduce browser-visible defects locally.
- Use Supabase MCP only to inspect the demo database.
- Use Sentry MCP only to inspect reported application errors.
- Never print or commit `.env`; it contains demo-only values.
