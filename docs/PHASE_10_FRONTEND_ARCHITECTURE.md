# SecureRAG Phase 10 — Frontend Architecture Proposal

**Status:** 10.1 architecture approved; 10.2 frontend foundation implemented and approved; 10.3 authentication implemented and awaiting review; 10.4 tenant and membership workspace implemented and awaiting independent review. 10.5 and later subphases have not started.

## 1. Technology stack

- React with TypeScript, built by Vite.
- Tailwind CSS and shadcn/ui components. shadcn/ui components are source-owned and can be adapted to SecureRAG's accessibility and visual standards.
- React Router for route composition and protected layouts.
- TanStack Query for server state, caching, retries, and invalidation.
- React Hook Form and Zod for form state and client-side input feedback. The backend remains authoritative for validation.
- Native `fetch` behind a typed API client. Use `FormData` for PDF upload and URL-encoded form data for login.
- Vitest and React Testing Library for unit/component tests; browser-level tests can be added in a later subphase if the team selects an E2E runner.

Pin compatible versions together in the frontend lockfile when implementation is approved. No frontend dependencies are being added in this architecture-only step.

## 2. Proposed folder structure

```text
frontend/
├── index.html
├── package.json
├── package-lock.json
├── tsconfig.json
├── vite.config.ts
├── postcss.config.js
├── tailwind.config.ts
└── src/
    ├── app/
    │   ├── providers/       # Auth, QueryClient, theme
    │   ├── router/          # Route table, protected layouts
    │   └── query-client.ts
    ├── components/
    │   ├── ui/              # shadcn/ui primitives
    │   ├── layout/          # App shell, sidebar, header, tenant selector
    │   └── shared/          # Empty, loading, error, status components
    ├── features/
    │   ├── auth/            # Login, registration, auth schemas/hooks
    │   ├── dashboard/       # Tenant-aware overview from supported APIs
    │   ├── tenants/         # Tenant list, details, update
    │   ├── memberships/     # Membership list and management
    │   ├── documents/       # Upload, list, metadata, processing status
    │   ├── permissions/     # Document ACL view and replacement
    │   ├── retrieval/       # Semantic search
    │   └── generation/      # Grounded answer and citations
    ├── hooks/               # Cross-feature hooks only
    ├── lib/
    │   ├── api/             # Typed fetch client and endpoint modules
    │   ├── auth/            # In-memory token and auth state
    │   ├── errors/          # API/network error normalization
    │   └── validation/      # Shared form helpers
    ├── types/               # Shared API/domain types
    └── styles/              # Tailwind entry, tokens, theme
```

Keep feature-specific schemas, hooks, API functions, and components inside their feature. Promote code to `shared` only when multiple features truly use it.

## 3. Application architecture

Use a small composition root: `QueryClientProvider`, `AuthProvider`, theme provider, and router. The API client is the only layer that attaches bearer credentials or translates HTTP failures. Feature API modules define typed request/response functions; TanStack Query hooks own cache keys and mutations. Components render query states and collect input, but do not implement backend authorization or business rules.

Tenant-sensitive query keys must include the tenant UUID, for example `['documents', tenantId, filters]`. Requests use the backend's tenant-scoped path. Tenant switching is a strict isolation sequence: (1) cancel all previous-tenant in-flight requests, (2) remove previous-tenant cached data, (3) immediately clear previous-tenant visible data, (4) update active tenant context, (5) fetch the new tenant's data, and (6) reject/ignore any late prior-tenant response at the query and view boundary. Query invalidation alone is insufficient; use cancellation, cache removal, tenant-keyed state, and an active-tenant guard before rendering response data.

## 4. Authentication flow

