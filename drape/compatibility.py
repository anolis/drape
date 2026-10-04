"""Persistent inspection evidence and portable, version-specific compatibility records."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

import requests

PATH = (
    Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "drape"
    / "compatibility.sqlite3"
)
# Kvantum file-pair detection changes archive evidence; older listings need rescanning.
RULES_VERSION = 3
FORMAT = "drape-compatibility/v1"


def fetch_snapshot(url, index=None):
    """Explicitly download one public snapshot; browsing never calls this."""
    from . import http

    if urlsplit(url).scheme != "https":
        raise ValueError("Compatibility snapshot URL must use HTTPS")
    limit = 16 * 1024 * 1024
    try:
        with http.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(data) + len(chunk) > limit:
                    raise ValueError("Compatibility snapshot exceeds 16 MiB")
                data.extend(chunk)
        return (index or Index()).import_records(json.loads(data))
    except requests.RequestException as exc:
        raise ValueError("Could not download the compatibility snapshot") from exc


class Index:
    def __init__(self, path=None):
        self.path = Path(path or PATH)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS inspections (
            item TEXT, filename TEXT, revision TEXT, origin TEXT,
            parts TEXT, complete INTEGER, checked REAL, rules INTEGER,
            PRIMARY KEY (item, filename, revision, origin))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS observations (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT UNIQUE, record TEXT)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS installed_evidence (
            item TEXT PRIMARY KEY, filename TEXT, changed TEXT, md5 TEXT,
            parts TEXT, rules INTEGER)""")
        return connection

    def record_installed(self, item_id, entry, parts):
        """Installed subsets are local-only evidence and never prove archive completeness."""
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO installed_evidence VALUES (?,?,?,?,?,?)",
                (
                    item_id,
                    entry.get("file", ""),
                    entry.get("changed", ""),
                    entry.get("download_md5", ""),
                    json.dumps(sorted(parts)),
                    RULES_VERSION,
                ),
            )

    @staticmethod
    def _installed(db, item, file):
        try:
            row = db.execute(
                "SELECT filename,changed,md5,parts,rules FROM installed_evidence WHERE item=?",
                (item.id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # Databases made before local installed evidence was supported.
        if row is None:
            return None
        filename, changed, checksum, parts, rules = row
        if filename != file.name or changed != item.changed or rules != RULES_VERSION:
            return None
        if checksum and checksum.lower() != file.md5.lower():
            return None
        if not checksum and not changed:
            return None  # No identity to match an old installation to a catalog revision.
        parsed = set(json.loads(parts))
        return (parsed, False) if parsed else None

    def read_many(self, items):
        """Read one snapshot without creating a DB, writing records or opening archives."""
        results = {item.id: {} for item in items}
        if not self.path.exists():
            return results
        try:
            with closing(
                sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
            ) as db:
                db.execute("BEGIN")
                now = time.time()
                for item in items:
                    for file in item.files:
                        rows = db.execute(
                            "SELECT parts,complete,checked,rules FROM inspections WHERE item=? AND filename=? AND revision=? AND rules IN (1,?) ORDER BY CASE origin WHEN 'local' THEN 0 ELSE 1 END",
                            (item.id, file.name, self.revision(item, file), RULES_VERSION),
                        ).fetchall()
                        for parts, complete, checked, rules in rows:
                            parsed = set(json.loads(parts))
                            if not parsed and not complete and rules < 2:
                                continue
                            if complete or now - checked < 86400:
                                results[item.id][file.index] = (parsed, bool(complete))
                                break
                        if file.index not in results[item.id]:
                            local = self._installed(db, item, file)
                            if local is not None:
                                results[item.id][file.index] = local
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            print(f"drape: compatibility index unavailable: {exc}", file=sys.stderr)
            return {item.id: {} for item in items}
        return results

    @staticmethod
    def revision(item, file):
        return "md5:" + file.md5.lower() if file.md5 else "modified:" + item.changed

    def inspection(self, item, file):
        with closing(self.connect()) as db:
            # v2 distinguishes genuine empty listings from legacy failed requests.
            # Positive v1 evidence can still be evaluated by the current desktop rules.
            rows = db.execute(
                """SELECT parts, complete, checked, rules FROM inspections
                WHERE item=? AND filename=? AND revision=? AND rules IN (1, ?)
                ORDER BY CASE origin WHEN 'local' THEN 0 ELSE 1 END""",
                (item.id, file.name, self.revision(item, file), RULES_VERSION),
            ).fetchall()
            local = self._installed(db, item, file)
        for parts, complete, checked, rules in rows:
            parsed = set(json.loads(parts))
            if not parsed and not complete and rules < 2:
                continue  # Legacy transport failures were stored as empty evidence.
            # Partial inspection gets another opportunity soon; durable evidence
            # survives relaunches indefinitely; revisions and rules invalidate it.
            if complete or time.time() - checked < 86400:
                return parsed, bool(complete)
        return local

    def record(self, item, file, result, kind, context, status, basis="archive"):
        parts, complete = result
        record = dict(
            item=item.id,
            filename=file.name,
            revision=self.revision(item, file),
            kind=kind,
            context=context,
            status=status,
            parts=sorted(parts),
            complete=bool(complete),
            rules=RULES_VERSION,
            basis=basis,
        )
        self._store(record, "local")

    def _store(self, record, origin):
        identity = json.dumps(
            [record[k] for k in ("item", "filename", "revision", "kind", "context")]
            + [record.get("basis", "archive")],
            sort_keys=True,
        )
        payload = json.dumps(record, sort_keys=True)
        with closing(self.connect()) as db, db:
            if record.get("basis", "archive") == "archive":
                self._store_inspection(db, record, origin)
            if origin == "local":
                previous = db.execute(
                    "SELECT record FROM observations WHERE identity=?", (identity,)
                ).fetchone()
                if previous is None or previous[0] != payload:
                    # Only actual changes advance the diff cursor. Imported observations
                    # are never echoed back as local contributions.
                    db.execute("DELETE FROM observations WHERE identity=?", (identity,))
                    db.execute(
                        "INSERT INTO observations(identity,record) VALUES (?,?)",
                        (identity, payload),
                    )

    def _store_inspection(self, db, record, origin):
        db.execute(
            """INSERT INTO inspections VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(item,filename,revision,origin) DO UPDATE SET
                parts=excluded.parts,complete=excluded.complete,checked=excluded.checked,rules=excluded.rules""",
            (
                record["item"],
                record["filename"],
                record["revision"],
                origin,
                json.dumps(record["parts"]),
                record["complete"],
                time.time(),
                record["rules"],
            ),
        )

    def export(self, since=0):
        with closing(self.connect()) as db:
            # Keep records and the cursor in one read snapshot during concurrent scans.
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT sequence,record FROM observations WHERE sequence>? ORDER BY sequence",
                (since,),
            ).fetchall()
            cursor = db.execute("SELECT COALESCE(MAX(sequence),0) FROM observations").fetchone()[0]
        return dict(
            format=FORMAT, cursor=cursor, observations=[json.loads(record) for _, record in rows]
        )

    def import_records(self, bundle):
        if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
            raise ValueError("Unsupported compatibility index format")
        records = bundle.get("observations")
        if not isinstance(records, list) or len(records) > 100000:
            raise ValueError("Invalid compatibility observations")
        # Validate the whole document before starting any writes. Imported evidence
        # is re-evaluated by local rules; it never supplies an executable rule or URL.
        for record in records:
            if (
                not isinstance(record, dict)
                or record.get("rules") != RULES_VERSION
                or any(
                    not isinstance(record.get(key), str)
                    for key in ("item", "filename", "revision", "kind", "status")
                )
                or record["status"] not in {"compatible", "incompatible", "unknown"}
                or not isinstance(record.get("context"), dict)
                or not isinstance(record.get("complete"), bool)
                or not isinstance(record.get("parts"), list)
                or any(not isinstance(part, str) for part in record["parts"])
                or record.get("basis", "archive") not in {"archive", "installed-components"}
            ):
                raise ValueError("Invalid compatibility record")
        with closing(self.connect()) as db, db:
            for record in records:
                if record.get("basis", "archive") == "archive":
                    self._store_inspection(db, record, "community")
        return len(records)


def context():
    from . import desktop, kde, system

    session = desktop.current_desktop()
    return dict(
        desktop=session,
        window_manager=desktop.running_wm(),
        borders=desktop.border_part(),
        cinnamon=desktop.cinnamon_version() if session == "cinnamon" else None,
        plasma=kde.major_version() if session == "kde" else None,
        display_manager=system.display_manager(),
        greeter=system.lightdm_greeter(),
    )
