# Contributing to Fondaco

Thanks for looking. Fondaco is a security boundary, so a few rules here are
stricter than in most projects. They exist so that a reviewer can trust a
change without re-deriving the whole threat model.

## Before you start

- **Be decent.** Participation is governed by the
  [Code of Conduct](.github/CODE_OF_CONDUCT.md).
- **Security findings never go in a public issue or PR.** Use GitHub's private
  vulnerability reporting, as described in [`SECURITY.md`](SECURITY.md).
- **Open an issue before a large change.** New adapters, new plan-DSL features
  and anything that touches `boundary/` should be discussed first.
- **Read the threat model** ([`design/threat-model.md`](design/threat-model.md))
  if your change touches what crosses the boundary.

## Setup

```sh
git clone https://github.com/mrzephyrzero/fondaco && cd fondaco
python3.12 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip            # --group needs pip >= 25.1
pip install -e . --group dev
pytest                               # Postgres integration tests skip without a DSN
fondaco demo                         # the zero-config demo, from your checkout
```

The Postgres integration tests run when `FONDACO_TEST_DSN` and
`FONDACO_TEST_ADMIN_DSN` are set (see `.env.example`); `docker compose up db`
gives you a database with the demo dataset and the read-only role.

## The rules

1. **The three interfaces are frozen.** `design/plan-dsl.md`,
   `design/label-model.md` and `design/adapter-contract.md` change only through
   a versioned request: describe the flaw or need under
   `INTERFACE_CHANGE_REQUEST` in `STATE.md`, and the maintainer approves it.
   An approved change ships as a new version of the document, with a changelog
   entry, never as a silent edit.
2. **`boundary/` is security code.** Parametrized queries only; fail closed on
   every error path (an exception is a deny, never an allow); no dynamic eval;
   anything an LLM or an agent wrote is hostile input. When in doubt, deny and
   say why in a machine-readable code.
3. **Tests come with the code, in the same PR.** A new defense needs an
   adversarial test that fails without it. A new adapter needs a test that
   proves the source itself refuses writes, not only the adapter.
4. **No new dependency without a reason.** Add one line of rationale to the
   Dependencies table in `DECISIONS.md`. Prefer the standard library. Optional
   integrations go in an extra, imported only when selected.
5. **The repository layout is fixed.** No new top-level directories; see
   `CLAUDE.md` for the map.
6. **Every source file carries the SPDX header** (`# SPDX-License-Identifier:
   Apache-2.0` and the copyright line). All contributions are under Apache-2.0.
7. **No real data, credentials or internal references** in code, tests or
   examples. Synthetic data only; the demo's IBANs and SSNs are generated in
   ranges that belong to no one.

## Checks

CI runs exactly these on every push; run them before you open a PR:

```sh
ruff check .
ruff format --check .
pytest
```

## Commits and pull requests

Small commits with imperative messages prefixed by area
(`boundary: refuse path-shaped relations`). In the PR, say what changed, why,
and how you tested it; link the threat-model row you touched, if any. Record a
design decision that a future reader would otherwise have to rediscover in
`DECISIONS.md`.