1. Registration submits JSON to `POST /api/v1/auth/register`. The response is a safe user profile; it does **not** contain an access token. The user proceeds to login.
2. Login submits `application/x-www-form-urlencoded` fields to `POST /api/v1/auth/login`: `username=<email>` and `password=<password>`. It returns `access_token`, `token_type`, and `expires_in`.
3. Keep the access token only in application memory. Do not use `localStorage`, `sessionStorage`, IndexedDB, URL parameters, or persistent query caches for the token. A page reload requires login again; there is no refresh endpoint.
4. After login, call `GET /api/v1/auth/me` with `Authorization: Bearer <token>` to obtain the database-authoritative active profile. The `UserPublic` schema also includes `is_active` and `is_superuser`; render only fields the interface needs, and never treat global `is_superuser` as tenant OWNER/ADMIN. Tenant-specific controls use the membership role returned by `GET /api/v1/tenants`. Treat 401 as invalid/expired authentication: clear in-memory auth state and tenant data and route to login.
5. The API has no logout endpoint. The UI can clear local in-memory state, explain that the bearer token remains valid until expiry, and avoid presenting local clearing as server-side revocation.

Never log submitted credentials, access tokens, or authorization headers. Password fields should use password input semantics, avoid browser persistence where practical, and be cleared after successful submission.

## 5. Tenant switching flow

- Load `GET /api/v1/tenants` after authentication. It returns active tenant memberships with each tenant and the caller's role.
- Select a tenant from that returned list; do not accept an arbitrary tenant UUID as proof of access. The selection is a navigation context, not an authorization grant.
- Load `GET /api/v1/tenants/{tenant_id}` when opening tenant details. The backend verifies active membership. On 404, clear the selection and present a generic unavailable-tenant state.
- On switch, reset tenant-specific query state as described above. Always render the tenant name/slug from backend data rather than trusting a locally edited label.
- Every tenant API call still sends the bearer token and uses the selected UUID in the path. Backend `TenantContext` and membership checks remain authoritative.

## 6. Role-aware navigation and controls

The membership role comes from `GET /api/v1/tenants` and is useful only to tailor the UI. `UserPublic` includes `is_active` and `is_superuser`, including when nested in tenant membership responses. Do not expose these fields by default, do not infer tenant privileges from `is_superuser`, and display only the user properties needed for the current UI. Tenant membership role is the source for tenant-specific affordances. Hiding a control is not an authorization boundary; the backend must continue to reject disallowed requests.

| Capability | UI affordance | Backend rule verified in source |
|---|---|---|
| View tenant list/details | Any authenticated user; show only returned memberships | Tenant list is current user's memberships; tenant detail requires active membership |
| Create tenant | Authenticated user | `POST /tenants` creates tenant and owner membership |
| Update tenant | OWNER or ADMIN | PATCH checks verified `TenantContext` role |
| List/add/update memberships | OWNER or ADMIN | Routes use owner/admin dependency; service further restricts privileged role changes (only OWNER may assign OWNER/ADMIN) |
| Upload PDF | OWNER, ADMIN, or HR | Upload route checks these roles and tenant context |
| List/read document metadata/status | Active tenant member | Routes require `TenantContext`; document metadata is not the same as document-content permission |
| Read/update document permissions | OWNER or ADMIN | Permission routes enforce owner/admin and tenant/document match |
| Search / generate | Active tenant member | Retrieval and generation use verified tenant context and document ACL enforcement; content access is decided by the backend ACL, not this UI table |

Roles are `OWNER`, `ADMIN`, `HR`, `MANAGER`, and `EMPLOYEE`. A role does not imply access to document content. In particular, uploader status, OWNER/ADMIN status, and classification do not grant document-content access. The permission editor must make clear that the document ACL is an explicit allow list/public-within-tenant policy.

## 7. Verified API integration map

All routes below are registered under `/api/v1`. Request and response types should mirror backend Pydantic schemas, not the illustrative types in this document.

