# Contributing to VAO CLI

Contributions to the client, tests, and documentation are welcome.

## Before proposing a change

Search existing issues and read the [architecture](docs/ARCHITECTURE.md),
[security model](docs/security.md), and the normative
[VAO Standard 0.4.0](https://doi.org/10.5281/zenodo.22122774). Security vulnerabilities
must follow [SECURITY.md](SECURITY.md), not a public issue.

Changes that interpret VAO semantics should identify the relevant normative rule,
preserve unsupported information, define failure behaviour, and include valid and
invalid tests. Network or archive changes should state their resource and trust
boundaries.

## Local checks

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps --no-build-isolation -e .
export VAO_STANDARD_ROOT=/path/to/vao-standard-v0.4.0
python tools/check_release.py
```

## Pull requests

Keep commits focused and include tests and documentation with behavioural changes.
Commits should include a Developer Certificate of Origin sign-off:

```text
Signed-off-by: Your Name <your-address@example.org>
```

By signing off, you certify that you may submit the contribution under Apache-2.0.
Do not submit confidential data, personal research data, access tokens, or third-party
media without permission.
