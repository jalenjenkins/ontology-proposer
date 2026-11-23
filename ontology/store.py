import json
import sqlite3
from contextlib import contextmanager

from ontology.gate import digest, load_suite, pinned_source, replay
from ontology.semantic import validate


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                create table if not exists versions (
                    id integer primary key, parent integer, snapshot text not null);
                insert or ignore into versions values (0, null, '[]');
                create table if not exists batches (
                    id integer primary key, parent integer not null,
                    published integer references versions(id));
                create table if not exists changes (
                    id integer primary key, batch integer references batches(id),
                    object text not null, validation text not null,
                    decision text check(decision in ('approve','reject')));
                create table if not exists gate_reports (
                    batch integer primary key references batches(id),
                    evidence_key text not null, report text not null, acknowledged integer not null default 0);
                create trigger if not exists versions_no_update before update on versions
                    begin select raise(abort,'immutable version'); end;
                create trigger if not exists versions_no_delete before delete on versions
                    begin select raise(abort,'immutable version'); end;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute("pragma foreign_keys=on")
                yield db
        finally:
            db.close()

    def snapshot(self, version=None):
        return self.versioned_snapshot(version)[1]

    def versioned_snapshot(self, version=None):
        with self.connect() as db:
            if version is None:
                row = db.execute("select id,snapshot from versions order by id desc limit 1").fetchone()
            else:
                row = db.execute("select id,snapshot from versions where id=?", [version]).fetchone()
            if row is None:
                raise ValueError("Unknown version")
            return row[0], json.loads(row[1])

    def stage(self, path, objects):
        def identity(obj):
            return obj["kind"], obj["name"]

        with self.connect() as db:
            db.execute("begin immediate")
            parent, snapshot = db.execute("select id,snapshot from versions order by id desc limit 1").fetchone()
            previous = {identity(obj): obj for obj in json.loads(snapshot)}
            identities = [identity(obj) for obj in objects]
            if len(identities) != len(set(identities)):
                raise ValueError("Duplicate changes")
            batch = db.execute("insert into batches (parent) values (?)", [parent]).lastrowid
            for obj in objects:
                old = previous.get(identity(obj))
                if old != obj:
                    payload = dict(before=old, after=obj)
                    db.execute("insert into changes (batch,object,validation) values (?,?,?)",
                               [batch, json.dumps(payload, default=str), json.dumps(validate(path,obj), default=str)])
            return batch

    def review(self, batch):
        with self.connect() as db:
            db.execute("begin")
            row = db.execute("select parent,published from batches where id=?", [batch]).fetchone()
            if row is None:
                raise ValueError("Unknown batch")
            gate = db.execute("select evidence_key,report,acknowledged from gate_reports where batch=?", [batch]).fetchone()
            report = dict(json.loads(gate[1]), evidence_key=gate[0], acknowledged=bool(gate[2])) if gate else None
            return dict(id=batch, parent=row[0], published=row[1], changes=self._changes(db, batch), gate=report)

    def changes(self, batch):
        with self.connect() as db:
            return self._changes(db, batch)

    @staticmethod
    def _changes(db, batch):
        return [dict(id=i, **json.loads(obj), validation=json.loads(result), decision=decision)
                for i, obj, result, decision in db.execute(
                    "select id,object,validation,decision from changes where batch=? order by id", [batch])]

    def decide(self, change, decision):
        if decision not in {"approve", "reject"}:
            raise ValueError("Invalid decision")
        with self.connect() as db:
            db.execute("begin immediate")
            row = db.execute("""select changes.validation, batches.published from changes
                              join batches on batch=batches.id where changes.id=?""", [change]).fetchone()
            if row is None or row[1] is not None:
                raise ValueError("Change is unavailable")
            if decision == "approve" and not json.loads(row[0])["ok"]:
                raise ValueError("Validation failed")
            cursor = db.execute("update changes set decision=? where id=? and decision is not ?",
                                [decision, change, decision])
            if cursor.rowcount:
                db.execute("delete from gate_reports where batch=(select batch from changes where id=?)", [change])

    def _candidate(self, db, batch):
        row = db.execute("select parent,published from batches where id=?", [batch]).fetchone()
        if row is None:
            raise ValueError("Unknown batch")
        parent, published = row
        if published is not None:
            return parent, published, [], []
        latest, snapshot = db.execute("select id,snapshot from versions order by id desc limit 1").fetchone()
        if parent != latest:
            raise ValueError("Stale batch. Stage against the current version.")
        changes = self._changes(db, batch)
        if any(change["decision"] is None for change in changes):
            raise ValueError("Every change needs a decision")
        accepted = [change for change in changes if change["decision"] == "approve"]
        if not accepted:
            raise ValueError("No approved changes")
        before = json.loads(snapshot)
        merged = {(obj["kind"], obj["name"]): obj for obj in before}
        for change in accepted:
            obj = change["after"]
            merged[obj["kind"], obj["name"]] = obj
        return parent, None, before, list(merged.values())

    @staticmethod
    def _evidence_key(parent, candidate, source, suite):
        return digest(["gate-v2", parent, candidate, source, suite])

    def check_gate(self, batch, path, suite_path, full=False):
        if not suite_path:
            raise ValueError("Configure a gate suite with --gate-cases")
        suite = load_suite(suite_path)
        with pinned_source(path) as (source, check_source):
            with self.connect() as db:
                db.execute("begin")
                parent, published, before, candidate = self._candidate(db, batch)
            if published is not None or parent == 0:
                raise ValueError("The gate runs on an unpublished update to an existing ontology")
            key = self._evidence_key(parent, candidate, source, suite)
            report = replay(path, before, candidate, suite, full=full)
            with self.connect() as db:
                db.execute("begin immediate")
                current, published, _, updated = self._candidate(db, batch)
                if published is not None or key != self._evidence_key(current, updated, source, load_suite(suite_path)):
                    raise ValueError("The proposal or suite changed during replay. Run it again.")
                check_source()
                db.execute("""insert into gate_reports(batch,evidence_key,report) values (?,?,?)
                    on conflict(batch) do update set evidence_key=excluded.evidence_key,
                    report=excluded.report, acknowledged=0""", [batch, key, json.dumps(report)])
        return dict(report, evidence_key=key, acknowledged=False)

    def acknowledge_gate(self, batch, key):
        with self.connect() as db:
            db.execute("begin immediate")
            row = db.execute("""select report from gate_reports join batches on batch=batches.id
                              where batch=? and evidence_key=? and published is null""", [batch, key]).fetchone()
            if row is None:
                raise ValueError("Review the current replay before acknowledging")
            report = json.loads(row[0])
            if report["blocked"] or not report["findings"]:
                raise ValueError("Only answer changes can be acknowledged")
            db.execute("update gate_reports set acknowledged=1 where batch=?", [batch])

    def publish(self, batch, path=None, suite_path=None):
        with self.connect() as db:
            db.execute("begin")
            parent, published, _, _ = self._candidate(db, batch)
        if published is not None:
            return published
        if parent == 0:
            return self._publish(batch)
        if path is None or not suite_path:
            raise ValueError("Configure a gate suite with --gate-cases")
        with pinned_source(path) as (source, check_source):
            return self._publish(batch, source, suite_path, check_source)

    def _publish(self, batch, source=None, suite_path=None, check_source=None):
        with self.connect() as db:
            db.execute("begin immediate")
            parent, published, _, candidate = self._candidate(db, batch)
            if published is not None:
                return published
            if parent:
                key = self._evidence_key(parent, candidate, source, load_suite(suite_path))
                row = db.execute("select report,acknowledged from gate_reports where batch=? and evidence_key=?",
                                 [batch, key]).fetchone()
                if row is None:
                    raise ValueError("Run the change gate on the current proposal, source and suite")
                report = json.loads(row[0])
                if report["blocked"]:
                    raise ValueError("Change gate blocked publication")
                if report["findings"] and not row[1]:
                    raise ValueError("Acknowledge the answer changes before publishing")
                check_source()
            version = db.execute("insert into versions (parent,snapshot) values (?,?)",
                                 [parent, json.dumps(candidate)]).lastrowid
            db.execute("update batches set published=? where id=?", [version, batch])
            return version
