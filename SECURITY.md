# Security policy

## Supported versions

Security fixes are provided for the current public release line.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |

## Reporting a vulnerability

Parser, archive, network, extraction, cache, and materialization vulnerabilities must
not be reported in a public issue. GitHub's **Security → Report a vulnerability** route
is preferred when available. The maintainer's [ORCID record](https://orcid.org/0000-0002-7904-3892)
provides the fallback contact route.

Include the affected version, a minimal reproduction, the expected impact, and any
suggested mitigation. Do not include confidential research data or third-party content.
Receipt should be acknowledged within seven days; remediation and disclosure timing
depend on severity and affected deployments.

VAO files and repository responses are untrusted input. The implementation applies
bounded reads, strict JSON parsing, host-restricted HTTPS, safe ZIP processing, and
exact digest verification. The complete trust model and known limits are documented in
[docs/security.md](docs/security.md).
