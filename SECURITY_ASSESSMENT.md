# SecureRAG Phase 9 Security Assessment

Assessment date: 2026-11-19
Scope: application source, unit tests, configuration, Docker Compose, and offline Alembic validation in this workspace. This is not a certification or a production penetration test.

## Executive summary

The review confirmed that the main authorization boundaries are implemented: JWT verification uses a fixed HS256 algorithm and required claims; tenant context is loaded from active database membership; retrieval applies a tenant filter in Qdrant and rechecks current document ACL and completion status in PostgreSQL; permissions are tenant-scoped and synchronize through a revisioned outbox; generation only receives Phase 5 results; audit metadata is allowlisted and permission changes share a transaction with audit writes.

Two availability/configuration weaknesses were addressed. Production settings now reject debug mode, and a pure ASGI middleware counts bytes in every received request body, including chunked requests without `Content-Length`, and stops downstream parsing once the configured limit is exceeded. Declared lengths are validated early, and the exact per-file upload limit remains enforced by private storage.

No Python 3.12 interpreter, Docker runtime, live PostgreSQL, Qdrant, or LLM was available. The complete pytest run was attempted under Python 3.14.7 and collected **356 cases**, but stalled at the existing TestClient-based login/audit integration test; a minimal FastAPI app reproduced the runtime stall. The new request-body tests use a direct ASGI harness and completed without TestClient. The Python 3.11 installation lacks pytest and project dependencies. The full run emitted no final pytest summary: passed, failed, skipped and deselected totals for that run are therefore unavailable (deselected 0). This assessment does not claim complete regression verification or production readiness.

The repository now has a GitHub Actions workflow at `.github/workflows/tests.yml`. It provisions Python 3.12 on Ubuntu for pushes and pull requests, installs `backend/requirements.txt` with pip caching, runs `python -m pytest backend/tests -vv` without selection filters, then builds and validates a source archive. The job timeout is 45 minutes. This workflow has not run in GitHub Actions from this workspace, so it establishes a reproducible supported environment but does not provide a Python 3.12 test result yet.

## Scope and architecture / trust boundaries

- **Authentication:** `auth/security.py` signs/verifies short-lived JWTs; `auth/dependencies.py` loads the user by the verified subject and checks current active status. `auth/routes.py` handles login and registration.
- **Tenant authorization:** `tenants/dependencies.py` derives `TenantContext` from the verified user and current active membership. Tenant routes compare path tenant IDs to that context.
- **Document ingestion:** `documents/routes.py` checks tenant context and role, validates ACL assignments, then writes uploads through `documents/storage.py` into private generated-name storage. `documents/service.py` validates/extracts PDFs, chunks and embeds, and writes vectors.
- **PostgreSQL:** `database/postgres.py` owns the pooled engine/session lifecycle. User, tenant, document, audit, and permission outbox state is queried with SQLAlchemy. PostgreSQL document ACLs are authoritative.
- **Qdrant:** ingestion and permission synchronization use the shared client. Retrieval uses a Qdrant tenant/ACL filter, then independently validates payload identity and rechecks the document in PostgreSQL. Synchronization is scoped by tenant and document and uses the latest database ACL.
- **LLM boundary:** the OpenAI-compatible provider uses configured HTTPS for remote endpoints, allows loopback HTTP only in development, disables redirects, bounds timeouts/retries, and does not log prompts or API keys. Generation has no tools and consumes authorized Phase 5 context only.
- **Audit persistence:** the audit service sanitizes metadata and uses request/event/action uniqueness. ORM mutation guards and the PostgreSQL migration trigger prevent normal updates/deletes; database owners/superusers can still bypass database protections.
- **Permission synchronization:** ACL changes and outbox revisions commit atomically with permission audit events. Qdrant synchronization is subsequent, idempotent, and retryable; reads continue to authorize against PostgreSQL.

## Methodology and test categories

Reviewed route and service implementations, configuration, Docker Compose, dependency declarations, Alembic migrations, and existing security tests. Ran bounded tests with synthetic users/tenants, SQLite, and mocked Qdrant/LLM clients. No destructive testing or uncontrolled load testing was performed.

Labels used below: **Confirmed vulnerability** means a demonstrable unsafe code path existed; **Potential weakness** means impact depends on deployment or was not integration-tested; **Not tested** means the required external runtime was unavailable; **Not applicable** means the feature is absent or not public.

## Confirmed findings

### SEC-01 — Production debug configuration could enable traceback responses

