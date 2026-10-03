"""Persistent inspection evidence and portable, version-specific compatibility records."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time

PATH = (
    Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "drape"
    / "compatibility.sqlite3"
)
RULES_VERSION = 1
FORMAT = "drape-compatibility/v1"


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
        return connection

    @staticmethod
    def revision(item, file):
        return "md5:" + file.md5.lower() if file.md5 else "modified:" + item.changed

    def inspection(self, item, file):
        with closing(self.connect()) as db:
            rows = db.execute(
                """SELECT parts, complete, checked FROM inspections
                WHERE item=? AND filename=? AND revision=? AND rules=?
                ORDER BY CASE origin WHEN 'local' THEN 0 ELSE 1 END""",
                (item.id, file.name, self.revision(item, file), RULES_VERSION),
            ).fetchall()
        for parts, complete, checked in rows:
            parsed = set(json.loads(parts))
            if not parsed and not complete:
                continue  # Legacy transport failures were stored as empty evidence.
            # Partial/failed inspection gets another opportunity soon; durable evidence
            # is reused for a week, and changed checksums always require a fresh record.
            lifetime = 7 * 86400 if complete else 600
            if time.time() - checked < lifetime:
                return parsed, bool(complete)
        return None

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
