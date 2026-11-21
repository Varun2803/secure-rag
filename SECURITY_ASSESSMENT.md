# SecureRAG Phase 9 Security Assessment

## Phase 10.8 — Security hardening and release review (current)

Assessment date: 2026-11-21. This section supersedes Phase 9 conclusions where it describes newer code or verification. This remains a source-code review and automated test assessment, not a penetration-test certification or production validation.

### Findings register and disposition

| ID | Severity | Finding and evidence | Exploit / operational impact | Disposition |
|---|---|---|---|---|
| SEC10-01 | High | Production settings previously did not validate `SECRET_KEY`; strength was checked later only when JWT encode/decode ran (`backend/app/config.py`, `backend/app/auth/security.py`). | A production service could initialize with missing or placeholder JWT signing material and fail at authentication time. | Fixed: production startup rejects missing, short, low-diversity, or known-placeholder keys, and rejects `DEBUG=true`. Operators must still generate an independent CSPRNG secret. |
| SEC10-02 | Medium | Login and registration routes previously had no shared throttle (`backend/app/auth/routes.py`). | Password guessing and registration attempts could consume password-hash and database resources. | Fixed: atomic PostgreSQL fixed-window counters; 10 login attempts/client/60 seconds and 5 registrations/client/hour; stale-row cleanup and a 100,000-row capacity ceiling; limiter storage failures fail closed with 503. Regression tests cover throttling, window reset, proxy-header trust, capacity and API responses. |
| SEC10-03 | Medium | No host validation or explicit proxy trust policy existed (`backend/app/main.py`). | A deployment with unsafe proxy/Host handling could be exposed to host-header confusion; impact depended on ingress topology. | Fixed for host validation: production requires exact `TRUSTED_HOSTS` and installs `TrustedHostMiddleware`; production disables OpenAPI, Swagger and ReDoc. Forwarded client IP is ignored unless the direct peer matches explicit `TRUSTED_PROXY_CIDRS`; proxy header normalization remains an operational requirement. |
| SEC10-04 | High | Production accepted empty Qdrant API key and HTTP Qdrant URLs although the client sends API-key authorization (`backend/app/config.py`, `backend/app/database/qdrant.py`). | Credentials could be sent without transport protection, or a production vector service could run unauthenticated. | Fixed at configuration boundary: production requires PostgreSQL `DATABASE_URL`, non-empty `QDRANT_API_KEY`, and HTTPS `QDRANT_URL`; plain HTTP is restricted to localhost/loopback or the Compose `qdrant` service in development. Compose now configures the Qdrant server key and allows a separate container URL. |

### Current verification snapshot