- **Severity:** Medium
- **Status:** Confirmed vulnerability; fixed.
- **Affected code:** `backend/app/config.py`; application construction in `backend/app/main.py`.
- **Impact:** Before the fix, `APP_ENV=production` and `DEBUG=true` passed settings validation and were passed to FastAPI. FastAPI/Starlette debug error handling may return traceback details for unhandled exceptions.
- **Reproduction:** Construct settings with production environment and debug enabled; the old settings validator had no production guard, and `create_app()` passed the value directly to `FastAPI(debug=...)`.
- **Expected:** Production debug configuration fails closed.
- **Actual before fix:** The configuration was accepted.
- **Root cause:** Environment and debug settings were validated independently.
- **Fix:** Settings validation rejects `DEBUG=true` for `production` and `prod` values, case-insensitively.
- **Regression test:** `backend/tests/security/test_configuration_security.py` covers production, accepted production with debug disabled, case/whitespace normalization, and `prod` alias.

### SEC-02 — Multipart spooling was not protected by a total request-body limit

- **Severity:** Medium
- **Status:** Confirmed availability weakness; fixed for ASGI-received request bodies.
- **Affected code:** `backend/app/main.py`, `backend/app/documents/routes.py`, `backend/app/documents/storage.py`.
- **Impact:** The existing upload copy loop limited each file, but FastAPI/Starlette parses multipart input and spools uploaded parts before the endpoint calls that loop. Oversized declared request bodies could therefore consume temporary storage before the application-level upload cap rejected them.
- **Reproduction:** The route accepts `UploadFile`; the configured byte limit was checked only in `store_upload()` after multipart parsing. No earlier request body check existed.
- **Expected:** Count actual body bytes, including chunked transfer, and stop downstream processing once the total request limit is exceeded.
- **Actual before fix:** Declared request size was not checked by application middleware before the route parser.
- **Root cause:** The file-copy cap was mistaken for an HTTP request-body cap.
- **Fix:** Pure ASGI `RequestBodyLimitMiddleware` counts the length of each received request body chunk without buffering it. On overflow it emits one sanitized 413, raises an internal stop signal, suppresses downstream error responses, and prevents later chunks reaching multipart parsing. `RequestCorrelationMiddleware` wraps it and adds request ID/security headers to early and streamed error responses. Total allowance is the per-file limit plus 1 MiB multipart/form overhead; the exact per-file storage check is unchanged.
- **Regression tests:** `backend/tests/security/test_configuration_security.py` tests declared length validation; `backend/tests/security/test_request_body_limit.py` tests valid declared requests, invalid and oversized lengths, chunked bodies, multi-chunk counting, disconnect handling, oversized multipart, normal JSON, headers on 413, and avoiding duplicate responses.
- **Residual limitation:** An ASGI server must materialize an individual incoming chunk before the middleware can count it. Set bounded ASGI server and trusted reverse-proxy request sizes/timeouts independently; these limits supplement rather than replace the application counter.

## Controls reviewed and attack scenarios

