from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .doi import normalize_doi
from .zenodo import (
    ZenodoClient,
    _concept_doi,
    _record_doi,
    record_id,
    record_publication_date,
    record_title,
)

DEFAULT_COMMUNITY = "virtual-acoustic-objects"


def default_catalog_path() -> Path:
    configured = os.environ.get("VAO_CLI_HOME")
    if configured:
        return Path(configured).expanduser() / "catalog.sqlite3"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "vao-cli" / "catalog.sqlite3"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Catalog:
    def __init__(self, path: Path | None = None):
        self.path = path or default_catalog_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS concepts (
              concept_doi TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              latest_doi TEXT NOT NULL,
              latest_record_id TEXT NOT NULL,
              latest_publication_date TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('new','updated','known'))
            );
            CREATE TABLE IF NOT EXISTS versions (
              version_doi TEXT PRIMARY KEY,
              concept_doi TEXT NOT NULL REFERENCES concepts(concept_doi) ON DELETE CASCADE,
              record_id TEXT NOT NULL,
              title TEXT NOT NULL,
              version_label TEXT,
              publication_date TEXT,
              created TEXT,
              updated TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('new','updated','known')),
              raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS versions_concept ON versions(concept_doi, publication_date);
            CREATE TABLE IF NOT EXISTS community_sync (
              slug TEXT PRIMARY KEY,
              last_synced_at TEXT NOT NULL,
              remote_count INTEGER NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(concepts)")}
        if "presence" not in columns:
            self.db.execute(
                "ALTER TABLE concepts ADD COLUMN presence TEXT NOT NULL DEFAULT 'listed'"
            )
        self.db.commit()

    def sync(
        self,
        client: ZenodoClient,
        *,
        slug: str = DEFAULT_COMMUNITY,
        all_versions: bool = False,
    ) -> dict[str, Any]:
        records = client.community_records(slug)
        expanded: dict[str, dict[str, Any]] = {}
        for record in records:
            expanded[_record_doi(record)] = record
            if all_versions:
                resolved = client.resolve(_record_doi(record))
                for version in client.versions(resolved):
                    expanded[_record_doi(version)] = version
        values = [item for key, item in expanded.items() if key]
        when = _now()
        new_versions = 0
        updated_concepts: set[str] = set()
        seen_concepts: set[str] = set()
        with self.db:
            self.db.execute("UPDATE concepts SET presence='not-listed'")
            for record in values:
                version_doi = _record_doi(record)
                concept_doi = _concept_doi(record) or version_doi
                seen_concepts.add(concept_doi)
                old_concept = self.db.execute(
                    "SELECT latest_doi FROM concepts WHERE concept_doi = ?",
                    (concept_doi,),
                ).fetchone()
                old_version = self.db.execute(
                    "SELECT version_doi FROM versions WHERE version_doi = ?",
                    (version_doi,),
                ).fetchone()
                if old_concept is None:
                    concept_status = "new"
                elif old_concept["latest_doi"] != version_doi and old_version is None:
                    concept_status = "updated"
                    updated_concepts.add(concept_doi)
                else:
                    concept_status = self.db.execute(
                        "SELECT status FROM concepts WHERE concept_doi = ?",
                        (concept_doi,),
                    ).fetchone()["status"]
                if old_version is None:
                    new_versions += 1
                    version_status = "new" if old_concept is None else "updated"
                else:
                    version_status = self.db.execute(
                        "SELECT status FROM versions WHERE version_doi = ?",
                        (version_doi,),
                    ).fetchone()["status"]
                metadata = (
                    record.get("metadata")
                    if isinstance(record.get("metadata"), dict)
                    else {}
                )
                publication_date = record_publication_date(record)
                title = record_title(record)
                record_identifier = record_id(record)
                self.db.execute(
                    """
                    INSERT INTO concepts (
                      concept_doi, title, latest_doi, latest_record_id,
                      latest_publication_date, first_seen_at, last_seen_at, status, presence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'listed')
                    ON CONFLICT(concept_doi) DO UPDATE SET
                      title=excluded.title,
                      latest_doi=CASE
                        WHEN COALESCE(excluded.latest_publication_date, '') >= COALESCE(concepts.latest_publication_date, '')
                        THEN excluded.latest_doi ELSE concepts.latest_doi END,
                      latest_record_id=CASE
                        WHEN COALESCE(excluded.latest_publication_date, '') >= COALESCE(concepts.latest_publication_date, '')
                        THEN excluded.latest_record_id ELSE concepts.latest_record_id END,
                      latest_publication_date=MAX(COALESCE(concepts.latest_publication_date, ''), COALESCE(excluded.latest_publication_date, '')),
                      last_seen_at=excluded.last_seen_at,
                      presence='listed',
                      status=CASE WHEN concepts.status='known' THEN excluded.status ELSE concepts.status END
                    """,
                    (
                        concept_doi,
                        title,
                        version_doi,
                        record_identifier,
                        publication_date,
                        when,
                        when,
                        concept_status,
                    ),
                )
                self.db.execute(
                    """
                    INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(version_doi) DO UPDATE SET
                      title=excluded.title,
                      version_label=excluded.version_label,
                      publication_date=excluded.publication_date,
                      created=excluded.created,
                      updated=excluded.updated,
                      last_seen_at=excluded.last_seen_at,
                      raw_json=excluded.raw_json
                    """,
                    (
                        version_doi,
                        concept_doi,
                        record_identifier,
                        title,
                        str(metadata.get("version"))
                        if metadata.get("version") is not None
                        else None,
                        publication_date,
                        record.get("created"),
                        record.get("updated"),
                        when,
                        when,
                        version_status,
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            self.db.execute(
                """INSERT INTO community_sync VALUES (?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET
                     last_synced_at=excluded.last_synced_at, remote_count=excluded.remote_count""",
                (slug, when, len(records)),
            )
        return {
            "community": slug,
            "syncedAt": when,
            "remoteRecordCount": len(records),
            "storedVersionCount": len(values),
            "newVersionCount": new_versions,
            "updatedConceptCount": len(updated_concepts),
            "catalog": str(self.path),
        }

    def list(
        self, *, status: str | None = None, all_versions: bool = False
    ) -> list[dict[str, Any]]:
        parameters: tuple[str, ...] = (status,) if status else ()
        condition = "WHERE status = ?" if status else ""
        if all_versions:
            rows = self.db.execute(
                f"""SELECT versions.version_doi AS doi, versions.concept_doi, record_id,
                           versions.title, version_label, publication_date, created, updated,
                           versions.status, concepts.presence
                    FROM versions JOIN concepts USING(concept_doi)
                    {condition.replace("status", "versions.status")}
                    ORDER BY COALESCE(publication_date, created) DESC, version_doi""",
                parameters,
            ).fetchall()
        else:
            rows = self.db.execute(
                f"""SELECT latest_doi AS doi, concept_doi, latest_record_id AS record_id,
                           title, NULL AS version_label, latest_publication_date AS publication_date,
                           NULL AS created, NULL AS updated, status, presence
                    FROM concepts {condition}
                    ORDER BY COALESCE(latest_publication_date, '') DESC, concept_doi""",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def acknowledge(self, doi: str | None = None) -> dict[str, int]:
        with self.db:
            if doi is None:
                versions = self.db.execute(
                    "UPDATE versions SET status='known' WHERE status!='known'"
                ).rowcount
                concepts = self.db.execute(
                    "UPDATE concepts SET status='known' WHERE status!='known'"
                ).rowcount
            else:
                normalized = normalize_doi(doi)
                concept = self.db.execute(
                    "SELECT concept_doi FROM versions WHERE version_doi=? UNION SELECT concept_doi FROM concepts WHERE concept_doi=?",
                    (normalized, normalized),
                ).fetchone()
                if concept is None:
                    return {"concepts": 0, "versions": 0}
                concept_doi = concept["concept_doi"]
                concepts = self.db.execute(
                    "UPDATE concepts SET status='known' WHERE concept_doi=?",
                    (concept_doi,),
                ).rowcount
                versions = self.db.execute(
                    "UPDATE versions SET status='known' WHERE concept_doi=?",
                    (concept_doi,),
                ).rowcount
        return {"concepts": concepts, "versions": versions}

    def sync_status(self, slug: str = DEFAULT_COMMUNITY) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM community_sync WHERE slug=?", (slug,)
        ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict[str, Any]:
        row = self.db.execute(
            """SELECT COUNT(*) AS concepts,
                      COALESCE(SUM(CASE WHEN presence='listed' THEN 1 ELSE 0 END), 0) AS listed,
                      COALESCE(SUM(CASE WHEN status='new' THEN 1 ELSE 0 END), 0) AS new,
                      COALESCE(SUM(CASE WHEN status='updated' THEN 1 ELSE 0 END), 0) AS updated
               FROM concepts"""
        ).fetchone()
        versions = self.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        return {**dict(row), "versions": versions, "catalog": str(self.path)}
