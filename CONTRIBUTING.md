# Contributing

Contributions must keep the code path-agnostic and safe for repositories that do not contain source data.

1. Create a branch from `main`.
2. Add focused tests for parsers or metric behavior.
3. Run `python -m unittest discover -s tests -v`.
4. Verify each CLI still supports `--help` without local data.
5. Document input-layout or output-schema changes.

Never commit source workbooks, client/fund identifiers, credentials, local absolute paths, or generated reporting outputs.
