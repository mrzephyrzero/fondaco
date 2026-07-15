# Security Policy

## Supported scope

Fondaco is a **reference architecture with a working implementation**, not a
hardened, maintained product. It demonstrates a data-boundary design for AI
over enterprise data. Only the latest `main` is in scope; there are no
supported releases or backports.

The security claim under test — and the honest limits of it — are documented
in [`design/threat-model.md`](design/threat-model.md) and the README section
["What this does NOT protect against"](README.md#what-this-does-not-protect-against).
Read those before deploying any of this against real data.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** through GitHub's private
vulnerability reporting:

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, affected component (`/boundary`, `/executor`,
   `/planner`, `/api`), and a reproduction if you have one.

This is the sole disclosure channel. Please do **not** open public issues or
pull requests for security findings before the report has been triaged, and
please do not disclose publicly until a fix or an accepted-risk statement is
in place.

> Maintainer note: enable *Settings → Code security → Private vulnerability
> reporting* on the repository so the button above is available.

## Response expectation (honest)

Fondaco is maintained by a single, unpaid maintainer as a reference project.
There is **no SLA**. Reports are handled best-effort; acknowledgement may take
days to weeks. A "critical" finding — one where row data can cross the
boundary — takes priority over everything else, consistent with the project's
one non-negotiable claim: *data stays home; plans cross.*

Confirmed findings are added to the threat model (with the fix or an
accepted-risk statement) so the public record stays honest — the threat model
ships *because* it shows real issues caught, not despite it.