- **Authentication/JWT:** Existing tests cover missing/malformed/expired tokens, required claims, signature and algorithm tampering, strong signing secret, inactive/deleted accounts, and database-authoritative identity. JWT-focused run: 15 passed. Session revocation is **not applicable**; no revocation/session store is implemented, so tokens remain valid until expiry unless the account is inactive/deleted.
- **Tenant/IDOR:** Existing service and route tests cover membership requirements, tenant mismatch, cross-tenant document hiding, cross-tenant ACL assignments, and tenant-scoped query/filter behavior. SQLite/service-level retrieval tests confirm foreign-tenant and stale-Qdrant candidates are removed before response construction.
- **Document ingestion:** Existing tests cover PDF signature/MIME validation, malformed/encrypted/scanned PDFs, page/file limits, traversal-safe generated storage names, cleanup, duplicate files, ACL membership validation, and Qdrant payload metadata. Document unit tests: 38 passed. Live filesystem permission behavior on production Linux was not tested here.
- **Retrieval/ACL:** Existing tests verify Qdrant tenant and ACL filters plus PostgreSQL authorization for `COMPLETED` documents; privileged role, uploader, classification, stale payload, missing document, and malformed payload do not independently grant access. Retrieval unit/service subset: 25 passed. HTTP response-level integration tests were blocked by the local TestClient stall.
- **Permission management:** Existing tests exercise role restrictions, cross-tenant target documents/users, ACL validation, atomic audit/outbox updates, revision/lease behavior, and stale-Qdrant revocation checks. Transaction-focused unit tests passed as part of the selected permission run; HTTP route tests were blocked by the same TestClient limitation.
- **RAG/prompt injection:** Existing tests keep untrusted source text separate from system instructions, validate exact citation labels/page metadata, reject invented IDs, abstain on invalid/unsupported citations, and prevent provider calls for empty retrieval. Generation service/provider subset: 54 passed, 6 route/auth tests deselected because they require TestClient.
- **Audit:** Existing tests cover metadata allowlisting/rejection, tenant query filtering, idempotency, transaction-aware staging, persistence failures, ORM append-only behavior, and migration trigger declarations. Audit non-HTTP subset: 32 passed, 13 integration tests deselected due TestClient.
- **SQL injection:** SQLAlchemy queries inspected use expression-bound parameters; no user-controlled SQL string construction was found in the reviewed paths. Search input is passed to embedding generation, not interpolated into SQL. No destructive live database probing was performed.
- **External services:** LLM endpoint validation and redirect behavior have unit coverage. PostgreSQL and Qdrant ports were unavailable, so no live integration was performed. Compose publishes PostgreSQL, Qdrant, and API ports on loopback by default; deployment-level firewall/network policy was not tested.
- **Secrets/dependencies:** The real `.env` file was removed without reading or displaying its contents. `.env.example` now contains blank or placeholder-only sensitive settings. The target GitHub repository was empty before this initial commit; no prior history exists to scan. The staged file scan found no private-key markers, GitHub token patterns, or AWS access key patterns. No dependency vulnerability scanner was installed or run; dependency versions were inspected but not checked against a live advisory database.

## Potential weaknesses and untested areas

