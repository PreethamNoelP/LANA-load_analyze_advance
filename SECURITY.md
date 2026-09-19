# Security Policy

## What LANA is designed to be

LANA is a **local-first, single-user tool**. It is built to run on your own
machine, against your own data, with a model running on that same machine. The
defaults reflect that and nothing else:

- It binds to localhost. The Docker stack publishes its ports on `127.0.0.1`
  only, for the same reason — `docker compose up` must not put an
  unauthenticated API on the network the machine happens to be attached to.
  Reaching LANA from another machine is a deliberate edit to
  `docker-compose.yml`, made after reading the next section.
- There is **no authentication by default**. Any client that can reach the port
  can upload files, read any session whose id it knows, run analysis, generate
  reports, and occupy your local model.
- Session ids are unguessable (UUID4) but are the *only* thing protecting a
  session. There is no per-user ownership.

This is a deliberate scope decision, not an oversight. A single person
analysing their own spreadsheet on their own laptop does not need a login
system, and adding one would make the common case worse.

## If more than one person can reach it

Running LANA on a home server, a shared workstation, or anything reachable
beyond localhost changes the threat model. Two things are expected of you:

1. **Set `LANA_AUTH_TOKEN`.** Every request then needs
   `Authorization: Bearer <token>`, or the HttpOnly cookie a browser gets by
   POSTing the token once to `/auth/session`. Generate one with
   `python -c "import secrets; print(secrets.token_hex(32))"`. Note what this
   is and is not: one shared secret for the whole instance. Everyone holding it
   has full access to their own sessions. It is a gate, not a user system.

   The token is **not** compiled into the frontend bundle. It used to be, via
   `VITE_LANA_AUTH_TOKEN`, which meant any visitor could read it out of the
   JavaScript and rotating it required rebuilding the image. The browser now
   exchanges it for an HttpOnly, SameSite=Strict cookie that page scripts
   cannot read and an XSS payload cannot exfiltrate.
2. **Put it behind a reverse proxy with TLS.** The token travels in a header;
   over plain HTTP on an untrusted network, it travels in the clear.

Do not expose LANA to the public internet. The upload and request-size limits
exist to stop a mistake from taking the machine down — they are not access
control, and they are not a substitute for it.

## What LANA does protect against

Listed so you know what has actually been considered, rather than having to
infer it from the code:

- **Upload size and memory exhaustion** — uploads stream to a spooled temp
  file, and every format is costed against a host-derived memory budget
  *before* it is parsed. For `.xlsx`, the archive's declared uncompressed size
  is read from its central directory, so a decompression bomb is refused
  without inflating a byte.
- **CSV formula injection** — cells beginning `=`, `+`, `@`, tab or CR (and a
  `-` that is not simply a negative number) are escaped on export, so a
  malicious value in an uploaded file does not become executable when the
  export is opened in Excel, LibreOffice or Sheets.
- **XML entity expansion** — `defusedxml` hardens the `openpyxl` path.
- **Provider error disclosure** — LLM errors are sanitised before reaching the
  browser, so a misconfigured endpoint does not leak its hostname, route or
  anything credential-shaped to every visitor.
- **CORS** — the allowlist rejects `*`, which cannot be combined with
  credentials safely.
- **Container posture** — the Docker image runs as an unprivileged user.
- **Generated SQL is sandboxed.** Answering a question can involve a model
  writing SQL that the server executes, which is a code-execution path reached
  from a text box. Four independent layers stand in front of it: DuckDB with
  `enable_external_access=false` and `lock_configuration=true` (no filesystem,
  no network, and a query cannot turn either back on), a single-statement check
  using DuckDB's own parser, a read-only shape check, and a denylist of
  file-touching functions. `tests/test_sql_engine.py` proves each layer against
  local file reads, metadata-service fetches, statement stacking and attempts
  to disable the sandbox. Queries are also row-capped and deadline-bounded.
- **SSRF on URL sources.** "Analyse the data at this URL" hands an attacker an
  HTTP client inside your network. Private, loopback, link-local and reserved
  addresses are refused; *every* address a hostname resolves to must be public
  (checking one is not enough); each redirect hop is re-validated, because a
  public URL that 302s to `169.254.169.254` is the standard bypass; and the
  request is sent to the address that was checked rather than to a name the
  HTTP client would resolve a second time, which is what closes the DNS
  rebinding window. `Host` and TLS SNI still carry the original hostname, so
  certificates are verified against the site being requested.
  `tests/test_ssrf_pinning.py` asserts the destination is an IP literal, that
  the certificate is not verified against it, and that a redirect to the
  metadata service is refused before a socket opens.
  `LANA_ALLOW_PRIVATE_SOURCE_URLS=true` disables this for the genuine
  internal-API case — do not set it where untrusted users can supply URLs.
