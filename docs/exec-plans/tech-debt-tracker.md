# Tech Debt Tracker

| Item | Owner | Status | Next step |
| --- | --- | --- | --- |
| Add CI checks for canonical docs layout, local markdown links, and deprecated path usage | Repository maintainers | open | Add a lightweight docs validation script and wire it into CI |
| Replace the placeholder generated schema snapshot with a real DB exporter if the repo gains persistence | Repository maintainers | open | Promote `docs/generated/db-schema.md` to table and index documentation when a DB layer exists |
| Decide whether legacy `.claude` helper docs should be mirrored or indexed under `docs/references/` for full agent discoverability | Repository maintainers | open | Audit remaining tracked markdown outside `docs/` and either index or justify it |