| Method and path | Request / response | Access and UI use |
|---|---|---|
| `GET /health` | No body; `{status, application}` | Liveness only |
| `GET /health/database` | No body; `{status, service}` | Service status; 503 when unavailable |
| `GET /health/vector-db` | No body; `{status, service}` | Service status; 503 when unavailable |
| `POST /auth/register` | JSON `{email,password,full_name}` → `UserPublic` (201) | No token returned |
| `POST /auth/login` | URL-encoded `{username,password}` → `{access_token,token_type,expires_in}` | OAuth2 password form; generic credential error |
| `GET /auth/me` | No body → `UserPublic` | Requires active-user bearer token |
| `POST /tenants` | JSON `{name,slug,description?}` → `TenantPublic` (201) | Authenticated user creates tenant and becomes owner |
| `GET /tenants` | No body → list of `{tenant,role,joined_at}` | Current user's active tenant memberships |
| `GET /tenants/{tenant_id}` | No body → `TenantPublic` | Active membership required |
| `PATCH /tenants/{tenant_id}` | JSON `{name?,description?}` → `TenantPublic` | OWNER/ADMIN; extra fields are forbidden |
| `GET /tenants/{tenant_id}/memberships` | No body → membership list with safe `UserPublic` | OWNER/ADMIN |
| `POST /tenants/{tenant_id}/memberships` | JSON `{email,role?}` → membership (201) | OWNER/ADMIN; target must be an existing active user; only OWNER can assign OWNER/ADMIN |
| `PATCH /tenants/{tenant_id}/memberships/{membership_id}` | JSON `{role?,is_active?}` → membership | OWNER/ADMIN, with service-side role, self-removal, and last-owner protections |
| `POST /tenants/{tenant_id}/documents/upload` | Multipart `file`, `classification?`, `is_public?`, repeated `allowed_roles?`/`allowed_users?` → `DocumentPublic` (201) | OWNER/ADMIN/HR; PDF only; defaults are 20 MiB maximum file size and 500 PDF pages (both configurable); ACL users must be active same-tenant members; same-tenant duplicate upload returns 409 |
| `GET /tenants/{tenant_id}/documents?limit=&offset=` | No body → `{items,total,limit,offset}`; each item is `DocumentPublic` | Active tenant context; pagination limit 1–100 |
| `GET /tenants/{tenant_id}/documents/{document_id}` | No body → `DocumentPublic` | Same tenant; metadata only, no file download |
| `GET /tenants/{tenant_id}/documents/{document_id}/status` | No body → `{id,status,page_count,chunk_count,failure_detail}` | Same tenant; returns persisted document processing status; it is not a live progress stream |
| `GET /tenants/{tenant_id}/documents/{document_id}/permissions` | No body → document ACL and sync status | OWNER/ADMIN; tenant/document scoped |
| `PUT /tenants/{tenant_id}/documents/{document_id}/permissions` | Complete ACL `{classification,is_public,allowed_roles,allowed_users}` → ACL and sync status | OWNER/ADMIN; validates users in same tenant; empty private ACL is allowed; PostgreSQL is authoritative |
| `POST /tenants/{tenant_id}/retrieval/search` | JSON `{query,top_k?}` → `{query,tenant_id,results,result_count}` | Active tenant; query 1–2000 chars, top_k 1–20; results are PostgreSQL ACL-verified |
| `POST /tenants/{tenant_id}/generation/answer` | JSON `{query,top_k?}` → `{query,answer,citations,result_count,grounded}` | Active tenant; consumes authorized retrieval only; may abstain when context is insufficient |

No audit query route is registered. Do not build an audit viewer against an invented endpoint. Also absent: document download/preview, refresh tokens, password reset, email invitation delivery, server-side logout/session revocation, and tenant deletion APIs.

### Document metadata and upload behavior

The backend processes uploads synchronously. The upload request performs PDF validation and extraction, text chunking, embedding generation, Qdrant vector storage, and PostgreSQL completion before it returns. Do not design background-job progress tracking or promise real-time progress. The status endpoint reads persisted document status after the request; it is useful for a failed/incomplete persisted record or later status checks, not a live indicator of work inside an upload request.

`DocumentPublic` includes `file_hash`, internal `filename`, `original_filename`, `allowed_roles`, and `allowed_users`, as well as other metadata. Treat the entire response as sensitive tenant metadata: ordinary screens should use an explicit display projection and avoid exposing storage-oriented filenames, hashes, or raw ACL user UUIDs unless an authorized workflow needs them. Metadata visibility for an active tenant member does not grant access to the document's text or content; the backend's explicit ACL continues to govern retrieval and generation.

Upload UI validation should reject non-PDF files and files over 20 MiB before submission, explain the 500-page server limit, and still handle server-side validation as authoritative. Show a clear duplicate-document conflict state for HTTP 409. The MIME type and page count cannot be trusted from browser metadata alone; the backend validates them.

