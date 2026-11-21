# SecureRAG

SecureRAG is a permission-aware, multi-tenant retrieval-augmented generation system. The current implementation includes **Phases 1–8**, including secure ingestion, tenant-authorized retrieval, citation-aware answer generation, persistent audit logging, and tenant-scoped document permission management. Frontend work remains out of scope.

## Technology stack

Python 3.12+, FastAPI, Uvicorn, PostgreSQL, SQLAlchemy 2.x, Psycopg 3, Alembic, PyJWT, pwdlib with Argon2id, Qdrant, Pydantic Settings, PyMuPDF, Sentence Transformers, Hugging Face Transformers, Docker Compose, pytest, and HTTPX.

## Architecture

`app.main` creates the FastAPI application and manages its lifecycle. `app.config` centralizes environment settings. `app.auth`, `app.tenants`, `app.documents`, and `app.permissions` serve authentication, tenant, document, and document permission operations. `app.retrieval` applies tenant filtering in Qdrant and authoritative PostgreSQL ACL/status checks. `app.generation` uses only the authorized retrieval response to build bounded context, call an OpenAI-compatible provider, and map citations back to actual source metadata. `app.audit` validates, persists, protects, and internally queries tenant-scoped audit events. Permission changes update PostgreSQL and enqueue a coalesced, per-document synchronization task in the same transaction; Qdrant synchronization is a separate, retryable step. Alembic manages schema changes; the application never calls `create_all()` at startup.

## Folder structure

```text
SecureRAG/
├── backend/app/{api,audit,auth,database,documents,generation,permissions,retrieval,tenants,users}/
├── backend/alembic/versions/ (users, tenants, documents, audit logs, permissions)
├── backend/tests/
├── alembic.ini
├── backend/requirements.txt
├── backend/Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

## Prerequisites

- Python 3.11 or later (container uses Python 3.12)
- Docker Desktop with Compose
- Windows PowerShell

Node.js is not needed in this backend-only phase. Local PostgreSQL and Qdrant installations are optional when using Compose.

## Installation (PowerShell)

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
```

If activation is blocked, run `Set-ExecutionPolicy -Scope Process Bypass` in the current shell, then activate again.

## Environment setup

```powershell
Copy-Item .env.example .env
notepad .env
```

Set a local development `POSTGRES_PASSWORD`; keep the same password in `DATABASE_URL`. Generate a unique signing key and set `SECRET_KEY` to its base64 value. For example, generate bytes and copy the encoded key to the clipboard:

```powershell
$secretBytes = [Security.Cryptography.RandomNumberGenerator]::GetBytes(48)
[Convert]::ToBase64String($secretBytes) | Set-Clipboard
```

Paste the value into `.env`. The signing secret must be at least 32 bytes. Keep `.env` private and ignored by Git; do not use this key across environments. Access tokens are HS256 signed, issuer and audience checked, and expire within 1–60 minutes (30 by default).

For production, `APP_ENV=production` requires `DEBUG=false`, a strong non-placeholder `SECRET_KEY`, PostgreSQL `DATABASE_URL`, an authenticated HTTPS Qdrant endpoint, and an explicit comma-separated `TRUSTED_HOSTS` list containing exact public hostnames. Wildcards are rejected. The configured host list is enforced with `TrustedHostMiddleware`. Set `TRUSTED_PROXY_CIDRS` only to actual ingress proxy networks; only then is the rightmost valid address from `X-Forwarded-For` used for authentication throttling. The proxy must strip or normalize incoming forwarding headers and append the verified client address. Forwarded scheme headers are not trusted by the application.

Login is limited to 10 attempts per client address per 60 seconds; registration is limited to 5 attempts per address per hour. Counters are shared through PostgreSQL and the `0006_auth_rate_limits` migration, stale rows are pruned in bounded batches, and storage has a hard row cap that fails closed with HTTP 503 for new buckets at capacity. Add independent edge rate and concurrency limits at the trusted reverse proxy, especially for expensive document/model workloads.

