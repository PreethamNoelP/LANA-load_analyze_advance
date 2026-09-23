# Backup and restore

`python -m scripts.backup` snapshots LANA's durable state into a local,
rotating archive. Read this before you need it — specifically the
encryption-key section below, since a backup without the key is not
actually a backup of encrypted sessions.

## What's included

| File | Included when |
|---|---|
| `accounts.db` | `LANA_ACCOUNTS=true` and at least one user exists |
| `sessions.db` + `<data_dir>/<session_id>/` folders | `LANA_PERSIST_SESSIONS=true` and at least one upload has happened |
| `audit.jsonl` and its rotated siblings (`audit.1.jsonl`, …) | auditing is on (follows session persistence, or `LANA_AUDIT_LOG` explicitly) |

Nothing else on the instance is durable state. A dev instance with none of
the above turned on backs up to an empty (but valid) archive — that's a
correct result, not an error.

## What's deliberately excluded

**`coordination.db`** — rate-limit buckets and LLM concurrency leases,
all with short TTLs. It reinitialises cleanly on every normal restart
already; there is nothing in it worth preserving across one.

## The encryption key is never in the backup

If `LANA_ENCRYPTION_KEY` is set, the per-session `raw.parquet`/
`cleaned.parquet`/`ledger.json` files are already encrypted before this
script ever sees them, so the archive itself carries no more risk than the
live files already do. But the **key**
is never written anywhere on disk by LANA (see "Encryption at rest" in
[`SECURITY.md`](../SECURITY.md)), and this script does not change that. Keep
the key somewhere separate from the backup archive — a password manager, a
secrets vault, whatever you'd use for any other credential. Restoring an
archive of encrypted sessions without the key that encrypted them recovers
nothing: `PersistenceBackend.load` treats an undecryptable session exactly
like a corrupt one and discards it, restore or not.

`accounts.db` (scrypt password hashes) and `audit.jsonl` are not touched by
`LANA_ENCRYPTION_KEY` at all — they were never encrypted at rest, backup or
not.

## Running a backup

```bash
python -m scripts.backup run
```

Writes one timestamped `lana-backup-<UTC timestamp>.tar.gz` into the backup
directory (default: a `backups/` folder next to your data directory — not
inside it, so losing the data directory doesn't take the backups too), then
prunes older archives down to the retention count. See `.env.example` for
`LANA_BACKUP_DIR` and `LANA_BACKUP_RETENTION`, or pass `--backup-dir`/`--keep`
directly.

```bash
python -m scripts.backup list
```

## Scheduling it

**cron** (Linux/macOS):

```cron
0 3 * * * cd /path/to/LANA-load_analyze_advance && /path/to/.venv/bin/python -m scripts.backup run >> /var/log/lana-backup.log 2>&1
```

**Windows Task Scheduler** — a daily trigger running:

```
<path>\.venv\Scripts\python.exe -m scripts.backup run
```

with "Start in" set to the repository root, so `.env` is picked up the same
way it is for `uvicorn`.

**Docker** — run it inside the running container, against the same volume
the app uses (mirroring the one `docker compose exec` example already in
`docker-compose.yml`'s own header comment):

```bash
docker compose exec backend python -m scripts.backup run
```

The archive lands inside the container's `/data/../backups` by default;
mount a second host volume and point `LANA_BACKUP_DIR` at it if you want
backups to survive the container (and its volume) being removed entirely,
not just a restart.

## Restoring

```bash
python -m scripts.backup restore lana-backup-20260101T030000Z.tar.gz --data-dir /path/to/empty/dir
```

**Stop LANA first if you're restoring into the directory it actually
runs against.** The restore target must be empty — the script refuses
otherwise, specifically so it can never partially overwrite a live SQLite
file mid-write. Restore into a scratch directory, then move it into place
once LANA is stopped:

```bash
python -m scripts.backup restore <archive> --data-dir /tmp/lana-restore
# stop LANA
mv /tmp/lana-restore/* /path/to/real/data-dir/
# start LANA again
```

If the original deployment used `LANA_ACCOUNTS_DB` or `LANA_AUDIT_LOG` to
point those files somewhere *outside* the data directory, the restore
collapses them back into the data directory alongside everything else —
move them back to your preferred location afterward if you rely on that
layout.
