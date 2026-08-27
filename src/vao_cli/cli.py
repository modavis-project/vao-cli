from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .cache import PersistentCache
from .catalog import DEFAULT_COMMUNITY, Catalog, default_catalog_path
from .compare import compare_carriers
from .doctor import run_doctor
from .download import download_vaos
from .errors import UnsupportedError, VAOCLIError
from .fetch import fetch_realization
from .group_fetch import fetch_group
from .local import (
    extract_local_realization,
    run_reference_validator,
    validate_local_carrier,
)
from .metadata import apply_metadata, edit_metadata, write_projection
from .models import INSTANCES
from .output import emit_json, error, human_size, table
from .publication import prepare_publication
from .resolver import VAOResolver
from .selection import SelectionConstraints, parse_byte_size, select_realizations
from .server import run_server
from .terminal import Terminal
from .vao import asset_rows, group_rows, manifest_summary
from .zenodo import ZenodoClient, record_publication_date, record_title


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vao",
        description="Resolve, inspect, stream, catalog, validate, and edit Virtual Acoustic Objects.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument("--instance", choices=sorted(INSTANCES), default="production")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="network timeout in seconds"
    )
    parser.add_argument("--catalog", type=Path, default=default_catalog_path())
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="disable persistent HTTP metadata/range caching",
    )
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument(
        "--quiet", action="store_true", help="suppress progress/status feedback"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    resolve = commands.add_parser(
        "resolve", help="resolve a DOI and list its Zenodo files"
    )
    resolve.add_argument("doi")
    resolve.add_argument("--exact", action="store_true", help="reject a concept DOI")

    inspect = commands.add_parser(
        "inspect", help="inspect a remote VAO without downloading it"
    )
    inspect.add_argument("doi")
    inspect.add_argument("--file", help="Zenodo .vao file key")
    inspect.add_argument(
        "--assets", action="store_true", help="show logical assets and realizations"
    )
    inspect.add_argument("--groups", action="store_true", help="show VAO asset groups")
    inspect.add_argument(
        "--archive", action="store_true", help="show remote carrier ZIP entries"
    )
    inspect.add_argument("--exact", action="store_true", help="reject a concept DOI")
    inspect.add_argument(
        "--no-conformance",
        action="store_true",
        help="skip the full VAO 0.4 reference check",
    )
    _standard_root_argument(inspect)

    select = commands.add_parser(
        "select", help="select realizations by VAO semantics and constraints"
    )
    select.add_argument("doi")
    select.add_argument("--file")
    _add_selection_arguments(select)
    select.add_argument(
        "--all", action="store_true", help="show every matching realization"
    )
    select.add_argument("--exact", action="store_true")
    select.add_argument("--no-conformance", action="store_true")
    _standard_root_argument(select)

    fetch = commands.add_parser(
        "fetch", help="selectively fetch and verify one VAO realization"
    )
    fetch.add_argument("doi")
    fetch.add_argument(
        "identifier", nargs="?", help="realization, logical asset, or asset-group ID"
    )
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--file", help="Zenodo .vao file key")
    fetch.add_argument(
        "--dry-run", action="store_true", help="show member and byte range only"
    )
    fetch.add_argument("--exact", action="store_true", help="reject a concept DOI")
    _add_selection_arguments(fetch)
    fetch.add_argument(
        "--chunks", help="verified chunk INDEX or START:STOP (STOP exclusive)"
    )
    fetch.add_argument("--no-conformance", action="store_true")
    _standard_root_argument(fetch)

    fetch_group_parser = commands.add_parser(
        "fetch-group", help="transactionally acquire an asset group and dependencies"
    )
    fetch_group_parser.add_argument("doi")
    fetch_group_parser.add_argument("group")
    fetch_group_parser.add_argument("--output-dir", type=Path, required=True)
    fetch_group_parser.add_argument("--file")
    fetch_group_parser.add_argument("--dry-run", action="store_true")
    fetch_group_parser.add_argument("--exact", action="store_true")
    fetch_group_parser.add_argument("--no-conformance", action="store_true")
    _standard_root_argument(fetch_group_parser)

    download = commands.add_parser(
        "download", help="download and verify complete VAO carriers"
    )
    download.add_argument("doi")
    download.add_argument("--output-dir", type=Path, default=Path.cwd())
    download.add_argument("--file", help="Zenodo .vao file key")
    download.add_argument(
        "--all", action="store_true", help="download every .vao on the record"
    )
    download.add_argument("--exact", action="store_true", help="reject a concept DOI")
    download.add_argument("--no-conformance", action="store_true")
    _standard_root_argument(download)

    relations = commands.add_parser(
        "relations", help="show Zenodo relations and version history"
    )
    relations.add_argument("doi")
    relations.add_argument("--versions", action="store_true")
    relations.add_argument("--exact", action="store_true", help="reject a concept DOI")

    validate = commands.add_parser("validate", help="validate a local VAO carrier")
    validate.add_argument("path", type=Path)
    validate.add_argument("--no-payloads", action="store_true")
    validate.add_argument(
        "--structural-only",
        "--no-conformance",
        dest="structural_only",
        action="store_true",
        help="run only bounded structural/integrity checks and make no conformance claim",
    )
    _standard_root_argument(validate)

    extract = commands.add_parser(
        "extract", help="extract and verify one realization from a local VAO"
    )
    extract.add_argument("input", type=Path)
    extract.add_argument("realization")
    extract.add_argument("--output", type=Path, required=True)

    compare = commands.add_parser(
        "compare", help="compare two local VAO releases without reading payload bytes"
    )
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)

    serve = commands.add_parser(
        "serve", help="run the local DOI resolver web/API service"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--cache-ttl", type=float, default=300.0)
    _standard_root_argument(serve)

    cache = commands.add_parser(
        "cache", help="inspect or maintain the persistent resolver cache"
    )
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    cache_commands.add_parser("stats")
    cache_commands.add_parser("prune")
    cache_commands.add_parser("clear")

    doctor = commands.add_parser(
        "doctor", help="diagnose the local VAO resolver environment"
    )
    doctor.add_argument(
        "--network", action="store_true", help="perform read-only Zenodo checks"
    )
    _standard_root_argument(doctor)

    publication = commands.add_parser(
        "publication", help="prepare offline publication artifacts"
    )
    publication_commands = publication.add_subparsers(
        dest="publication_command", required=True
    )
    publication_prepare = publication_commands.add_parser(
        "prepare", help="stage a validated VAO for later publication"
    )
    publication_prepare.add_argument("input", type=Path)
    publication_prepare.add_argument("--output", type=Path, required=True)
    publication_prepare.add_argument("--copy-carrier", action="store_true")
    _standard_root_argument(publication_prepare)

    community = commands.add_parser(
        "community", help="manage the moderated Zenodo community catalog"
    )
    community_commands = community.add_subparsers(
        dest="community_command", required=True
    )
    community_sync = community_commands.add_parser(
        "sync", help="synchronize community records"
    )
    community_sync.add_argument("--all-versions", action="store_true")
    community_list = community_commands.add_parser(
        "list", help="list locally cataloged VAOs"
    )
    community_list.add_argument("--status", choices=("new", "updated", "known"))
    community_list.add_argument("--all-versions", action="store_true")
    community_list.add_argument("--query", help="filter DOI, title, or version text")
    community_commands.add_parser("stats", help="summarize the local community index")
    community_ack = community_commands.add_parser(
        "acknowledge", help="mark notices as seen"
    )
    community_ack.add_argument("doi", nargs="?")

    metadata = commands.add_parser(
        "metadata", help="show or edit VAO 0.4 descriptive metadata"
    )
    metadata_commands = metadata.add_subparsers(dest="metadata_command", required=True)
    metadata_show = metadata_commands.add_parser(
        "show", help="export an editable metadata projection"
    )
    metadata_show.add_argument("input", type=Path)
    metadata_show.add_argument("--output", type=Path)
    metadata_apply = metadata_commands.add_parser(
        "apply", help="apply metadata and create a new VAO release"
    )
    metadata_apply.add_argument("input", type=Path)
    metadata_apply.add_argument("document", type=Path)
    metadata_apply.add_argument("--output", type=Path, required=True)
    _standard_root_argument(metadata_apply)
    metadata_edit = metadata_commands.add_parser(
        "edit", help="open an editor and create a new VAO release"
    )
    metadata_edit.add_argument("input", type=Path)
    metadata_edit.add_argument("--output", type=Path, required=True)
    metadata_edit.add_argument("--editor")
    _standard_root_argument(metadata_edit)

    return parser


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--asset", dest="asset_id")
    parser.add_argument("--group", dest="group_id")
    parser.add_argument(
        "--kind",
        choices=(
            "audio",
            "video",
            "geometry",
            "image",
            "document",
            "data",
            "event",
            "software",
            "other",
        ),
    )
    parser.add_argument(
        "--quality",
        help="bootstrap/mobile/production/production-spatial/preservation or preview/low/medium/high/full",
    )
    parser.add_argument(
        "--media-type", help="exact media type or wildcard such as audio/*"
    )
    parser.add_argument(
        "--max-bytes", type=parse_byte_size, help="maximum extent, e.g. 100MiB"
    )
    parser.add_argument("--capability")
    parser.add_argument("--profile")
    parser.add_argument("--prefer", choices=("best", "smallest"), default="best")


def _standard_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--standard-root",
        dest="standard_root",
        type=Path,
        metavar="STANDARD_ROOT",
        help="released VAO Standard 0.4.0 source checkout",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        terminal = Terminal(color=not args.no_color, quiet=args.quiet)
        if args.json or args.quiet or args.command == "serve":
            value = _dispatch(args)
        else:
            with terminal.phase(_phase_label(args)):
                value = _dispatch(args)
        if args.json:
            emit_json(value)
        else:
            _emit_human(args, value)
        if args.command == "validate":
            reference = value.get("referenceConformance")
            if not value.get("valid", False) or (
                isinstance(reference, dict) and not reference.get("valid", False)
            ):
                return 1
        if args.command == "doctor" and not value.get("healthy", False):
            return 1
        return 0
    except VAOCLIError as exc:
        if args.json:
            emit_json({"error": type(exc).__name__, "message": str(exc)})
        else:
            error(str(exc))
        return 1
    except KeyboardInterrupt:
        error("interrupted")
        return 130
    except BrokenPipeError:
        return 0


def _client(args: argparse.Namespace) -> ZenodoClient:
    cache = PersistentCache(args.catalog, enabled=not args.no_cache)
    return ZenodoClient.for_name(args.instance, timeout=args.timeout, cache=cache)


def _dispatch(args: argparse.Namespace) -> Any:
    if args.command == "resolve":
        client = _client(args)
        resolved = client.resolve(args.doi, allow_concept=not args.exact)
        client = client.for_resolved(resolved)
        metadata = (
            resolved.record.get("metadata")
            if isinstance(resolved.record.get("metadata"), dict)
            else {}
        )
        communities = client.record_communities(resolved)
        return {
            "requestedDOI": resolved.requested_doi,
            "resolvedDOI": resolved.resolved_doi,
            "conceptDOI": resolved.concept_doi,
            "requestedWasConcept": resolved.requested_was_concept,
            "recordId": resolved.record_id,
            "title": record_title(resolved.record),
            "publicationDate": record_publication_date(resolved.record),
            "version": metadata.get("version"),
            "instance": resolved.instance.identity,
            "files": [_file_dict(item) for item in client.files(resolved.record)],
            "communities": communities,
            "communityStatus": "curated"
            if DEFAULT_COMMUNITY in {str(item.get("slug")) for item in communities}
            else "not-listed",
        }
    if args.command == "inspect":
        inspection = VAOResolver(_client(args)).inspect(
            args.doi,
            file_key=args.file,
            allow_concept=not args.exact,
            full_conformance=not args.no_conformance,
            community_slug=DEFAULT_COMMUNITY,
            standard_root=args.standard_root,
        )
        return {
            "record": {
                "requestedDOI": inspection.resolved.requested_doi,
                "resolvedDOI": inspection.resolved.resolved_doi,
                "conceptDOI": inspection.resolved.concept_doi,
                "recordId": inspection.resolved.record_id,
                "title": record_title(inspection.resolved.record),
                "publicationDate": record_publication_date(inspection.resolved.record),
            },
            "selectedFile": _file_dict(inspection.selected_file)
            if inspection.selected_file
            else None,
            "summary": manifest_summary(inspection.manifest, inspection.carrier)
            if inspection.manifest
            else None,
            "assets": asset_rows(inspection.manifest, inspection.carrier)
            if args.assets and inspection.manifest
            else [],
            "groups": group_rows(inspection.manifest)
            if args.groups and inspection.manifest
            else [],
            "archiveEntries": inspection.archive_entries if args.archive else [],
            "releaseDescriptor": inspection.release_descriptor,
            "communities": inspection.communities,
            "communityStatus": inspection.community_status,
            "conformance": inspection.conformance,
            "warnings": inspection.warnings,
        }
    if args.command == "select":
        inspection = VAOResolver(_client(args)).inspect(
            args.doi,
            file_key=args.file,
            allow_concept=not args.exact,
            full_conformance=not args.no_conformance,
            standard_root=args.standard_root,
        )
        if inspection.manifest is None:
            raise VAOCLIError("No VAO manifest is available for semantic selection")
        matches = select_realizations(inspection.manifest, _constraints(args))
        if not args.all:
            matches = matches[:1]
        return {
            "requestedDOI": inspection.resolved.requested_doi,
            "resolvedDOI": inspection.resolved.resolved_doi,
            "constraints": vars(_constraints(args)),
            "matches": [
                {key: value for key, value in item.items() if key != "realization"}
                for item in matches
            ],
        }
    if args.command == "fetch":
        return fetch_realization(
            _client(args),
            args.doi,
            args.identifier,
            args.output,
            file_key=args.file,
            allow_concept=not args.exact,
            dry_run=args.dry_run,
            asset_id=args.asset_id,
            group_id=args.group_id,
            kind=args.kind,
            quality=args.quality,
            media_type=args.media_type,
            max_bytes=args.max_bytes,
            capability=args.capability,
            profile=args.profile,
            prefer=args.prefer,
            chunks=args.chunks,
            conformance=not args.no_conformance,
            standard_root=args.standard_root,
            progress=_transfer(args, "Acquiring realization"),
        )
    if args.command == "fetch-group":
        return fetch_group(
            _client(args),
            args.doi,
            args.group,
            args.output_dir,
            file_key=args.file,
            allow_concept=not args.exact,
            dry_run=args.dry_run,
            conformance=not args.no_conformance,
            standard_root=args.standard_root,
            progress=_transfer(args, "Acquiring group member"),
        )
    if args.command == "download":
        return download_vaos(
            _client(args),
            args.doi,
            args.output_dir,
            file_key=args.file,
            all_files=args.all,
            allow_concept=not args.exact,
            conformance=not args.no_conformance,
            standard_root=args.standard_root,
            progress=_transfer(args, "Downloading carrier"),
        )
    if args.command == "relations":
        client = _client(args)
        resolved = client.resolve(args.doi, allow_concept=not args.exact)
        client = client.for_resolved(resolved)
        result: dict[str, Any] = {
            "requestedDOI": resolved.requested_doi,
            "resolvedDOI": resolved.resolved_doi,
            "relations": [vars(item) for item in client.relations(resolved)],
        }
        if args.versions:
            result["versions"] = [
                {
                    "doi": item.get("doi"),
                    "recordId": item.get("id", item.get("recid")),
                    "title": record_title(item),
                    "publicationDate": record_publication_date(item),
                    "version": (item.get("metadata") or {}).get("version")
                    if isinstance(item.get("metadata"), dict)
                    else None,
                }
                for item in client.versions(resolved)
            ]
        return result
    if args.command == "validate":
        result = validate_local_carrier(args.path, verify_payloads=not args.no_payloads)
        if not args.structural_only:
            manifest = result.get("manifest")
            if result["valid"] and (
                not isinstance(manifest, dict)
                or manifest.get("formatVersion") != "0.4.0"
            ):
                raise UnsupportedError(
                    "Full reference conformance is available only for VAO 0.4.0; "
                    "use --structural-only only for explicitly limited legacy validation"
                )
            result["referenceConformance"] = run_reference_validator(
                args.path, standard_root=args.standard_root, required=True
            )
        result["path"] = str(args.path)
        return result
    if args.command == "extract":
        return extract_local_realization(args.input, args.realization, args.output)
    if args.command == "compare":
        return compare_carriers(args.left, args.right)
    if args.command == "serve":
        run_server(
            _client(args),
            host=args.host,
            port=args.port,
            catalog_path=args.catalog,
            cache_ttl=args.cache_ttl,
            standard_root=args.standard_root,
        )
        return {"stopped": True}
    if args.command == "cache":
        cache = PersistentCache(args.catalog, enabled=True)
        if args.cache_command == "stats":
            return cache.stats()
        if args.cache_command == "prune":
            return {"pruned": cache.prune(), **cache.stats()}
        return {"cleared": cache.clear(), **cache.stats()}
    if args.command == "doctor":
        cache = PersistentCache(args.catalog, enabled=not args.no_cache)
        return run_doctor(
            _client(args),
            cache,
            network=args.network,
            standard_root=args.standard_root,
        )
    if args.command == "publication":
        return prepare_publication(
            args.input,
            args.output,
            copy_carrier=args.copy_carrier,
            standard_root=args.standard_root,
        )
    if args.command == "community":
        with Catalog(args.catalog) as catalog:
            if args.community_command == "sync":
                return catalog.sync(
                    _client(args),
                    slug=DEFAULT_COMMUNITY,
                    all_versions=args.all_versions,
                )
            if args.community_command == "list":
                records = catalog.list(
                    status=args.status, all_versions=args.all_versions
                )
                if args.query:
                    query = args.query.casefold()
                    records = [
                        item
                        for item in records
                        if query
                        in " ".join(
                            str(item.get(key) or "")
                            for key in ("doi", "concept_doi", "version_label", "title")
                        ).casefold()
                    ]
                return {
                    "sync": catalog.sync_status(DEFAULT_COMMUNITY),
                    "records": records,
                    "catalog": str(catalog.path),
                }
            if args.community_command == "stats":
                return catalog.stats()
            return {
                "acknowledged": catalog.acknowledge(args.doi),
                "catalog": str(catalog.path),
            }
    if args.command == "metadata":
        if args.metadata_command == "show":
            return write_projection(args.input, args.output)
        if args.metadata_command == "apply":
            return apply_metadata(
                args.input,
                args.document,
                args.output,
                standard_root=args.standard_root,
            )
        return edit_metadata(
            args.input,
            args.output,
            editor=args.editor,
            standard_root=args.standard_root,
        )
    raise AssertionError(f"Unhandled command {args.command}")


def _constraints(args: argparse.Namespace) -> SelectionConstraints:
    return SelectionConstraints(
        identifier=getattr(args, "identifier", None),
        asset_id=getattr(args, "asset_id", None),
        group_id=getattr(args, "group_id", None),
        kind=getattr(args, "kind", None),
        quality=getattr(args, "quality", None),
        media_type=getattr(args, "media_type", None),
        max_bytes=getattr(args, "max_bytes", None),
        capability=getattr(args, "capability", None),
        profile=getattr(args, "profile", None),
        prefer=getattr(args, "prefer", "best"),
    )


def _file_dict(item) -> dict[str, Any]:
    return {
        "key": item.key,
        "byteSize": item.size,
        "checksum": item.checksum,
        "contentURL": item.content_url,
    }


def _emit_human(args: argparse.Namespace, value: Any) -> None:
    terminal = Terminal(color=not args.no_color, quiet=args.quiet)
    if args.command == "resolve":
        print(
            f"{terminal.heading(value['title'])}\nDOI: {value['resolvedDOI']}\nRecord: {value['recordId']}  Date: {value['publicationDate'] or '-'}"
        )
        status = value.get("communityStatus")
        print(
            terminal.success("Curated by the official VAO community")
            if status == "curated"
            else terminal.warning("Not currently listed in the official VAO community")
        )
        if value["requestedWasConcept"]:
            print(f"Requested concept DOI: {value['requestedDOI']}")
        rows = [
            dict(item, size=human_size(item["byteSize"])) for item in value["files"]
        ]
        print(
            "\n"
            + table(rows, [("key", "FILE"), ("size", "SIZE"), ("checksum", "CHECKSUM")])
        )
    elif args.command == "inspect":
        record, summary = value["record"], value["summary"]
        print(
            f"{terminal.heading(record['title'])}\nDOI: {record['resolvedDOI']}  Date: {record['publicationDate'] or '-'}"
        )
        status = value.get("communityStatus")
        print(
            terminal.success("Official VAO community: curated")
            if status == "curated"
            else terminal.warning(f"Official VAO community: {status}")
        )
        if value.get("conformance"):
            print(
                terminal.success("Full VAO 0.4 reference conformance")
                if value["conformance"]["valid"]
                else terminal.failure("VAO 0.4 conformance failed")
            )
        if summary:
            print(
                f"VAO {summary['formatVersion']}  {summary['title']}\n"
                f"Release: {summary['releaseId']}  Revision: {summary['revision']}  Version: {summary['contentVersion']}\n"
                f"Modalities: {', '.join(summary['modalities']) or '-'}\n"
                f"Assets: {summary['logicalAssetCount']}  Realizations: {summary['realizationCount']}  "
                f"Groups: {summary['assetGroupCount']}  Extent: {human_size(summary['totalRealizationBytes'])}"
            )
        else:
            print("No inspectable VAO 0.4/0.3.3 manifest was found.")
        if value["assets"]:
            rows = [
                dict(row, size=human_size(row.get("byteSize")))
                for row in value["assets"]
            ]
            print(
                "\nAssets\n"
                + table(
                    rows,
                    [
                        ("assetId", "ASSET"),
                        ("realizationId", "REALIZATION"),
                        ("kind", "KIND"),
                        ("quality", "QUALITY"),
                        ("size", "SIZE"),
                        ("embedded", "EMBEDDED"),
                    ],
                )
            )
        if value["groups"]:
            rows = [
                dict(row, size=human_size(row.get("byteSize")))
                for row in value["groups"]
            ]
            print(
                "\nGroups\n"
                + table(
                    rows,
                    [
                        ("id", "GROUP"),
                        ("quality", "QUALITY"),
                        ("availability", "AVAILABILITY"),
                        ("realizationCount", "ITEMS"),
                        ("size", "SIZE"),
                    ],
                )
            )
        if value["archiveEntries"]:
            rows = [
                dict(row, size=human_size(row.get("uncompressedSize")))
                for row in value["archiveEntries"]
            ]
            print(
                "\nCarrier entries\n"
                + table(
                    rows,
                    [("name", "ENTRY"), ("size", "SIZE"), ("compression", "METHOD")],
                )
            )
        for warning in value["warnings"]:
            print(terminal.warning(warning), file=sys.stderr)
    elif args.command == "select":
        print(terminal.heading(f"Semantic selection for {value['resolvedDOI']}"))
        rows = [
            dict(item, size=human_size(item.get("byteSize")))
            for item in value["matches"]
        ]
        print(
            table(
                rows,
                [
                    ("realizationId", "REALIZATION"),
                    ("kind", "KIND"),
                    ("quality", "QUALITY"),
                    ("mediaType", "MEDIA TYPE"),
                    ("size", "SIZE"),
                ],
            )
        )
        if rows:
            print(
                terminal.success(
                    f"Selected {len(rows)} matching realization{'s' if len(rows) != 1 else ''}"
                )
            )
    elif args.command == "fetch":
        verb = "Would fetch" if value["dryRun"] else "Fetched"
        print(
            f"{terminal.success(verb + ' ' + str(value['realizationId']))}\n"
            f"Delivery: {value.get('delivery')}  Carrier: {value['carrier']}  Member: {value['member']}\n"
            f"Range: bytes={value['rangeStart']}-{value['rangeEnd']}  "
            f"Output: {value['output']}  Size: {human_size(value['byteSize'])}"
        )
        if value.get("chunks"):
            print(terminal.info(f"Verified chunks: {value['chunks']}"))
    elif args.command == "fetch-group":
        verb = "Would acquire" if value["dryRun"] else "Acquired"
        print(terminal.heading(f"Asset group {value['groupId']}"))
        print(
            terminal.success(
                f"{verb} {value['realizationCount']} realizations "
                f"({human_size(value['totalByteSize'])})"
            )
        )
        rows = [
            {
                "realizationId": item["realizationId"],
                "delivery": item.get("delivery"),
                "size": human_size(item.get("byteSize")),
                "output": item["output"],
            }
            for item in value["realizations"]
        ]
        print(
            table(
                rows,
                [
                    ("realizationId", "REALIZATION"),
                    ("delivery", "DELIVERY"),
                    ("size", "SIZE"),
                    ("output", "OUTPUT"),
                ],
            )
        )
    elif args.command == "download":
        for item in value:
            print(
                f"Downloaded {item['file']} -> {item['output']} ({human_size(item['byteSize'])})"
            )
    elif args.command == "relations":
        print(
            table(
                value["relations"],
                [
                    ("relation", "RELATION"),
                    ("identifier", "IDENTIFIER"),
                    ("resource_type", "TYPE"),
                ],
            )
        )
        if value.get("versions") is not None:
            print(
                "\nVersions\n"
                + table(
                    value["versions"],
                    [
                        ("doi", "DOI"),
                        ("version", "VERSION"),
                        ("publicationDate", "DATE"),
                        ("title", "TITLE"),
                    ],
                )
            )
    elif args.command == "validate":
        print(
            (
                terminal.success("VALID")
                if value["valid"]
                else terminal.failure("INVALID")
            )
            + f": {value['path']}"
        )
        for message in value["errors"]:
            print(f"Error: {message}")
        print(f"Verified payload bytes: {human_size(value['verifiedPayloadBytes'])}")
        if args.structural_only:
            print(
                terminal.warning(
                    "Structural/integrity checks only; no VAO conformance claim"
                )
            )
        else:
            reference = value.get("referenceConformance")
            if reference["valid"]:
                print(terminal.success("VAO 0.4.0 reference conformance"))
            else:
                print(terminal.failure("VAO 0.4.0 reference conformance"))
                detail = reference.get("stderr") or reference.get("stdout")
                if detail:
                    print(detail)
    elif args.command == "extract":
        print(terminal.success(f"Extracted {value['realizationId']}"))
        print(
            f"Output: {value['output']}  Size: {human_size(value['byteSize'])}\n"
            f"SHA-256: {value['sha256']}"
        )
    elif args.command == "compare":
        print(terminal.heading("VAO release comparison"))
        print(
            f"{value['left']['contentVersion']} → {value['right']['contentVersion']}  "
            f"Same VAO: {value['sameVAO']}"
        )
        summary = value["summary"]
        print(
            f"Added: {summary['added']}  Removed: {summary['removed']}  "
            f"Changed: {summary['changed']}  Byte-identical: {summary['byteIdentical']}"
        )
        for key, label in (
            ("added", "+"),
            ("removed", "-"),
            ("changedDeclarations", "~"),
        ):
            for identifier in value["realizations"][key]:
                print(f"{label} {identifier}")
    elif args.command == "doctor":
        print(terminal.heading("VAO CLI environment diagnostics"))
        for check in value["checks"]:
            marker = (
                terminal.success(check["name"])
                if check["ok"]
                else terminal.failure(check["name"])
                if check["required"]
                else terminal.warning(check["name"])
            )
            print(f"{marker}: {check['detail']}")
        print(
            "\n"
            + terminal.bar(value["passed"], value["total"])
            + f"  {value['passed']}/{value['total']} checks"
        )
    elif args.command == "cache":
        print(terminal.heading("Persistent resolver cache"))
        if "cleared" in value:
            print(terminal.success(f"Cleared {value['cleared']} entries"))
        if "pruned" in value:
            print(terminal.success(f"Pruned {value['pruned']} expired entries"))
        print(
            f"Entries: {value['entries']}  Size: {human_size(value['bytes'])}  Hits: {value.get('hits', 0)}\nPath: {value['path']}"
        )
    elif args.command == "publication":
        print(terminal.heading("Offline publication staging"))
        print(terminal.success(f"Validated and staged at {value['destination']}"))
        print(
            f"Manifest: {value['manifestSHA256']}\nCarrier: {value['carrierSHA256']}\nVerified payloads: {human_size(value['verifiedPayloadBytes'])}"
        )
        print(terminal.warning("Live Zenodo identities remain intentionally pending"))
    elif args.command == "community":
        if args.community_command == "list":
            sync = value["sync"]
            if sync:
                print(
                    f"Community last synchronized: {sync['last_synced_at']} ({sync['remote_count']} current records)"
                )
            else:
                print("Community has not been synchronized; run `vao community sync`.")
            print(
                table(
                    value["records"],
                    [
                        ("status", "STATUS"),
                        ("presence", "COMMUNITY"),
                        ("doi", "DOI"),
                        ("version_label", "VERSION"),
                        ("publication_date", "DATE"),
                        ("title", "TITLE"),
                    ],
                )
            )
        elif args.community_command == "sync":
            print(terminal.heading(f"Community: {value['community']}"))
            print(
                terminal.success(
                    f"Indexed {value['storedVersionCount']} version records"
                )
            )
            print(
                f"New versions: {value['newVersionCount']}  "
                f"Updated concepts: {value['updatedConceptCount']}  "
                f"Date: {value['syncedAt']}"
            )
        elif args.community_command == "stats":
            print(terminal.heading("VAO community catalog"))
            print(
                f"Concepts: {value['concepts']}  Versions: {value['versions']}  "
                f"Listed: {value['listed']}  New: {value['new']}  Updated: {value['updated']}"
            )
            print(f"Catalog: {value['catalog']}")
        else:
            counts = value["acknowledged"]
            print(
                terminal.success(
                    f"Acknowledged {counts['concepts']} concepts and {counts['versions']} versions"
                )
            )
    elif (
        args.command == "metadata"
        and args.metadata_command == "show"
        and not args.output
    ):
        emit_json(value)
    elif args.command == "metadata":
        if args.metadata_command == "show":
            print(f"Wrote editable metadata to {args.output}")
        else:
            print(
                f"Created {value['output']} as release {value['newReleaseId']} (revision {value['revision']})"
            )
    else:
        emit_json(value)


def _phase_label(args: argparse.Namespace) -> str:
    labels = {
        "resolve": "Resolving DOI and record identity",
        "inspect": "Inspecting remote VAO structure",
        "select": "Evaluating semantic constraints",
        "fetch": "Planning and verifying selective acquisition",
        "fetch-group": "Planning and verifying asset-group acquisition",
        "download": "Downloading and validating carriers",
        "relations": "Resolving record relations",
        "validate": "Validating local VAO",
        "extract": "Extracting verified realization",
        "compare": "Comparing VAO releases",
        "community": "Updating community catalog",
        "metadata": "Processing VAO metadata",
        "publication": "Preparing offline publication package",
        "doctor": "Checking resolver environment",
        "cache": "Maintaining resolver cache",
    }
    return labels.get(args.command, f"Running {args.command}")


def _transfer(args: argparse.Namespace, label: str):
    if args.json:
        return None
    return Terminal(color=not args.no_color, quiet=args.quiet).transfer(label)


if __name__ == "__main__":
    raise SystemExit(main())