Apply the current PostgreSQL schema, including audit logs, before using the application:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m alembic -c alembic.ini upgrade head
```

The schema includes the shared authentication rate-limit table (`0006_auth_rate_limits`). Do not run the application against a schema that has not been migrated.

## Docker setup

```powershell
docker compose config
docker compose up --build -d
docker compose ps
```

PostgreSQL and Qdrant data use named volumes. Service ports bind to loopback for local development; containers communicate over Compose's private network. `docker compose down` stops services while retaining data. `docker compose down -v` also removes data volumes.

## Application startup

For local development, install dependencies, configure `.env`, then run:

```powershell
python -m uvicorn app.main:app --app-dir backend --reload
```

Compose startup is shown above. Interactive API docs are at `http://localhost:8000/docs` when running. Apply migrations before calling authentication routes.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Application liveness |
| GET | `/api/v1/health/database` | Runs `SELECT 1`; responds 503 when PostgreSQL is not configured or unavailable |
| GET | `/api/v1/health/vector-db` | Queries Qdrant; responds 503 when unavailable |
| POST | `/api/v1/auth/register` | Register an account and return safe profile fields |
| POST | `/api/v1/auth/login` | OAuth2 password form (`username` is the email); returns a short-lived bearer access token |
| GET | `/api/v1/auth/me` | Return the profile for the active user represented by the bearer token |
| POST | `/api/v1/tenants` | Create a tenant and become its owner |
| GET | `/api/v1/tenants` | List the caller's active tenant memberships |
| GET | `/api/v1/tenants/{tenant_id}` | Read a tenant after active membership verification |
| PATCH | `/api/v1/tenants/{tenant_id}` | Update tenant name or description as an owner or admin |
| GET | `/api/v1/tenants/{tenant_id}/memberships` | List memberships as an owner or admin |
| POST | `/api/v1/tenants/{tenant_id}/memberships` | Add an existing active user; only owners can assign owner/admin roles |
| PATCH | `/api/v1/tenants/{tenant_id}/memberships/{membership_id}` | Change a membership role or deactivate it |
| POST | `/api/v1/tenants/{tenant_id}/documents/upload` | Upload a PDF as OWNER, ADMIN, or HR (multipart/form-data) |
| GET | `/api/v1/tenants/{tenant_id}/documents` | List tenant documents with `limit` and `offset` pagination |
| GET | `/api/v1/tenants/{tenant_id}/documents/{document_id}` | Read safe metadata for an active tenant member |
| GET | `/api/v1/tenants/{tenant_id}/documents/{document_id}/status` | Read ingestion status and sanitized failure code |
| POST | `/api/v1/tenants/{tenant_id}/retrieval/search` | Search tenant chunks the caller is authorized to read |
| POST | `/api/v1/tenants/{tenant_id}/generation/answer` | Generate a cited answer using only authorized retrieval results |

Service health responses do not expose credentials or connection details. Qdrant collection inspection is available as a helper; no collection or test documents are created. The registration and profile responses never include password hashes. Login uses form-encoded OAuth2-compatible fields.

Memberships are tenant-scoped, users may belong to multiple tenants, and duplicate active memberships are blocked by a database partial unique index. Tenant and user deletion is restricted while memberships exist. Deactivate memberships instead; an active tenant must retain at least one active owner.

### Document ingestion

Ingestion runs synchronously: bounded private file storage, PDF signature/parser validation, page text extraction, text cleanup, tokenizer-aware chunking, normalized local embeddings, Qdrant upsert, then PostgreSQL completion. The application does not connect to Qdrant or load the model during startup.

- **Collection**: `securerag_chunks`, cosine distance, with payload indexes for tenant, document, chunk ID, roles, users, classification, and public flag. Initialization is lazy and idempotent; incompatible vector dimensions or distance fail safely without collection recreation.
- **Embedding model**: `sentence-transformers/all-MiniLM-L6-v2` by default. It loads once on first ingestion. CPU is the default; set `EMBEDDING_DEVICE=cuda` when the installed PyTorch build and host support it. Weights download on first use unless cached. The chunker caps chunks to the model's actual input limit to prevent silent truncation; MiniLM commonly has a 256-token limit, so the effective size can be below the configured 800-token target.
- **Storage**: `DOCUMENT_STORAGE_PATH` defaults to `./var/documents`, outside the application package. Compose mounts a private persistent volume. Disk names are generated UUIDs; responses never expose storage paths.
- **Limits**: `MAX_UPLOAD_SIZE_BYTES` defaults to 20 MiB, `MAX_PDF_PAGES` to 500, and `MAX_FILENAME_LENGTH` to 255. Encrypted PDFs and scanned PDFs without extractable text are rejected; OCR is not included.
- **Consistency**: PostgreSQL and Qdrant do not share a transaction. If vector insertion or database finalization fails, the service attempts compensating deletion of deterministic point IDs and records a sanitized failure code. This is best-effort compensation, not cross-system atomicity.

Upload from PowerShell after obtaining a bearer token:

