# Token Telemetry Dashboard

No measured telemetry has been published yet. This dashboard is populated only
from a real measurement run: a release-tag push whose release PR included a
matching `docs/marc/benchmarks/OPTIN` declaration (issue #309 — tag shape
alone no longer triggers this; a patch tag never does, opt-in or not), or a
`workflow_dispatch` opted into `real_run=true`. No chart is shown until that
has happened at least once.