- **Server-side file reads are scoped to the deployment.** The `file`
  connector reads a path on the *server's* disk, which on a single-user laptop
  is the whole point and on a shared instance is an arbitrary file read
  reachable from a JSON body. With `LANA_AUTH_TOKEN` set — the project's own
  signal that more than one person can reach this instance — the connector is
  disabled unless `LANA_FILE_SOURCE_ROOTS` names the directories it may read.
  Containment is decided on the resolved path, so `..` and symlinks cannot
  climb out of a root. Uploading a file is unaffected either way.
- **An audit trail.** With session persistence on (or `LANA_AUDIT_LOG` set),
  data entering LANA, being cleaned, being switched between versions, being
  questioned, and leaving as an export are appended to `audit.jsonl` in the
  data directory, along with refused access and failed authentication. Entries
  carry the principal, the request id and the session — never cell values,
  column names, filenames' contents or the token. `GET /audit` returns the
  caller's own entries and no one else's.
- **Credentials from connectors are redacted** everywhere they could surface:
  labels, errors, logs. A database password supplied to LANA is never echoed
  back in a response, and `tests/test_sources.py` asserts that for URI
  passwords, query-string keys, bearer tokens and connection-string options.
- **Per-session ownership.** A session records the principal that created it.
  Another principal gets a 404 — not a 403, which would confirm the id exists
  and turn the endpoint into an enumeration oracle.
- **Rate limiting.** A token bucket per caller, with a separate and tighter
  bucket for the LLM endpoints, shared across workers when persistence is on.

## Known limitations, stated plainly

These are accepted for now, not hidden:

- **Session ownership is per-token, not per-user.** Sessions are now owned and
  a different principal cannot read them — but with `LANA_AUTH_TOKEN` set,
  everyone shares one token and therefore one principal. Two colleagues using
  the same instance are the same principal and can see each other's sessions.
  Separating them needs real user accounts, which LANA does not have.
- **Persisted sessions are stored unencrypted.** With
  `LANA_PERSIST_SESSIONS=true`, uploaded data is written to `LANA_DATA_DIR` as
  Parquet files. That is your own data on your own disk, at your filesystem's
  protection level and no more.
- **Prompt injection through cell contents is only partly mitigated.** Values
  reaching the model are stripped of control characters and length-bounded,
  but LANA does not claim to detect data crafted to manipulate the model's
  answer. The answer validator checks numeric claims against computed facts;
  it does not check intent. See `KNOWN_BLIND_SPOTS` in
  `app/llm/validation.py` and `docs/provenance.md`.
- **Multi-worker, not multi-host.** Several uvicorn workers sharing one volume
  now coordinate correctly: they share one LLM concurrency cap and one rate
  limit through SQLite, and a worker that misses a session in memory reads it
  back from the shared store. Replicas on *separate hosts* are not supported
  and are not claimed to be — that needs shared storage and a real lock
  service, neither of which is in scope.
- **Prompt injection through a generated query.** The SQL planner sees only
  the schema, never cell values, so data cannot steer the query it writes.
  Column *names* are part of that schema, so a maliciously named column is
  still text the planner reads. The sandbox bounds what any query can do, but
  a crafted column name could still influence which rows are selected.
- **Metrics are unauthenticated.** `/metrics` exposes request counts, latency
  histograms and error rates — no column names, values or filenames. If load
  patterns are sensitive in your deployment, block the path at your proxy.
- **The audit trail is a file, not a ledger.** It is appended to under a
  process lock and rotated by size. It answers "what did this instance do" for
  an operator reading it; it is not evidence against someone who has write
  access to the disk. Tamper evidence would mean signing entries or shipping
  them off-host, neither of which is in scope, and neither of which is
  claimed.

## Reporting a vulnerability

Please report privately through GitHub's **Report a vulnerability** button on
the repository's Security tab, rather than opening a public issue.

Include what you did, what happened, and what you expected. A proof of concept
helps. You will get an acknowledgement; this is a single-maintainer project, so
please allow a reasonable window before disclosing publicly.

For anything that is not sensitive — a hardening suggestion, a question about
the scope above — a normal issue is fine and welcome.

## Supported versions

The latest commit on `main`. There are no released versions yet, so fixes land
there and nowhere else.
