from dataclasses import dataclass
from surface.results.codec import encode_payload, decode_payload, canonical_digest
from surface.mesh.model import OrdinaryMesh, StitchedMesh
from surface.sources.field_input import normalize_fields

STITCH_NAMES = {"mesh", "fields", "stitch"}


class ConflictError(ValueError):
    pass


def revision_fingerprint(content):
    return canonical_digest(content)


@dataclass(frozen=True)
class SourceRevision:
    revision_id: int
    fingerprint: str
    mesh: object
    fields: object
    stitch: object = None

    def to_record(self):
        record = {"revision_id": self.revision_id, "fingerprint": self.fingerprint,
                  "mesh": self.mesh.to_record(), "fields": self.fields.to_record()}
        if self.stitch is not None:
            record["stitch"] = _copy_stitch(self.stitch)
        return record


def _copy_stitch(stitch):
    def convert(value):
        if type(value) is dict:
            return {key: convert(item) for key, item in value.items()}
        if type(value) is list:
            return [convert(item) for item in value]
        return value
    return convert(stitch)


class RevisionRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def initialize(store):
        statements = [
            "CREATE TABLE IF NOT EXISTS source_revisions (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL, payload BLOB NOT NULL)",
            "CREATE TABLE IF NOT EXISTS source_requests (request_id TEXT PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES source_revisions(id))",
            "CREATE TABLE IF NOT EXISTS source_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision_id INTEGER NOT NULL REFERENCES source_revisions(id))"]
        for statement in statements:
            store.connection.execute(statement)

    def lookup_request(self, request_id, content):
        row = self.store.connection.execute(
            "SELECT r.id,r.fingerprint,r.payload FROM source_requests q JOIN source_revisions r ON r.id=q.revision_id WHERE q.request_id=?",
            (request_id,)).fetchone()
        if row is None:
            return None
        prior = self.read(row[0])
        names = STITCH_NAMES if prior.stitch is not None else {"mesh", "fields"}
        if set(content) != names:
            raise ConflictError("source request bound to different complete content")
        if encode_payload(_content_snapshot(prior)) != encode_payload(content):
            raise ConflictError("source request bound to different complete content")
        return {"revision_id": row[0], "fingerprint": row[1]}

    def append(self, request_id, content):
        self.store.require_transaction()
        existing = self.lookup_request(request_id, content)
        if existing is not None:
            return existing
        fingerprint = revision_fingerprint(content)
        cursor = self.store.connection.execute(
            "INSERT INTO source_revisions(fingerprint,payload) VALUES(?,?)",
            (fingerprint, encode_payload(content)))
        revision_id = cursor.lastrowid
        self.store.checkpoint("source_after_revision")
        self.store.connection.execute("INSERT INTO source_requests VALUES(?,?)",
                                      (request_id, revision_id))
        self.store.checkpoint("source_after_request")
        self.store.connection.execute(
            "INSERT INTO source_head VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET revision_id=excluded.revision_id",
            (revision_id,))
        self.store.checkpoint("source_after_head")
        return {"revision_id": revision_id, "fingerprint": fingerprint}

    def append_stitched(self, content):
        self.store.require_transaction()
        fingerprint = revision_fingerprint(content)
        cursor = self.store.connection.execute(
            "INSERT INTO source_revisions(fingerprint,payload) VALUES(?,?)",
            (fingerprint, encode_payload(content)))
        revision_id = cursor.lastrowid
        self.store.checkpoint("source_after_revision")
        self.store.connection.execute(
            "INSERT INTO source_head VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET revision_id=excluded.revision_id",
            (revision_id,))
        self.store.checkpoint("source_after_head")
        return {"revision_id": revision_id, "fingerprint": fingerprint}

    def read(self, revision_id):
        if type(revision_id) is not int or revision_id < 1:
            raise KeyError(revision_id)
        row = self.store.connection.execute(
            "SELECT fingerprint,payload FROM source_revisions WHERE id=?",
            (revision_id,)).fetchone()
        if row is None:
            raise KeyError(revision_id)
        try:
            content = decode_payload(row[1])
            if type(content) is not dict or revision_fingerprint(content) != row[0]:
                raise ValueError("source digest mismatch")
            if set(content) == STITCH_NAMES:
                mesh = StitchedMesh.from_record(content["mesh"], content["stitch"])
                fields = normalize_fields(mesh, content["fields"])
                from surface.mesh.stitch import validate_stitch_record
                validate_stitch_record(content["stitch"], mesh, fields)
                return SourceRevision(revision_id, row[0], mesh, fields,
                                      _copy_stitch(content["stitch"]))
            if set(content) != {"mesh", "fields"}:
                raise ValueError("unknown source revision content")
            mesh = OrdinaryMesh.from_record(content["mesh"])
            fields = normalize_fields(mesh, content["fields"])
            return SourceRevision(revision_id, row[0], mesh, fields)
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError("damaged source revision") from exc

    def current(self):
        row = self.store.connection.execute(
            "SELECT revision_id FROM source_head WHERE singleton=1").fetchone()
        return None if row is None else row[0]


def _content_snapshot(revision):
    content = {"mesh": revision.mesh.to_record(), "fields": revision.fields.to_record()}
    if revision.stitch is not None:
        content["stitch"] = _copy_stitch(revision.stitch)
    return content