## 8. Security and API integration considerations

- **Same-origin:** No CORS middleware is present. Use Vite's `/api` development proxy to the backend and a same-origin reverse proxy in production. Do not add wildcard CORS to solve local development.
- **Typed API client:** Centralize bearer headers, JSON serialization, URL-encoded login, `FormData` upload, `AbortSignal`, and normalization of network, 401, 403, 404, 413, 422, and 503 responses. Do not set `Content-Type` manually for `FormData`; let the browser supply the boundary. Surface FastAPI validation issues safely without rendering arbitrary server error content as HTML.
- **Request correlation:** Backend responses include an `X-Request-ID` and security headers. Capture the request ID in normalized API errors for support diagnostics, but never include secrets or query/document text in telemetry.
- **Tenant and ACL boundary:** Tenant IDs and roles sent by the browser are context/selection only. Do not put actor IDs, role claims, or ACL grants in request bodies except the explicitly supported permission replacement form; server-side membership and ACL validation remain authoritative.
- **Cache isolation:** Avoid persisting query caches. Clear tenant-scoped query state on logout, 401, and tenant switch. Do not display stale data while a tenant switch is pending.
- **File handling:** Preserve multipart upload semantics and server limits: PDF only, 20 MiB maximum file size, 500 pages maximum by default, and HTTP 409 for duplicate tenant uploads. Validate the selected file's extension/size client-side for feedback, but rely on backend PDF validation. Upload processing is synchronous through extraction, chunking, embeddings, vector storage, and database completion; show a pending submission/loading state, not background progress. The status endpoint reports persisted status. Do not preview/download files; those endpoints do not exist.
- **Untrusted text:** Render retrieved passages and model answers as text, not raw HTML/Markdown with unsafe HTML enabled. Treat document text as untrusted and avoid client-side prompt/action interpretation.
- **Browser hardening:** Production deployment should set an appropriate Content Security Policy, TLS, secure response headers, and trusted host/proxy configuration. These deployment controls are outside this frontend-only proposal.
- **No secrets in the bundle:** API keys, database credentials, JWT signing secrets, and LLM provider credentials stay in backend environment configuration and must never use Vite `VITE_*` variables.

## 9. UI design system

Aim for a restrained enterprise security console: neutral slate surfaces, high-contrast text, one blue/teal action color, and restrained semantic colors for success, warning, and failure. Use a consistent spacing and radius scale, a readable sans-serif typeface, and compact but spacious data tables. Keep tenant identity visible in the global header and sidebar.

Build responsive navigation that collapses to a drawer on narrow screens. Use explicit page titles, breadcrumbs for tenant/document depth, and accessible labels and focus states. Use badges for ingestion/sync status; pair color with text/icons. Forms need inline field errors, submission state, and a confirmation step for replacing ACLs. All pages need loading, empty, error, and retry states. Support light/dark themes with design tokens and test contrast in both.

## 10. Testing strategy

- **API client unit tests:** form-encoded login, bearer attachment, multipart boundary behavior, typed error mapping, abort/network errors, request-ID extraction, and ensuring 401 clears auth state.
- **Auth/provider tests:** memory-only state, profile fetch, expiry/401 behavior, page reload requiring login, and no credential/token logging or persistence.
- **Tenant tests:** membership list drives selector choices, switch invalidates/cancels tenant query cache, late prior-tenant results are not rendered, and role display controls are correct while API remains source of authority.
- **Feature component tests:** accessible form labels and validation, upload status states, pagination, permission replacement confirmation, empty/error states, and citations mapping to backend response data.
- **Security-focused tests:** extra tenant/role body fields are not sent, cross-tenant cache data is not shown, no unsafe HTML rendering, and no hidden feature is represented as working without an endpoint.
- **Backend contract tests:** use mocked API responses based on actual Pydantic schemas; retain backend tests as the authority for authorization and ACL behavior.
- **Quality gates:** TypeScript typecheck, ESLint/format checks, Vitest, and accessibility checks for key workflows. A later implementation can add browser E2E coverage after a test backend/API setup is chosen.

## 11. Subphase implementation plan

