"""Client & operator onboarding (docs/CLIENT_ONBOARDING_PLAN.md).

Pure core: `requirements` (the service -> required-input matrix), `status` (the
entitlement-driven readiness computation), `scaffold` (the ~/.sable/orgs/<org>/ file
templates). The CLI (`cli/onboarding_cmds.py`) is the thin I/O shell over this +
`db/onboarding.py`. Nothing in this package touches the DB or the network; `status`
operates on an `Evidence` dict the caller assembles, and `scaffold` writes under an
injected base dir — so both are unit-tested without a live DB or a real ~/.sable.
"""
