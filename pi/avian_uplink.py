#!/usr/bin/env python3
"""avian-uplink - ship BirdNET-Pi detections to the Vogel server.

Python standard library only (urllib, sqlite3, zoneinfo, tomllib, json).
Nothing gets installed on the Pi.

Design notes
------------
* The station database is opened strictly read-only. BirdNET-Pi keeps writing
  to it; we must never take a write lock on it.
* Progress is a persistent rowid cursor, so a restart never re-reads and never
  skips.
* Everything read is written to our own outbox database first, then uploaded.
  Only ids the server confirmed are removed. That makes a power cut, a dead
  uplink and a server restart all survivable.
* No audio ever leaves the Pi. Metadata only.

    ./avian_uplink.py --once --db /tmp/birds.db
    ./avian_uplink.py --config /etc/avian-uplink/config.toml
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

AGENT_VERSION = "avian-uplink/1.0.0"

DEFAULT_CONFIG_PATHS = (
    "/etc/avian-uplink/config.toml",
    os.path.expanduser("~/.avian-uplink/config.toml"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml"),
)

# Constant schedule, never gives up. Index beyond the end repeats the last.
BACKOFF_SECONDS = (30, 60, 120, 300, 900)
RETENTION_DAYS = 14
HEARTBEAT_INTERVAL_S = 300

log = logging.getLogger("avian-uplink")


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


@dataclass
class Config:
    server_url: str = "http://localhost:8090"
    device_token: str = "dev-token-zweitstandort"
    device_id: str = "pi-zweitstandort-01"
    db_path: str = "/home/pi/BirdNET-Pi/scripts/birds.db"
    db_table: str = "detections"
    tz: str = "Europe/Beispielstadt"
    poll_interval_s: int = 60
    batch_max: int = 200
    state_dir: str = os.path.expanduser("~/.avian-uplink")
    http_timeout_s: int = 45
    # Plan B when the sqlite file is not reachable (different host, permissions).
    http_source_url: str = ""

    @property
    def outbox_path(self) -> str:
        return os.path.join(self.state_dir, "outbox.db")


def load_config(path: str | None) -> Config:
    candidates = [path] if path else list(DEFAULT_CONFIG_PATHS)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            if tomllib is None:
                raise SystemExit(
                    "tomllib is unavailable (needs Python 3.11+); "
                    "upgrade Python or pass every value on the command line"
                )
            with open(candidate, "rb") as handle:
                raw = tomllib.load(handle)
            log.info("config loaded from %s", candidate)
            known = {field for field in Config.__dataclass_fields__}
            unknown = set(raw) - known
            if unknown:
                log.warning("ignoring unknown config keys: %s", ", ".join(sorted(unknown)))
            return Config(**{k: v for k, v in raw.items() if k in known})
    log.warning("no config file found, using built-in defaults")
    return Config()


# --------------------------------------------------------------------------
# detections
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Detection:
    rowid: int
    local_date: str  # YYYY-MM-DD
    local_time: str  # HH:MM:SS
    sci_name: str
    com_name: str
    confidence: float
    source_file: str | None
    week: int | None

    def to_event(self, device_id: str, tz_name: str) -> dict:
        moment_utc = local_to_utc(self.local_date, self.local_time, tz_name)
        return {
            "client_event_id": f"{device_id}:{self.rowid}",
            "detected_at": moment_utc,
            "local_date": self.local_date,
            "local_time": self.local_time,
            "sci_name": self.sci_name,
            "com_name": self.com_name,
            "confidence": self.confidence,
            "source_file": self.source_file,
            "week": self.week,
        }


def local_to_utc(local_date: str, local_time: str, tz_name: str) -> str:
    """DST aware. Ambiguous autumn readings resolve to the first pass."""
    time_part = (local_time or "00:00:00").strip()
    if len(time_part) == 5:
        time_part += ":00"
    time_part = time_part.split(".")[0]
    try:
        naive = dt.datetime.strptime(f"{local_date} {time_part}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        naive = dt.datetime.strptime(local_date, "%Y-%m-%d")
    aware = naive.replace(tzinfo=ZoneInfo(tz_name), fold=0)
    return aware.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# data sources (interface + two implementations)
# --------------------------------------------------------------------------


class DetectionSource:
    """Anything that can hand out detections newer than a cursor."""

    def fetch_since(self, cursor: int, limit: int) -> list[Detection]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SqliteSource(DetectionSource):
    """Read-only rowid scan over the BirdNET-Pi database."""

    def __init__(self, db_path: str, table: str = "detections") -> None:
        self.db_path = db_path
        self.table = table
        if not os.path.exists(db_path):
            raise FileNotFoundError(db_path)
        uri = "file:{}?mode=ro".format(urllib.parse.quote(os.path.abspath(db_path)))
        self.conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.columns = self._columns()

    def _columns(self) -> set[str]:
        rows = self.conn.execute(f"PRAGMA table_info({self.table})").fetchall()
        if not rows:
            raise RuntimeError(f"table '{self.table}' not found in {self.db_path}")
        return {row["name"] for row in rows}

    def _pick(self, row: sqlite3.Row, *names: str):
        for name in names:
            if name in self.columns:
                value = row[name]
                if value is not None:
                    return value
        return None

    def fetch_since(self, cursor: int, limit: int) -> list[Detection]:
        sql = (
            f"SELECT rowid AS _rowid, * FROM {self.table} "
            "WHERE rowid > ? ORDER BY rowid ASC LIMIT ?"
        )
        rows = self.conn.execute(sql, (cursor, limit)).fetchall()
        out: list[Detection] = []
        for row in rows:
            try:
                confidence = float(self._pick(row, "Confidence", "confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            week_raw = self._pick(row, "Week", "week")
            try:
                week = int(week_raw) if week_raw is not None else None
            except (TypeError, ValueError):
                week = None
            out.append(
                Detection(
                    rowid=int(row["_rowid"]),
                    local_date=str(self._pick(row, "Date", "date") or ""),
                    local_time=str(self._pick(row, "Time", "time") or "00:00:00"),
                    sci_name=str(self._pick(row, "Sci_Name", "sci_name") or ""),
                    com_name=str(self._pick(row, "Com_Name", "com_name") or ""),
                    confidence=confidence,
                    source_file=self._pick(row, "File_Name", "file_name"),
                    week=week,
                )
            )
        return [d for d in out if d.sci_name and d.local_date]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


class HttpApiSource(DetectionSource):
    """Plan B: the station's own web API when the file is out of reach.

    The upstream endpoint groups by species and has no rowid, so we derive a
    stable synthetic key from the recording file name. Ordering is
    best-effort; the server's UNIQUE(device_id, client_event_id) still keeps
    the result idempotent.
    """

    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_since(self, cursor: int, limit: int) -> list[Detection]:
        url = f"{self.base_url}?action=recent&hours=48"
        request = urllib.request.Request(url, headers={"User-Agent": AGENT_VERSION})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))

        out: list[Detection] = []
        for entry in payload.get("species", [])[:limit]:
            top_at = entry.get("top_at") or ""
            parts = top_at.split(" ")
            if len(parts) != 2:
                continue
            synthetic = abs(hash((entry.get("sci"), top_at))) % (10**12)
            if synthetic <= cursor:
                continue
            out.append(
                Detection(
                    rowid=synthetic,
                    local_date=parts[0],
                    local_time=parts[1],
                    sci_name=entry.get("sci") or "",
                    com_name=entry.get("com") or "",
                    confidence=float(entry.get("best_conf") or 0.0),
                    source_file=entry.get("top_file"),
                    week=None,
                )
            )
        return out


# --------------------------------------------------------------------------
# outbox
# --------------------------------------------------------------------------


class Outbox:
    """Durable local queue. WAL so a power cut cannot corrupt it."""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                client_event_id TEXT PRIMARY KEY,
                payload         TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_outbox_due ON outbox(next_attempt_at);
            CREATE TABLE IF NOT EXISTS cursor (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    # -- cursor ------------------------------------------------------------

    def get_cursor(self, key: str = "rowid", default: int = 0) -> int:
        row = self.conn.execute("SELECT v FROM cursor WHERE k = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return int(row["v"])
        except (TypeError, ValueError):
            return default

    def set_cursor(self, value: int, key: str = "rowid") -> None:
        self.conn.execute(
            "INSERT INTO cursor (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, str(value)),
        )
        self.conn.commit()

    # -- queue -------------------------------------------------------------

    def enqueue(self, events: list[dict]) -> int:
        now = _now_iso()
        rows = [
            (event["client_event_id"], json.dumps(event, separators=(",", ":")), now, 0, now)
            for event in events
        ]
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO outbox "
            "(client_event_id, payload, created_at, attempts, next_attempt_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def due(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM outbox WHERE next_attempt_at <= ? "
            "ORDER BY created_at ASC, client_event_id ASC LIMIT ?",
            (_now_iso(), limit),
        ).fetchall()

    def delete(self, ids: list[str]) -> int:
        if not ids:
            return 0
        deleted = 0
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" * len(chunk))
            cur = self.conn.execute(
                f"DELETE FROM outbox WHERE client_event_id IN ({marks})", chunk
            )
            deleted += cur.rowcount or 0
        self.conn.commit()
        return deleted

    def defer(self, ids: list[str]) -> None:
        """Bump attempts and push the next try out by the backoff schedule."""
        if not ids:
            return
        for event_id in ids:
            row = self.conn.execute(
                "SELECT attempts FROM outbox WHERE client_event_id = ?", (event_id,)
            ).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            nxt = (_now() + dt.timedelta(seconds=delay)).isoformat()
            self.conn.execute(
                "UPDATE outbox SET attempts = ?, next_attempt_at = ? "
                "WHERE client_event_id = ?",
                (attempts, nxt, event_id),
            )
        self.conn.commit()

    def depth(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def prune(self, days: int = RETENTION_DAYS) -> int:
        cutoff = (_now() - dt.timedelta(days=days)).isoformat()
        cur = self.conn.execute("DELETE FROM outbox WHERE created_at < ?", (cutoff,))
        self.conn.commit()
        if cur.rowcount:
            log.warning("pruned %d outbox entries older than %d days", cur.rowcount, days)
        return cur.rowcount or 0

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# --------------------------------------------------------------------------
# server transport
# --------------------------------------------------------------------------


class ServerError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class ServerClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.config.server_url.rstrip("/") + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.device_token}",
                "User-Agent": AGENT_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.http_timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ServerError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise ServerError(0, str(exc.reason)) from exc
        except (TimeoutError, OSError) as exc:
            raise ServerError(0, str(exc)) from exc

    def send_detections(self, events: list[dict]) -> dict:
        return self._post(
            "/api/v1/ingest/detections",
            {
                "schema_version": 1,
                "device_id": self.config.device_id,
                "agent_version": AGENT_VERSION,
                "sent_at": _now_iso(),
                "site": {"tz": self.config.tz},
                "detections": events,
            },
        )

    def heartbeat(self, outbox_depth: int, last_detection_at: str | None) -> dict:
        return self._post(
            "/api/v1/ingest/heartbeat",
            {
                "device_id": self.config.device_id,
                "agent_version": AGENT_VERSION,
                "uptime_s": _uptime_seconds(),
                "outbox_depth": outbox_depth,
                "last_detection_at": last_detection_at,
                "disk_free_mb": _disk_free_mb(),
                "cpu_temp_c": _cpu_temp_c(),
            },
        )


def _uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _disk_free_mb() -> float | None:
    try:
        usage = os.statvfs("/")
        return round(usage.f_bavail * usage.f_frsize / (1024 * 1024), 1)
    except OSError:
        return None


def _cpu_temp_c() -> float | None:
    for path in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ):
        try:
            with open(path, "r", encoding="ascii") as handle:
                return round(int(handle.read().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            continue
    return None


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------


class Uplink:
    def __init__(self, config: Config, source: DetectionSource, outbox: Outbox, dry_run: bool = False):
        self.config = config
        self.source = source
        self.outbox = outbox
        self.client = ServerClient(config)
        self.dry_run = dry_run
        self.last_detection_at: str | None = None
        self._stop = False

    def stop(self, *_args) -> None:
        log.info("shutdown requested")
        self._stop = True

    # -- one full cycle ----------------------------------------------------

    def collect(self) -> int:
        """Read new rows from the station and park them in the outbox."""
        cursor = self.outbox.get_cursor()
        found = 0
        while True:
            rows = self.source.fetch_since(cursor, self.config.batch_max)
            if not rows:
                break
            events = [row.to_event(self.config.device_id, self.config.tz) for row in rows]
            self.outbox.enqueue(events)
            cursor = max(row.rowid for row in rows)
            # The cursor only moves after the rows are durable in the outbox.
            self.outbox.set_cursor(cursor)
            found += len(rows)
            if events:
                self.last_detection_at = events[-1]["detected_at"]
            if len(rows) < self.config.batch_max:
                break
        if found:
            log.info("collected %d new detection(s), cursor now %d", found, cursor)
        return found

    def upload(self) -> tuple[int, int]:
        """Send whatever is due. Only confirmed ids leave the outbox."""
        sent = confirmed = 0
        while not self._stop:
            rows = self.outbox.due(self.config.batch_max)
            if not rows:
                break
            events = [json.loads(row["payload"]) for row in rows]
            ids = [row["client_event_id"] for row in rows]

            if self.dry_run:
                log.info("[dry-run] would upload %d event(s)", len(events))
                return len(events), 0

            try:
                result = self.client.send_detections(events)
            except ServerError as exc:
                if exc.status in (400, 413, 422):
                    # A malformed batch would loop forever; halve it instead.
                    log.error("server rejected the batch (%s)", exc)
                    if len(events) > 1:
                        self.config.batch_max = max(1, len(events) // 2)
                        log.warning("reducing batch size to %d", self.config.batch_max)
                    else:
                        log.error("dropping unsendable event %s", ids[0])
                        self.outbox.delete(ids)
                    return sent, confirmed
                log.warning("upload failed, backing off: %s", exc)
                self.outbox.defer(ids)
                return sent, confirmed

            done = list(result.get("accepted", [])) + list(result.get("duplicate_ids", []))
            if not done and result.get("duplicates"):
                # Older server without duplicate_ids: everything we sent that
                # was not rejected is settled.
                rejected = {item.get("id") for item in result.get("rejected", [])}
                done = [i for i in ids if i not in rejected]

            for item in result.get("rejected", []):
                log.error("server rejected %s: %s", item.get("id"), item.get("reason"))
                self.outbox.delete([item["id"]])

            removed = self.outbox.delete(done)
            sent += len(events)
            confirmed += removed
            log.info(
                "uploaded %d, accepted=%d duplicates=%d, outbox now %d",
                len(events),
                len(result.get("accepted", [])),
                int(result.get("duplicates", 0)),
                self.outbox.depth(),
            )
            if len(rows) < self.config.batch_max:
                break
        return sent, confirmed

    def send_heartbeat(self) -> None:
        if self.dry_run:
            log.info("[dry-run] would send heartbeat")
            return
        try:
            result = self.client.heartbeat(self.outbox.depth(), self.last_detection_at)
        except ServerError as exc:
            log.warning("heartbeat failed: %s", exc)
            return
        server_config = result.get("config") or {}
        # The server steers the client.
        poll = int(server_config.get("poll_interval_s") or self.config.poll_interval_s)
        batch = int(server_config.get("batch_max") or self.config.batch_max)
        if poll != self.config.poll_interval_s:
            log.info("server set poll_interval_s=%d", poll)
            self.config.poll_interval_s = max(5, poll)
        if batch != self.config.batch_max:
            log.info("server set batch_max=%d", batch)
            self.config.batch_max = max(1, min(batch, 200))

    def cycle(self) -> None:
        self.collect()
        self.upload()
        self.outbox.prune()

    def run_forever(self) -> None:
        last_heartbeat = 0.0
        self.send_heartbeat()
        last_heartbeat = time.monotonic()
        while not self._stop:
            started = time.monotonic()
            try:
                self.cycle()
            except Exception as exc:  # noqa: BLE001 - the daemon never dies
                log.exception("cycle failed: %s", exc)
            if time.monotonic() - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                try:
                    self.send_heartbeat()
                except Exception as exc:  # noqa: BLE001
                    log.warning("heartbeat error: %s", exc)
                last_heartbeat = time.monotonic()
            elapsed = time.monotonic() - started
            remaining = max(1.0, self.config.poll_interval_s - elapsed)
            slept = 0.0
            while slept < remaining and not self._stop:
                time.sleep(min(1.0, remaining - slept))
                slept += 1.0


# --------------------------------------------------------------------------


def build_source(config: Config) -> DetectionSource:
    try:
        return SqliteSource(config.db_path, config.db_table)
    except (FileNotFoundError, sqlite3.Error, RuntimeError) as exc:
        if config.http_source_url:
            log.warning("sqlite source unavailable (%s), falling back to HTTP", exc)
            return HttpApiSource(config.http_source_url)
        raise SystemExit(f"cannot open station database {config.db_path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BirdNET-Pi uplink daemon")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--db", help="override db_path (handy for testing on a Mac)")
    parser.add_argument("--server", help="override server_url")
    parser.add_argument("--token", help="override device_token")
    parser.add_argument("--device-id", help="override device_id")
    parser.add_argument("--state-dir", help="override state_dir (outbox location)")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="never POST anything")
    parser.add_argument("--reset-cursor", action="store_true", help="re-read the station db from row 0")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    config = load_config(args.config)
    for attr, value in (
        ("db_path", args.db),
        ("server_url", args.server),
        ("device_token", args.token),
        ("device_id", args.device_id),
        ("state_dir", args.state_dir),
    ):
        if value:
            setattr(config, attr, value)

    # Environment beats the file, so systemd drop-ins stay simple.
    config.device_token = os.environ.get("AVIAN_DEVICE_TOKEN", config.device_token)
    config.server_url = os.environ.get("AVIAN_SERVER_URL", config.server_url)

    log.info(
        "%s -> %s as %s (db=%s)",
        AGENT_VERSION, config.server_url, config.device_id, config.db_path,
    )

    outbox = Outbox(config.outbox_path)
    if args.reset_cursor:
        outbox.set_cursor(0)
        log.warning("cursor reset to 0")

    source = build_source(config)
    agent = Uplink(config, source, outbox, dry_run=args.dry_run)

    signal.signal(signal.SIGTERM, agent.stop)
    signal.signal(signal.SIGINT, agent.stop)

    try:
        if args.once:
            agent.collect()
            agent.upload()
            agent.outbox.prune()
            agent.send_heartbeat()
            log.info("single cycle complete, outbox depth %d", outbox.depth())
        else:
            agent.run_forever()
    finally:
        source.close()
        outbox.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