```powershell
$token = "<access-token>"
$tenantId = "<tenant-uuid>"
$pdfPath = "C:\docs\policy.pdf"
curl.exe -X POST "http://localhost:8000/api/v1/tenants/$tenantId/documents/upload" `
  -H "Authorization: Bearer $token" `
  -F "file=@$pdfPath;type=application/pdf" `
  -F "classification=1" `
  -F "is_public=false"
```

The tenant ID in the URL selects the context; active membership is verified, and stored tenant/uploader IDs come from the verified context. A repeated upload with the same server-computed SHA-256 in that tenant returns `409 Conflict`.

### Secure semantic retrieval

`POST /api/v1/tenants/{tenant_id}/retrieval/search` accepts `{"query":"What is the employee leave policy?","top_k":5}`. Queries are trimmed, must contain non-whitespace text, and are limited to 2,000 characters. `top_k` must be from 1 to 20. Extra body fields such as tenant ID, user ID, role, or ACL values are rejected.

The route requires a valid JWT and active membership in the active tenant. The URL tenant must match the verified `TenantContext`. Qdrant receives a server-side filter requiring that tenant and at least one ACL candidate match: `is_public=true`, the verified membership role in `allowed_roles`, or the verified user ID in `allowed_users`. It searches at most `min(top_k × 5, 100)` candidates.

PostgreSQL is authoritative after vector search: only `COMPLETED` documents belonging to the verified tenant are considered. A result is returned only when the current database ACL allows it: tenant-scoped public access, an explicit role entry, or an explicit user entry. Empty private ACLs deny access. Uploader status, OWNER/ADMIN titles, and classification levels do not grant permission. Classification has no clearance hierarchy in this phase. Stale Qdrant ACL payloads cannot grant access, and malformed or cross-tenant hits are discarded. The response contains only chunk/document identifiers, index, page references, text, and similarity score; it excludes raw Qdrant payloads and filesystem metadata.

Example response:

```json
{
  "query": "What is the employee leave policy?",
  "tenant_id": "<tenant-uuid>",
  "results": [
    {
      "chunk_id": "<chunk-uuid>",
      "document_id": "<document-uuid>",
      "chunk_index": 3,
      "page_numbers": [2],
      "text": "Eligible employees receive...",
      "similarity_score": 0.82
    }
  ],
  "result_count": 1
}
```

Set `DOCUMENT_STORAGE_PATH`, upload limits, `EMBEDDING_MODEL_NAME`, `EMBEDDING_DEVICE`, `EMBEDDING_BATCH_SIZE`, `EMBEDDING_CHUNK_SIZE`, `EMBEDDING_CHUNK_OVERLAP`, and `QDRANT_COLLECTION_NAME` in `.env` as needed.

### Grounded answer generation

`POST /api/v1/tenants/{tenant_id}/generation/answer` accepts the same query and `top_k` limits as secure retrieval. Example:

```json
{"query":"What is the annual leave policy?","top_k":5}
```

The route verifies the existing JWT and active tenant membership, then calls the Phase 5 `search_documents` service directly. It does not access Qdrant, document files, or any alternate source. Phase 5's PostgreSQL-verified `COMPLETED` documents and current ACLs are the only possible context. Empty authorized results return a fixed insufficient-information response without calling the LLM.

