# mycanal-hodor-core

Shared MIT-licensed Hodor protocol primitives for independent Expiry and private Catalog applications. Python 3.11+, managed with uv.

`http` provides conservative GET transport, reused sessions, URL validation, pacing, retries and technical `HodorError` failures. `availability`, `detail` and `episodes` parse canonical API fields and navigation. There is no catalog/report persistence, playlist progression, editorial crawling or catalog topology in Core.

`bootstrap` parses authenticated browser GET cURL as data into a disposable `HodorRuntimeContext`, with origin/header/token validation and non-sensitive errors. Endpoint policy stays in each consumer. Its optional `read_curl()` uses the `interactive` extra (`prompt-toolkit`) with multiline Esc/Enter submission and no persistent history. Importing the parser does not require that extra.

`logging.get_logger()` and synchronous `timing.timeit()` use the application-selected structlog configuration. Timing is inclusive and preserves exceptions; logging failures do not break wrapped operations. Importing Core never configures global logging. Applications may explicitly call `console.configure_console()` or supply their own processor chain.

```sh
uv sync
uv run pytest
uv build
```

Tests are offline, with synthetic fixtures. Core has no application dependencies. Neither package nor fixtures include authenticated captures. Version 0.1.0 is prepared for local development only; nothing has been published. See LICENSE and NOTICE for extraction provenance.

HTTP errors expose a sanitized optional `HodorError.policy_error` diagnostic code for explicitly recognized server messages. Transport does not learn/apply application policies or retain error bodies; consumers own adaptive behavior.

Failure diagnostics follow the configured structlog DEBUG level. Normal error events stay unchanged; DEBUG adds sanitized HTTP response bodies/context or exception chains without frame locals. INFO and higher suppress these details. Existing console log-level defaults are unchanged.

Episode content IDs are unique identities; editorial numbers may repeat. Catalogs preserve Hodor list order, including units with technical synthetic numbers.

## Shared authentication

`authentication` implements passId bootstrap and renewable tokenPass credentials,
using the existing native keyring entry `mycanal-catalog` / `passId`. `keyring` is
now a Core dependency. `vault('set'/'get'/'delete')` never falls back to plaintext.
`PassIdAuth` performs createToken, profile-zero init/Profiles, explicit selection,
and authenticate; it writes only the profile ID after successful authentication.
Applications supply independent `profile_file` paths via `profile_path(app_name)`.
An absent remembered profile has no default; retirement/replacement is deferred
until authentication succeeds. Network/authentication failures preserve preferences.

`HodorRuntimeContext` stays immutable. Renewable callers use `authentication.headers`
and retain `authentication.secrets`, including passId and all issued tokens, for
redaction. HTTP diagnostics include this registry when a client is given an
`authentication` attribute. Authentication request failures do not retain sensitive
underlying exception contexts. Imports never access keyring, prompt or network.

`ensure(force=...)` implements renewal; applications decide when to call it.
`recover_authentication()` recognizes credential-bearing 401/403 responses,
renews through an optional application coordinator, and updates current headers.
Callers retry once outside a loop and choose their recovery budget. Generic HTTP
transport does not rotate or replay automatically. Catalog retains favorite-window
validation and service-specific policies; Expiry owns playlist acquisition and its
one-recovery budget. An optional rotation observer keeps application presentation
outside Core. cURL remains inert transient context without automatic renewal.
