---
layout: page
title: Command reference
description: Complete reference for VAO CLI global options, commands, subcommands, selection constraints, and output behavior.
permalink: /commands/
---

Syntax:

```text
vao [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

Global options must appear before the command.

## Global options

| Option | Default | Meaning |
| --- | --- | --- |
| `--version` | — | Print the installed CLI version. |
| `--json` | off | Emit machine-readable JSON and suppress progress output. |
| `--instance production\|sandbox` | `production` | Zenodo instance for searches and non-native DOI prefixes. Native Zenodo DOI prefixes rebind safely. |
| `--timeout SECONDS` | `30` | Per-request network timeout. |
| `--catalog PATH` | platform data directory | SQLite catalog and persistent HTTP/range cache. |
| `--no-cache` | off | Disable persistent HTTP metadata and range caching for the command. |
| `--no-color` | off | Disable ANSI colors. |
| `--quiet` | off | Suppress phase and transfer feedback. |

The default catalog is `~/.local/share/vao-cli/catalog.sqlite3`. `VAO_CLI_HOME`
changes the application data directory; otherwise `XDG_DATA_HOME` is respected.

## DOI and discovery commands

### `resolve`

```text
vao resolve DOI [--exact]
```

Resolves a DOI, displays exact and concept identity, record ID, title, publication date,
files, and official-community status. `--exact` rejects concept DOI input.

### `inspect`

```text
vao inspect DOI [--file KEY] [--assets] [--groups] [--archive]
                [--exact] [--no-conformance]
                [--standard-root PATH]
```

Inspects a standalone manifest or range-indexes a `.vao` carrier. `--assets`, `--groups`,
and `--archive` add detailed tables. Version-matched manifest conformance runs by
default. `--standard-root` points to a VAO Standard checkout containing that version.
Community status
is checked against `virtual-acoustic-objects`.

### `relations`

```text
vao relations DOI [--versions] [--exact]
```

Shows repository relations. `--versions` also lists version DOI, version label,
publication date, and title for the version chain.

## Semantic selection

### `select`

```text
vao select DOI [--file KEY] [SELECTION OPTIONS] [--all] [--exact]
               [--no-conformance] [--standard-root PATH]