Configure `LLM_PROVIDER=openai_compatible`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS`, and `LLM_TIMEOUT_SECONDS`. Remote LLM endpoints must use HTTPS. Plain HTTP is accepted only when `APP_ENV=development` and the URL host is `localhost` or a loopback IP (`127.0.0.1`/`::1`); other private-network addresses are not treated as local. Provider redirects are disabled. The API key is optional at startup; if absent, a request with nonempty retrieval results returns sanitized HTTP 503. No LLM subscription is assumed. Bound context with `RAG_MAX_CONTEXT_TOKENS`, `RAG_MAX_CONTEXT_CHUNKS`, and `RAG_MAX_ANSWER_TOKENS`.

Context budgeting counts the UTF-8 byte lengths of the complete system and user message contents, including serialized JSON, question, delimiters, and a 256-token chat-framing reserve. This is conservative for byte-pair tokenizers and safely accounts for multilingual characters and JSON escaping, but is not an exact token count for every provider tokenizer or undocumented protocol framing. The builder includes whole chunks only and skips chunks that do not fit; it never silently truncates source identity or exceeds its configured estimate. Retrieved text is untrusted and passed separately from the system prompt. The model's citation labels must exactly match the labels it references; citations and page references are validated against their own authorized source before application-side metadata is returned. Invalid or unsupported references cause a safe abstention. Citation checks reduce attribution risk but do not prove semantic entailment; LLM grounding cannot guarantee hallucination elimination.

Example response:

```json
{"query":"What is the annual leave policy?","answer":"The available documents describe the leave policy [S1].","citations":[{"document_id":"<uuid>","chunk_id":"<uuid>","page_numbers":[2],"relevance_score":0.89}],"result_count":1,"grounded":true}
```

With no provider key, no authorized context, or a provider outage, no fabricated answer is returned. Generation tests mock the provider; no live provider request is made by the test suite.

### Audit logging

Phase 7 stores security and workflow events in PostgreSQL `audit_logs`. Login outcomes, verified tenant access/denials, existing tenant administration, document upload/ingestion, secure retrieval, and generation are recorded with a server correlation ID. No public audit-management endpoint is exposed. The internal query service requires verified `TenantContext`, enforces its tenant ID in every query, and supports event/actor/resource/outcome/date filters and stable pagination.

Audit metadata uses a strict safe-field allowlist, flat primitive values, bounded strings/lists, and a 4 KiB serialized cap. Passwords, bearer tokens, prompts, document contents, filenames, storage paths, query strings, generated answers, IP addresses, and user agents are not recorded. The server generates a UUID request ID and returns it as `X-Request-ID`; client-supplied IDs are ignored so clients cannot collide with event idempotency keys.

Audit writes use the request's SQLAlchemy session, reject pending business changes, and commit explicitly; request/event/action combinations are deduplicated. Existing business services commit their own transactions before success events are written. Thus a successful business operation can already be committed if its later audit write fails; the API then returns a sanitized service error and does not claim that the event was recorded. This is visible failure handling, not a transactional outbox or cross-operation atomicity. When PostgreSQL is unavailable, audit-required requests fail visibly.

The application ORM rejects audit record updates/deletes, and migration `0004_create_audit_logs` installs a PostgreSQL trigger rejecting SQL UPDATE/DELETE. These controls prevent ordinary application mutation but are not absolute tamper-proof storage: a database owner/superuser can alter schema or disable triggers. Production deployments should use a runtime database role that cannot alter the audit table or trigger and should apply the migration with a separate deployment role. No hash chain is claimed.

## Document permissions

Only verified `OWNER` and `ADMIN` members can read or replace a document's permission set:

- `GET /api/v1/tenants/{tenant_id}/documents/{document_id}/permissions`
- `PUT /api/v1/tenants/{tenant_id}/documents/{document_id}/permissions`

The PUT body replaces the complete ACL. For example:

```json
{
  "classification": "CONFIDENTIAL",
  "is_public": false,
  "allowed_roles": ["HR", "MANAGER"],
  "allowed_users": ["2ec4a8d3-0177-4dc2-bf69-0ae610e226df"]
}
```

An empty private ACL (`is_public: false`, empty role and user arrays) denies access to everyone. OWNER/ADMIN status, uploader status, and classification do not grant document access. Permission administrators can manage an ACL without receiving access to its contents. User assignments must refer to active users with active membership in the same tenant. Only completed documents can be changed so ingestion cannot race a vector rewrite; incomplete documents can still be inspected. A revoke can be expressed by replacing the ACL with a private empty ACL.

PostgreSQL is authoritative for retrieval authorization. Each replacement commits the document ACL, `permission_updated_at`, and a unique per-tenant/document `document_permission_sync` outbox row in one database transaction. The route then attempts Qdrant synchronization using a filter requiring both the verified tenant ID and document ID. The update changes only ACL payload fields and uploader metadata; chunk text, IDs, and vectors are untouched. A confirmed Qdrant update marks the outbox complete. On Qdrant failure the task remains `PENDING` with a sanitized code and attempt count; the API returns the new PostgreSQL permissions with that pending status. Revocation is effective immediately because Phase 5 rechecks PostgreSQL before returning chunks. New grants may return fewer results until Qdrant catches up.

The internal `process_pending_permission_syncs(session, limit=50)` service can be invoked by a future worker or controlled maintenance process; no public retry endpoint or scheduler is included. It claims tasks with row locking, recovers expired processing leases, coalesces each document to its latest revision, and reloads the current PostgreSQL ACL before each retry. Permission updates and synchronization outcomes use the Phase 7 audit service without storing user IDs from ACL arrays or document contents. Migration `0005_document_permissions` adds the timestamp, outbox, and new audit event types. Downgrade removes the permission structures while retaining the expanded audit event constraint so append-only Phase 8 audit records remain valid.

Apply new migrations using the existing Alembic command after updating the environment or container:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m alembic -c alembic.ini upgrade head
```

## Development seed data