- Environment: local Windows, Python 3.14.7; FastAPI 0.163.2, Starlette 1.10.0, HTTPX 0.28.1, AnyIO 4.16.0, SQLAlchemy 2.1.9, Alembic 1.20.1, psycopg 3.3.9, qdrant-client 1.20.0, Pydantic 2.14.3, pydantic-settings 2.16.3, PyJWT 2.15.1, pwdlib 0.4.2, pytest 8.4.2. Node v24.19.0, npm 11.17.0; frontend versions are locked in `frontend/package-lock.json`.
- `python -m compileall backend/app backend/tests`: passed.
- Security suite: **57 passed** on Python 3.14.7, including request-byte/streaming, archive, configuration, authorization-boundary and provider/citation tests.
- Authentication rate-limit unit tests (database-backed SQLite behavior): **4 passed**. The route-level HTTP tests are present but local execution is affected by the Windows Python 3.14 event-loop socket-pair stall described below; supported Python 3.12 CI remains the required verification.
- Frontend: `npm run typecheck`, `npm run lint`, `npm test` (**137 passed, 20 files**), and `npm run build` succeeded. Vite emitted existing dependency annotation and bundle-size warnings; the build completed successfully.
- Backend collection: **378 tests** were collected in the final local run. `python -m pytest backend/tests -vv` stalled at `backend/tests/test_audit.py::test_login_success_and_failure_are_audited_without_credentials` under Python 3.14.7 before a final summary. The runtime trace is the Windows asyncio socket-pair `accept()` stall documented in Phase 9. No tests were deselected to produce a pass.
- GitHub Actions run 15, commit `faa7e2ae63dec9957e614e17b829cce386f20ab1`, used Python 3.12.16 and collected 378 tests: **376 passed, 2 failed, 0 skipped, 0 deselected** in 25.51 seconds. The failures were OpenAPI path assumptions (`test_openapi_available` and `test_permission_routes_are_registered_and_unauthenticated_is_rejected`): development documentation had inadvertently moved from `/openapi.json` to `/api/v1/openapi.json` while production docs were disabled. The development path was restored to `/openapi.json`; corrected-commit CI is pending and no successful Phase 10.8 full-suite result is claimed yet.
- Alembic history is valid through `0006_auth_rate_limits`; offline upgrade and downgrade SQL both generated successfully. No live migration was run.
- FastAPI import/OpenAPI generation succeeded (17 paths in development configuration). Docker Compose YAML parsed and showed `postgres`, `qdrant`, and `backend`; the Docker CLI is unavailable, so `docker compose config` and container tests were not run.
- Release packaging validator passed with **108 source entries** after the final changes; `.env` is absent.
- No live PostgreSQL, Qdrant, LLM, Docker, reverse-proxy, TLS, load or deployment integration was performed. Local Python 3.12 is not installed. The existing GitHub Actions job uses Python 3.12; this commit’s workflow result must be recorded before treating the supported-runtime backend suite as verified.

### Production conditions and residual risks

- Set `APP_ENV=production`, `DEBUG=false`, a unique randomly generated `SECRET_KEY`, PostgreSQL `DATABASE_URL`, HTTPS `QDRANT_URL`, `QDRANT_API_KEY`, and exact comma-separated `TRUSTED_HOSTS` before startup. Secrets must be delivered by the deployment secret manager, never committed in `.env` or printed.
- If behind a proxy, set `TRUSTED_PROXY_CIDRS` only to the actual ingress source networks. The proxy must validate `Host`, strip untrusted forwarded headers, append the verified client address, terminate HTTPS, enforce request-size/time/concurrency limits, and forward only to the application. The ASGI byte cap and per-file upload cap remain active.
- Auth throttling uses shared PostgreSQL state, but does not replace edge rate/concurrency controls for PDF parsing, embeddings, retrieval, generation or other resource-heavy routes. At the 100,000 bucket cap, new auth clients fail closed until cleanup frees capacity; monitor this and size/retain the table operationally.
- Keep PostgreSQL and Qdrant private; configure database TLS when crossing an untrusted network, authenticated HTTPS to remote Qdrant, monitored backups, and rehearsed restores. Audit append-only triggers do not prevent a privileged database owner/superuser from tampering.
- Existing tenant and PostgreSQL ACL checks, stale-Qdrant defense, generation context boundary, citation validation, memory-only frontend tokens, tenant-scoped query keys/cancellation, and escaped React text rendering remain in place according to source review and existing tests. LLM grounding cannot prove semantic entailment or eliminate prompt-injection effects.
- No dependency advisory scanner or independent secret scanner against all historical commit objects was run as part of this local pass. The working tree has no `.env`; production credentials and build artifacts are not included by the release archive policy.

**Current release verdict: Not ready — a supported-runtime full backend run and live deployment validation remain outstanding.** This assessment does not claim production readiness or penetration-test certification.

Assessment date: 2026-11-19
Scope: application source, unit tests, configuration, Docker Compose, and offline Alembic validation in this workspace. This is not a certification or a production penetration test.

## Executive summary

The review confirmed that the main authorization boundaries are implemented: JWT verification uses a fixed HS256 algorithm and required claims; tenant context is loaded from active database membership; retrieval applies a tenant filter in Qdrant and rechecks current document ACL and completion status in PostgreSQL; permissions are tenant-scoped and synchronize through a revisioned outbox; generation only receives Phase 5 results; audit metadata is allowlisted and permission changes share a transaction with audit writes.

