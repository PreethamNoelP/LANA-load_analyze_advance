# Security Policy

## What LANA is designed to be

LANA is a **local-first, single-user tool**. It is built to run on your own
machine, against your own data, with a model running on that same machine. The
defaults reflect that and nothing else:

- It binds to localhost.
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
   `Authorization: Bearer <token>`. Generate one with
   `python -c "import secrets; print(secrets.token_hex(32))"`. Note what this
   is and is not: one shared secret for the whole instance. Everyone holding it
   has full access to every session. It is a gate, not a user system.
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

## Known limitations, stated plainly

These are accepted for now, not hidden:

- **No per-session ownership.** Anyone who learns a session id can use that
  session. With `LANA_AUTH_TOKEN` set, they would also need the token.
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
- **No rate limiting.** Concurrent LLM calls are capped, nothing else is.
- **Single process.** No multi-worker or multi-replica support; the session
  store is in-memory with an optional disk mirror.

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