1. **10.1 — Architecture:** Architecture proposal and approval. Already completed.
2. **10.2 — Frontend Foundation:** Vite, React, TypeScript, Tailwind, shadcn/ui, providers, router, design tokens, API client foundation, linting, typechecking, and testing configuration. No authentication implementation.
3. **10.3 — Authentication:** Registration, login, profile, in-memory JWT, protected routes, and authentication tests.
4. **10.4 — Tenant and Membership Workspace:** Tenant selector, tenant details and updates, memberships, role-aware UI affordances, and strict tenant cache isolation.
5. **10.5 — Document Management:** PDF upload, paginated listing, metadata and persisted status states; no preview/download controls or background progress tracking.
6. **10.6 — Permission Management:** Read and replace document ACLs, validate form shape, explain explicit ACL semantics, and show synchronization status.
7. **10.7 — Retrieval and Grounded Answers:** Semantic search and generated answers/citations using only existing backend endpoints.
8. **10.8 — Security Hardening and Release Review:** Accessibility, responsive polish, CSP/reverse-proxy deployment notes, security tests, dependency review, and end-to-end workflow verification.

10.1 architecture is approved, and 10.2 foundation is implemented and approved. 10.3 authentication is implemented and awaiting review. 10.4 tenant and membership workspace is implemented and awaiting independent review; 10.5 and later subphases have not started.

## 12. Backend additions required and known gaps

No backend change is required to implement the scoped frontend features listed above. Use proxy routing instead of adding CORS middleware. The following requested product capabilities would require backend design/API work before a truthful UI can offer them:

- Audit-log browsing: currently internal audit querying only; no public endpoint or UI authorization contract.
- Document preview/download: no content-serving endpoint.
- Refresh/remembered sessions and server-side logout/revocation: no refresh or revocation endpoint; current access token is short-lived.
- Password reset and invitation email: no reset or invitation delivery flow.
- User directory/search: membership creation accepts an existing email, but no general directory endpoint exists; UI must ask for email rather than fake autocomplete.
- A dashboard with aggregate document/activity metrics: no aggregate dashboard endpoint; initially derive only from existing paginated/list calls, avoiding unbounded fan-out.

Health checks report service availability and should be used only for operational status, not as a substitute for authenticated feature readiness.

## 13. Security assessment: implemented controls, deployment recommendations, and untested areas

The current [SECURITY_ASSESSMENT.md](../SECURITY_ASSESSMENT.md) reports reviewed application controls and explicit limitations. The frontend design must not turn deployment recommendations into claims that the application already enforces them:

**Implemented/reviewed controls:** application-level ASGI request-body byte counting (including chunked requests), per-file upload limit, PDF page limit, JWT and active-membership checks, server-side tenant scoping, PostgreSQL-authoritative document ACL checks, and Qdrant tenant/ACL filtering as defense in depth. Audit ORM/migration protections reject normal application update/delete behavior.

**Deployment recommendations, not confirmed application controls:** there is no application-level rate limiting; configure rate and concurrency limits at a trusted gateway/server. `TrustedHostMiddleware` and a trusted-proxy policy are absent; configure host/proxy handling for the actual deployment. Reverse-proxy and ASGI server request-size and time limits must be set independently in deployment; the application counter is not a substitute for ingress/server limits. Configure Qdrant production authentication and network restrictions; local Compose configuration permits an empty Qdrant key.

**Not tested / residual limits:** live PostgreSQL and Qdrant integration testing was not completed in the documented assessment. Audit append-only protections do not prevent a privileged database owner/superuser from tampering with records. No production deployment verification is documented. Therefore this proposal and the assessment do not claim production readiness or penetration-test certification.

## 14. Proposed local run commands (for the later implementation)

From repository root in PowerShell, with the 10.2 foundation and current 10.3 authentication implementation:

```powershell
cd frontend
npm install
npm run dev
```

Vite should proxy `/api` to the local FastAPI origin (typically `http://127.0.0.1:8000`) via `vite.config.ts`; browser code should call relative `/api/v1/...` paths. Production should route the same path through the trusted same-origin reverse proxy. No frontend directory or package files are created as part of this proposal.
