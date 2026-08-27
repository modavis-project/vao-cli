---
layout: page
title: Getting started
description: Install VAO CLI, connect the released VAO 0.4.0 reference tools, and validate or resolve a first object.
permalink: /getting-started/
---

## Requirements

- Python 3.11 or newer.
- A source checkout of the released VAO Standard 0.4.0 for full conformance.
- HTTPS access to Zenodo for remote commands.

## Install

```sh
git clone https://github.com/modavis-project/vao-standard.git
git -C vao-standard checkout v0.4.0
git clone https://github.com/modavis-project/vao-cli.git

cd vao-cli
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
export VAO_STANDARD_ROOT="$(cd ../vao-standard && pwd)"
vao --version
vao doctor
```

The standard checkout supplies the normative schemas and reference implementation.
VAO CLI accepts `--standard-root` for a single command when an environment variable is
not appropriate.

The reference validator is executed from this checkout. Use the signed `v0.4.0` tag in
the official repository and do not point `VAO_STANDARD_ROOT` at an untrusted directory.

## Validate the released fixture

```sh
vao validate "$VAO_STANDARD_ROOT/Fixtures/VAO04/carriers/minimal.vao"
```

Successful output distinguishes the local bounded carrier checks from full VAO 0.4.0
reference conformance. `--structural-only` deliberately omits the second layer and
makes no conformance claim.

## Resolve and inspect a record

Set `VAO_DOI` to an exact Zenodo version DOI for a published VAO:

```sh
export VAO_DOI=10.5281/zenodo.12345678

vao resolve "$VAO_DOI" --exact
vao inspect "$VAO_DOI" --exact --assets --groups --archive
```

Concept DOI input resolves to the current version and reports both identities. `--exact`
rejects concept DOI input when a reproducible operation requires a fixed version.

## Select and acquire

```sh
vao select "$VAO_DOI" \
  --kind geometry --quality low --max-bytes 100MiB

vao fetch "$VAO_DOI" urn:example:realization:model:mobile \
  --output model.glb --dry-run

vao fetch "$VAO_DOI" urn:example:realization:model:mobile \
  --output model.glb
```

The dry run reports the delivery type, member, compressed extent, and HTTP byte range.
The real operation writes a temporary output, verifies its declared identity, and then
commits it atomically. Existing outputs are refused.

Friendly quality terms map to the standard tiers:

| Input | VAO 0.4.0 tier |
| --- | --- |
| `preview` | `bootstrap` |
| `low` | `mobile` |
| `medium` | `production` |
| `high` | `production-spatial` |
| `full`, `archival` | `preservation` |

## Output modes

Global options precede the command:

```sh
vao --json inspect "$VAO_DOI" --assets
vao --quiet download "$VAO_DOI" --output-dir Downloads
vao --no-color doctor
```

- `--json` emits machine-readable JSON.
- `--quiet` suppresses phase and transfer feedback.
- `--no-color` removes ANSI colour while retaining readable status text.
- `--instance sandbox` selects Zenodo Sandbox for externally assigned DOI searches;
  native Sandbox DOI prefixes also rebind automatically.

Continue with the [command reference](command-reference.md),
[architecture](ARCHITECTURE.md), or [security model](security.md).