Two availability/configuration weaknesses were addressed. Production settings now reject debug mode, and a pure ASGI middleware counts bytes in every received request body, including chunked requests without `Content-Length`, and stops downstream parsing once the configured limit is exceeded. Declared lengths are validated early, and the exact per-file upload limit remains enforced by private storage.

Local Python 3.12 was unavailable, and the Windows Python 3.14 full-suite attempt still stalls in TestClient setup. The new Python 3.12 GitHub Actions run on Ubuntu completed all **356 tests** successfully. The first CI attempt exposed one isolated test fixture issue; it was corrected without changing the assertion or application behavior, and the follow-up run passed. Live infrastructure and deployment integrations remain unverified; this assessment is not a penetration-test certification or production-readiness approval.

The GitHub Actions workflow at `.github/workflows/tests.yml` provisions Python 3.12 on Ubuntu for pushes and pull requests, installs `backend/requirements.txt` with pip caching, records Python and installed package versions, runs `python -m pytest backend/tests -vv` without selection filters, then builds and validates a source archive. Its timeout is 45 minutes. Run 2 passed on commit `7e5125b5d4c57d7687ec23eb5b3adb8b68456a92`. Run 3 passed on commit `20491328b69790e87e7a7b58775e1b4768539a20`; its [workflow result](https://github.com/Varun2803/secure-rag/actions/runs/44486428709) includes explicit `python --version` and `pip freeze` output.

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
- **Retrieval/ACL:** Existing tests verify Qdrant tenant and ACL filters plus PostgreSQL authorization for `COMPLETED` documents; privileged role, uploader, classification, stale payload, missing document, and malformed payload do not independently grant access. Retrieval unit/service subset: 25 passed in an isolated earlier run. HTTP tests pass in the full Python 3.12 CI suite; local Windows/Python 3.14 TestClient execution remains unreliable.
- **Permission management:** Tests cover role restrictions, cross-tenant targets, ACL validation, atomic audit/outbox updates, revision/lease behavior, and stale-Qdrant revocation. The full Python 3.12 CI run passed these tests; isolated transaction tests also passed in an earlier run. Local Windows/Python 3.14 TestClient execution remains unreliable.
- **RAG/prompt injection:** Existing tests keep untrusted source text separate from system instructions, validate exact citation labels/page metadata, reject invented IDs, abstain on invalid/unsupported citations, and prevent provider calls for empty retrieval. All generation tests passed in the full Python 3.12 CI run. Earlier selected service/provider run: 54 passed and 6 route/auth tests deselected; those deselections were only in that isolated run, not the full run.
- **Audit:** Existing tests cover metadata allowlisting/rejection, tenant query filtering, idempotency, transaction-aware staging, persistence failures, ORM append-only behavior, login and tenant event behavior. All audit tests passed in the full Python 3.12 CI run. An earlier isolated non-HTTP selection had 32 passed and 13 deselected; those deselections were not used in the full CI command.
- **SQL injection:** SQLAlchemy queries inspected use expression-bound parameters; no user-controlled SQL string construction was found in the reviewed paths. Search input is passed to embedding generation, not interpolated into SQL. No destructive live database probing was performed.
- **External services:** LLM endpoint validation and redirect behavior have unit coverage. PostgreSQL and Qdrant ports were unavailable, so no live integration was performed. Compose publishes PostgreSQL, Qdrant, and API ports on loopback by default; deployment-level firewall/network policy was not tested.
- **Secrets/dependencies:** The real `.env` file was removed without reading or displaying its contents. `.env.example` has blank or placeholder-only sensitive settings. The target GitHub repository was empty before the initial commit; the staged scan found no private-key markers, GitHub token patterns, or AWS access key patterns. No dependency vulnerability scanner was run.

## Potential weaknesses and untested areas

1. **Potential weakness — rate limiting coverage:** Shared application-level throttling now protects login (10 per client per 60 seconds) and registration (5 per client per hour). Upload, embedding, retrieval, generation, and permission synchronization do not have application-level rate limits or concurrency quotas. Apply bounded limits at a trusted gateway/server, especially for resource-intensive endpoints; do not run uncontrolled load tests against production.
2. **Deployment requirement — ingress limits and proxy trust:** The application counts actual ASGI request bytes and limits each uploaded file, but the ASGI server and trusted reverse proxy must independently enforce request-size, header-size, body-read timeout, and connection/concurrency limits. No reverse proxy is configured in this local Compose stack. Configure `TRUSTED_PROXY_CIDRS` only for actual proxy source networks, and ensure the proxy strips/normalizes forwarded headers.
3. **Deployment requirement — host validation:** Production now requires exact `TRUSTED_HOSTS` and installs `TrustedHostMiddleware`. The deployment must supply the real hostnames and topology; development can leave this unset. Host/proxy behavior at the real ingress remains untested.
4. **Deployment requirement — Qdrant network security:** Production configuration now requires `QDRANT_API_KEY` and an HTTPS `QDRANT_URL`; Compose passes the configured key to the Qdrant service. Development Compose can use a blank key and HTTP on its private service network. Production firewalling, TLS termination, key rotation, and private network policy were not verified.
5. **Potential weakness — database-owner tampering:** Audit triggers and application guards provide append-only behavior for normal roles, not protection from database owners/superusers or privileged infrastructure operators.
6. **Not tested — concurrent load/resource exhaustion:** No load, parser stress, model concurrency, or rate-limit tests were run. The PDF upload/page limits and request byte cap bound individual input sizes but do not bound concurrent work.
7. **Not tested — live integration:** PostgreSQL (`127.0.0.1:5432`) and Qdrant (`127.0.0.1:6333`) were unavailable. No live LLM request was made. Docker was unavailable.
8. **Potential local test-runtime limitation:** Python 3.12 is unavailable on this Windows workstation. Python 3.14.7's TestClient/ASGI sync-endpoint execution stalls in AnyIO portal startup; the new auth rate-limit route tests also stall locally at this boundary, while their direct database/counter tests pass. A prior minimal TestClient app reproduced the Windows asyncio `_fallback_socketpair()` stall. GitHub Actions on Ubuntu/Python 3.12 is the supported environment for the complete suite; this Phase 10.8 commit still requires its own CI run.
9. **Not tested — pre-existing repository history:** The remote was empty before the initial commit, so there was no earlier history to inspect for secrets. `.env` is absent and ignored by `.gitignore`; if credentials from the previously removed local file were ever shared elsewhere, rotate them.

## Verification results

- Python 3.12 CI run 1, commit `c90b91b94f9fb69116b81e97ab6c5f23b2b961f1`: collected 356; 355 passed, 1 failed, 0 skipped, 0 deselected; 31.05 seconds. Failure: `test_generation_route_requires_authentication` used `TestClient(create_app())` without the SQLite `get_db` override and failed with `DatabaseNotConfiguredError` before asserting its expected 401.
- Python 3.12 CI run 2, commit `7e5125b5d4c57d7687ec23eb5b3adb8b68456a92`: collected 356; **356 passed, 0 failed, 0 skipped, 0 deselected** in 22.09 seconds. The test now uses the existing in-memory SQLite session fixture and retains the 401 assertion; application behavior was not modified.
- Python 3.12 CI run 3, commit `20491328b69790e87e7a7b58775e1b4768539a20`: collected 356; **356 passed, 0 failed, 0 skipped, 0 deselected** in 32.28 seconds. Dependency installation, Python/package version reporting, full pytest suite, and archive packaging steps all succeeded.
- CI environment for run 2: Python 3.12.16, pytest 8.4.2, FastAPI 0.165.0, Starlette 1.10.0, HTTPX 0.28.1, httpx2 2.16.2, AnyIO 4.16.0, SQLAlchemy 2.1.9, Alembic 1.20.1, qdrant-client 1.20.0, sentence-transformers 5.7.0, torch 2.15.0. Dependencies installed from declared requirement ranges without edits to those constraints.
- `python -m pytest backend/tests/security -vv` — 42 passed, 0 failed, 0 skipped, 0 deselected in 0.45 seconds on local Python 3.14.7. Includes direct ASGI request-body tests and archive packaging checks.
- `python scripts/package_release.py` and archive validation — passed, 105 entries, including `.github/workflows/tests.yml`; `.env.example` is the only environment file included, and prohibited secret/generated paths are absent.
- Exposure handling: the root `.env` was deleted without inspecting or printing its contents. Previously non-placeholder password/database values in `.env.example` were replaced. The remote was confirmed empty before the initial commit, so exposure through that repository is not indicated. Prior distribution elsewhere cannot be determined; if any removed or replaced value was a real credential and was shared elsewhere, rotate it. No credential was used or disclosed during this work.
- `python -m compileall backend/app backend/tests` — passed.
- `python -m pytest backend/tests/security/test_request_body_limit.py -vv` — 10 passed; direct ASGI tests, no TestClient.
- `python -m pytest backend/tests/test_jwt.py -vv` — 15 passed.
- `python -m pytest backend/tests/test_documents.py -vv` — 38 passed.
- Tenant database/index tests — 4 passed.
- Generation service/provider selection — 54 passed, 6 route/auth tests deselected in an earlier isolated run; all are included and passed in the 356-test CI run above.
- Retrieval service selection — 25 passed, 6 route/auth tests deselected in an earlier isolated run; all are included and passed in the 356-test CI run above.
- Audit non-HTTP selection — 32 passed, 13 HTTP integration tests deselected in an earlier isolated run; all are included and passed in the 356-test CI run above.
- Permission atomicity/synchronization regression tests — 7 passed.
- Local `python -m pytest backend/tests -vv` under Python 3.14.7 — collected 356, then stalled before the first `auth_client` test completed; interrupted after 30 seconds, 0 deselected, no final summary. Python 3.12 GitHub Actions results above supersede this local platform-specific limitation for the supported environment.
- Alembic history — valid through `0005_document_permissions`.
- Offline upgrade SQL — generated successfully.
- Offline downgrade SQL — generated successfully.
- API route generation — FastAPI OpenAPI schema generated 17 API paths, including permission, retrieval, and generation routes.
- Docker Compose — YAML parsed; services are `postgres`, `qdrant`, and `backend`. Docker CLI/runtime validation was unavailable.
- Live PostgreSQL, Qdrant, and LLM checks — not performed.
- Python 3.12 — unavailable locally; verified in GitHub Actions as 3.12.16. The installed dependency version record is included above; a workflow step now emits `python --version` and `python -m pip freeze` for subsequent runs.

## Production recommendations and assessment

- Keep `APP_ENV=production` and `DEBUG=false`; the application now rejects the contrary configuration.
- Enforce request-body limits, rate limits, and concurrency limits at the reverse proxy/ASGI server, including chunked requests.
- Configure Qdrant authentication and deny public network access; keep PostgreSQL private.
- Configure trusted host/proxy rules for the actual deployment topology and terminate TLS at a trusted ingress.
- Run dependency advisory scanning in CI and secret scanning against repository history; rotate any credential ever committed.
- Run integration tests against disposable PostgreSQL and Qdrant instances; those live services were unavailable in this review.
- The full suite passes on the supported Python 3.12 CI environment. The local Windows/Python 3.14 TestClient issue remains limited to this workstation.
- Establish monitored backups and recovery for audit data; database-owner access can bypass trigger protections.

**Assessment:** The complete suite passed in supported Python 3.12 CI after correcting a missing SQLite override in a route test. Live services, deployment controls, and rate/concurrency limits remain unverified, so this application is **not assessed as production-ready by this review**. This report is not a penetration-test certification.