```

Returns the highest-ranked matching realization, or every match with `--all`.
Full manifest conformance runs before semantic interpretation unless explicitly
disabled. Equal scores use realization identifier order as a deterministic tie-break.

### Shared selection options

| Option | Meaning |
| --- | --- |
| `--asset ID` | Restrict to a logical asset. |
| `--group ID` | Restrict to an asset group and its dependency closure. |
| `--kind KIND` | `audio`, `video`, `geometry`, `image`, `document`, `data`, `event`, `software`, or `other`. |
| `--quality TIER` | VAO tier or `preview`, `low`, `medium`, `high`, `full`, or `archival` alias. |
| `--media-type TYPE` | Exact media type or wildcard such as `audio/*`. |
| `--max-bytes SIZE` | Maximum extent as bytes or decimal/binary suffix, such as `100MB` or `100MiB`. |
| `--capability ID` | Restrict to groups requiring a capability. |
| `--profile ID` | Restrict to groups materializing a profile. |
| `--prefer best\|smallest` | Rank by highest quality (default) or smallest extent. |

`best` is a client policy, not a normative VAO ordering. It ranks the named tiers as
preservation, production-spatial, production, mobile, then bootstrap, and uses the
smaller extent as a tie-breaker. `custom` has no standard ordinal meaning and is ranked
with production only for deterministic selection; use `--quality custom` when it must
be selected explicitly. The result always reports the exact declared tier and is never
relabeled through an alias.

## Acquisition commands

### `fetch`

```text
vao fetch DOI [IDENTIFIER] --output PATH [--file KEY] [--dry-run]
              [--exact] [SELECTION OPTIONS] [--chunks INDEX|START:STOP]
              [--no-conformance] [--standard-root PATH]
```

Fetches one realization by direct realization/logical-asset/asset-group identifier or by
semantic constraints. `--dry-run` returns the delivery plan without writing. `--chunks`
retrieves an independently verified inline chunk or half-open chunk-index range and is
available only for stored or raw repository delivery.

The output is staged, checked, and committed atomically. Existing targets are refused.

### `fetch-group`

```text
vao fetch-group DOI GROUP --output-dir PATH [--file KEY] [--dry-run] [--exact]
                [--no-conformance] [--standard-root PATH]
```

Plans or transactionally materializes all realizations in an asset group and its declared
group dependencies. The destination directory must not exist.

### `download`

```text
vao download DOI [--output-dir PATH] [--file KEY] [--all | --complete] [--exact]
                 [--no-conformance] [--standard-root PATH]
```

Downloads complete `.vao` carriers. With neither `--file`, `--all`, nor `--complete`,
the bootstrap carrier declared by `vao-release.json` is selected. `--complete` selects
the declared preservation-closure carrier. Checks include record size, Zenodo MD5 when
present, release SHA-256 when inventoried, complete embedded realization integrity, and
version-matched VAO reference conformance unless disabled.

### `materialize`

```text
vao materialize DOI --output PATH
    [--realization ID ...] [--group ID ...] [--all]
    [--asset ID] [--kind KIND] [--quality TIER] [--media-type TYPE]
    [--max-bytes SIZE] [--capability ID] [--profile ID]
    [--prefer best|smallest] [--file KEY] [--dry-run] [--exact]
    [--no-conformance] [--standard-root PATH]
```

Creates an immutable custom `.vao` carrier from any union of explicit realizations,
asset-group closures, or semantic filters. `--all` requests every realization. The
command retrieves only selected payload members, verifies each against the manifest,
embeds the exact published manifest bytes, and validates the completed carrier before
committing the output path. `--dry-run` reports the planned population and extent
without creating or downloading payload files.

## Local carrier commands

### `validate`

```text
vao validate PATH [--no-payloads] [--structural-only|--no-conformance]
                  [--standard-root PATH]
```

Validates the local carrier. Payload verification and version-matched reference conformance
are on by default. `--structural-only` (alias `--no-conformance`) runs only bounded
local structure and integrity checks and makes no VAO conformance claim. `--no-payloads`
skips payload hashing in the local layer; unless structural-only mode is also selected,
the reference validator still determines conformance.

### `extract`

```text
vao extract INPUT REALIZATION --output PATH
```

Extracts one embedded realization and verifies exact size and SHA-256 before committing.

### `compare`

```text
vao compare LEFT RIGHT
```

Compares two carriers without reading payload bytes. Reports VAO/release identity and
added, removed, declaration-changed, unchanged, and byte-identical realizations.

## Community catalog

```text
vao community sync [--all-versions]
vao community list [--status new|updated|known] [--all-versions]
                   [--query TEXT]
vao community stats
vao community acknowledge [DOI]
```

- `sync` reads the moderated community and updates the local catalog.
- `list` reports dates, versions, notice state, and current community presence.
- `stats` summarizes concepts, versions, listed concepts, and notices.
- `acknowledge` marks one DOI family or every notice as known.

Synchronization is read-only and paginated with a safety limit.

## Persistent cache

```text
vao cache stats
vao cache prune
vao cache clear
```

`stats` reports entry count, byte extent, hits, and timestamps. `prune` removes expired
entries. `clear` removes all HTTP cache rows but leaves community catalog tables intact.

## Metadata commands

```text
vao metadata show INPUT [--output PATH]
vao metadata apply INPUT DOCUMENT --output PATH [--standard-root PATH]
vao metadata edit INPUT --output PATH [--editor COMMAND]
                  [--standard-root PATH]
```

`show` emits or writes the editable descriptive projection. `apply` creates a new local
release from a reviewed projection. `edit` opens `$VISUAL`, `$EDITOR`, or the supplied
editor, then performs the same revisioned application. Scientific/runtime content not in
the projection is preserved.

## Publication preparation

```text
vao publication prepare INPUT [INPUT ...] --output DIRECTORY [--copy-carrier]
                        [--readme README.pdf] [--standard-root PATH]
```

Creates a local staging directory containing a standalone manifest, `SHA256SUMS`, a
release template, Zenodo metadata projection, and readiness report. VAO 0.5 staging
requires exactly one bootstrap and one preservation-closure input; `--readme` adds the
reviewed record guide under the fixed `README.pdf` name. The directory must be empty or
absent. Repository identities and rights remain pending, so
`readyForLivePublication` is deliberately false. No Zenodo request is made.

## Diagnostics and service

### `doctor`

```text
vao doctor [--network] [--standard-root PATH]
```

Checks Python, cache access, the exact standard version, schemas, and the reference
validator runtime. `--network` adds read-only Zenodo API and community checks. Missing
required checks produce a non-zero exit status.

### `serve`

```text
vao serve [--host ADDRESS] [--port PORT] [--cache-ttl SECONDS]
          [--standard-root PATH]
```

Runs the loopback-only HTML/JSON resolver. Defaults: `127.0.0.1:8765`, five-minute
discovery cache. Non-loopback bind addresses are refused. See the
[resolver API](resolver-api.md) and [security model](security.md).

## Exit behavior

- `0`: command completed and required validation/diagnostics passed.
- `1`: expected resolution, network, integrity, validation, or configuration failure.
- `130`: interrupted with Ctrl-C.

With `--json`, expected failures are JSON objects with `error` and `message` fields.