1. **Potential weakness — rate limiting:** No application-level rate limiter was found for login, upload, embedding, retrieval, generation, or permission synchronization. Do not run load tests against production. Deploy bounded rate limits and concurrency quotas at a trusted gateway or server layer, especially for login and model/embedding workloads.
2. **Potential weakness — ingress limits:** Configure a bounded request size and timeout in the trusted reverse proxy and ASGI server; no reverse proxy is configured in this local Compose stack.
3. **Potential weakness — host validation:** No `TrustedHostMiddleware` or trusted-proxy policy was found. This matters if deployed behind a proxy that uses `Host` to construct absolute URLs or cache keys; impact depends on that deployment.
4. **Potential weakness — Qdrant credentials:** Qdrant API-key configuration exists, but Docker Compose permits an empty key and relies on a private service network plus loopback host binding. Production deployments should configure Qdrant authentication and network restrictions.
5. **Potential weakness — database-owner tampering:** Audit triggers and application guards provide append-only behavior for normal roles, not protection from database owners/superusers or privileged infrastructure operators.
6. **Not tested — concurrent load/resource exhaustion:** No load, parser stress, model concurrency, or rate-limit tests were run. The PDF upload/page limits and request byte cap bound individual input sizes but do not bound concurrent work.
7. **Not tested — live integration:** PostgreSQL (`127.0.0.1:5432`) and Qdrant (`127.0.0.1:6333`) were unavailable. No live LLM request was made. Docker was unavailable.
8. **Not tested — full regression suite:** Python 3.12 is not installed. Python 3.11 is present but has neither pytest nor project dependencies; `py -3.12 --version` found no suitable runtime, and a Python 3.12 user-scope installation attempt could not launch. The suite ran under Python 3.14.7 and collected **356 cases**, then stalled at `backend/tests/test_audit.py::test_login_success_and_failure_are_audited_without_credentials`. A minimal FastAPI TestClient request reproduced the stall. Environment versions: Python 3.14.7, pytest 8.4.2, FastAPI 0.163.2, Starlette 1.10.0, HTTPX 0.28.1, `httpx2` 2.16.2 (Starlette 1.10's TestClient selected this distribution), AnyIO 4.16.0, SQLAlchemy 2.1.9, Alembic 1.20.1, qdrant-client 1.20.0. The trace blocks in Windows asyncio `_fallback_socketpair()` while AnyIO starts the TestClient portal; changing to the deprecated Selector policy produced the same block. The full run had 0 deselections and was interrupted before pytest emitted a summary. Full-run passed/failed/skipped counts are unavailable; no full-suite pass is claimed. Dependencies were not force-upgraded for this verification.
9. **Not tested — pre-existing repository history:** The remote was empty before the initial commit, so there was no earlier history to inspect for secrets. `.env` is absent and ignored by `.gitignore`; if credentials from the previously removed local file were ever shared elsewhere, rotate them.

## Verification results

- `python -m pytest backend/tests/security -vv` — 42 passed, 0 failed, 0 skipped, 0 deselected in 0.45 seconds. Includes direct ASGI request-body tests and archive packaging checks.
- `python scripts/package_release.py` — passed; built and validated a 105-entry source-only ZIP, including the CI workflow. The archive contains `.env.example` as its only environment file and excludes `.env`, key material, generated caches, virtual environments, and temporary artifacts.
- `python scripts/package_release.py --validate dist/SecureRAG-phase9.zip` — archive validator passed (105 entries).
- Exposure handling: the root `.env` was deleted without inspecting or printing its contents. Previously non-placeholder password/database values in `.env.example` were replaced. The remote was confirmed empty before the initial commit, so exposure through that repository is not indicated. Prior distribution elsewhere cannot be determined; if any removed or replaced value was a real credential and was shared elsewhere, rotate it. No credential was used or disclosed during this work.
- `python -m compileall backend/app backend/tests` — passed.
- `python -m pytest backend/tests/security/test_request_body_limit.py -vv` — 10 passed; direct ASGI tests, no TestClient.
- `python -m pytest backend/tests/test_jwt.py -vv` — 15 passed.
- `python -m pytest backend/tests/test_documents.py -vv` — 38 passed.
- Tenant database/index tests — 4 passed.
- Generation service/provider selection — 54 passed, 6 HTTP route/auth tests deselected for this isolated unit run; they remain part of the full suite attempt.
- Retrieval service selection — 25 passed, 6 HTTP route/auth tests deselected for this isolated unit run; they remain part of the full suite attempt.
- Audit non-HTTP selection — 32 passed, 13 HTTP integration tests deselected for this isolated unit run; they remain part of the full suite attempt.
- Permission atomicity/synchronization regression tests — 7 passed.
- Full `python -m pytest backend/tests -vv` — collected 356 and was interrupted after 30 seconds at `test_login_success_and_failure_are_audited_without_credentials`; elapsed wall time before interruption was about 30 seconds. A prior minimal FastAPI TestClient reproduction and faulthandler trace showed the TestClient worker blocked in Python's Windows asyncio `_fallback_socketpair()` while AnyIO started its blocking portal. The current focused reproduction again stalled before the test completed, consistent with the fixture's `with TestClient(auth_app)` startup. Pytest emitted no terminal summary, so full-run passed/failed/skipped totals are unavailable. The full command deselected 0 tests. No full-suite pass is claimed.
- Alembic history — valid through `0005_document_permissions`.
- Offline upgrade SQL — generated successfully.
- Offline downgrade SQL — generated successfully.
- API route generation — FastAPI OpenAPI schema generated 17 API paths, including permission, retrieval, and generation routes.
- Docker Compose — YAML parsed; services are `postgres`, `qdrant`, and `backend`. Docker CLI/runtime validation was unavailable.
- Live PostgreSQL, Qdrant, and LLM checks — not performed.
- Python 3.12 — unavailable; `py -3.12 --version` returned no suitable runtime. All executable Python checks above used Python 3.14.7. Python 3.11 lacked pytest and project dependencies. The new CI workflow is the path for obtaining the required Python 3.12 result; it has not yet run.

## Production recommendations and assessment

- Keep `APP_ENV=production` and `DEBUG=false`; the application now rejects the contrary configuration.
- Enforce request-body limits, rate limits, and concurrency limits at the reverse proxy/ASGI server, including chunked requests.
- Configure Qdrant authentication and deny public network access; keep PostgreSQL private.
- Configure trusted host/proxy rules for the actual deployment topology and terminate TLS at a trusted ingress.
- Run dependency advisory scanning in CI and secret scanning against repository history; rotate any credential ever committed.
- Run integration tests against disposable PostgreSQL and Qdrant instances and fix the local TestClient runtime issue before treating HTTP regression coverage as complete.
- Re-run the complete suite in the supported Python 3.12 environment before closing Phase 9; that interpreter could not be made available here.
- Establish monitored backups and recovery for audit data; database-owner access can bypass trigger protections.

**Assessment:** The inspected source and completed isolated tests show meaningful tenant and document ACL controls. Two findings were fixed or partially mitigated as described. Because full HTTP tests stalled, external services and deployment controls were unavailable, and rate/concurrency limits were not verified, this application is **not assessed as production-ready by this review**. This report is not a penetration-test certification.