The seed command creates fictional accounts for TechNova Solutions, FinEdge Corporation, and HealthBridge Systems. It is explicit and idempotent; it is never run by application startup. Set `APP_ENV=development` and provide a local-only password through `DEV_SEED_PASSWORD`. The same password is applied only to users the seed command creates; it never prints or changes passwords for existing users.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
$secureSeedPassword = Read-Host "Local development seed password" -AsSecureString
$env:DEV_SEED_PASSWORD = [System.Net.NetworkCredential]::new("", $secureSeedPassword).Password
try {
    python -m app.seed_development
}
finally {
    Remove-Item Env:DEV_SEED_PASSWORD -ErrorAction SilentlyContinue
}
```

The command refuses to run when `APP_ENV=production`. Use only a development password and never reuse production credentials.

## Testing

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "backend")
python -m pytest backend\tests -q
```

Tests use isolated in-memory SQLite databases and mocks; they need no live PostgreSQL, Qdrant, or model download.

## Clean source archive

Create a source-only ZIP and validate its entries with:

```powershell
python scripts/package_release.py
```

The archive is written to `dist/SecureRAG-phase9.zip`. Packaging uses an explicit source allowlist and rejects environment secret files, private-key material, caches, virtual environments, and temporary artifacts. `.gitignore` and `.dockerignore` also exclude local secrets, but they are not used as the ZIP safety boundary.

## Troubleshooting

- **Database URL missing**: define `DATABASE_URL` in `.env`; the database health route returns 503 until configured and reachable.
- **Database refused connection**: check `docker compose ps`; local Python uses host `localhost`, while backend containers use `postgres`.
- **Qdrant unavailable**: check Compose service health; use `localhost:6333` locally or `qdrant:6333` from the backend container.
- **Authentication returns 503**: set a strong `SECRET_KEY` in `.env` and restart the backend.
- **Production startup rejects configuration**: set `APP_ENV=production`, `DEBUG=false`, a unique randomly generated `SECRET_KEY`, and `TRUSTED_HOSTS` to the exact public hostname(s); apply all Alembic migrations before serving traffic.
- **Authentication returns 429**: wait for the `Retry-After` interval. The database-backed limits are shared across backend workers; configure additional edge limits for deployments behind a reverse proxy.
- **Authentication routes fail with database errors**: ensure PostgreSQL is reachable and `python -m alembic -c alembic.ini upgrade head` has completed.
- **Embedding model unavailable**: check `EMBEDDING_MODEL_NAME`, device/PyTorch compatibility, first-use network access, and the Hugging Face model cache.
- **Document upload returns 503**: the status endpoint exposes only a sanitized stage code. Verify the Alembic migration, Qdrant availability and collection dimension, storage permissions, and model loading.
- **PDF rejected**: confirm it is a real non-encrypted `.pdf` with `application/pdf` MIME and selectable text. OCR is not supported.
- **Qdrant dimension conflict**: the existing collection must use cosine distance and the selected model's discovered dimension; the service will not delete or recreate an incompatible collection.
- **Import errors**: run from repository root and set `PYTHONPATH` to `backend`.
- **Docker command missing**: install and start Docker Desktop, then retry Compose commands.

## HTTP request body limits

The ASGI application counts incoming request bytes as they are received and stops parsing once the configured upload size plus 1 MiB of multipart/form overhead is exceeded. It also rejects malformed or oversized declared `Content-Length` values before multipart parsing. The document storage layer continues to enforce the exact per-file upload limit.

Deployments behind a trusted reverse proxy should configure an independent request-body limit at that proxy (at least the application's per-file limit plus the documented multipart allowance), along with request timeouts. The application byte counter remains enabled as defense in depth; proxy limits do not replace it.

## Production deployment security

Production settings fail closed unless PostgreSQL, a strong non-placeholder `SECRET_KEY`, an authenticated HTTPS `QDRANT_URL`, a nonempty `QDRANT_API_KEY`, and exact `TRUSTED_HOSTS` are configured. Interactive OpenAPI/Swagger/ReDoc documentation is disabled in production. Configure secrets through the deployment secret manager, use TLS for browser ingress and external database/vector traffic, and keep PostgreSQL and Qdrant on private networks. Configure the reverse proxy to validate the public Host, strip untrusted forwarded headers, enforce request/time/concurrency limits, and forward only from known ingress. If `TRUSTED_PROXY_CIDRS` is set, configure the proxy to append the verified client address; all other forwarding sources are ignored. Rate limits for login and registration are shared through PostgreSQL; add gateway limits for document/model workloads. Back up PostgreSQL and Qdrant consistently and rehearse restore procedures before deployment.
