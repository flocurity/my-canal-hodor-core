# mycanal-hodor-core

Shared MIT-licensed Hodor protocol primitives for independent Expiry and private Catalog applications. Python 3.11+, managed with uv.

`http` provides conservative GET transport, reused sessions, URL validation, pacing, retries and technical `HodorError` failures. `availability`, `detail` and `episodes` parse canonical API fields and navigation. There is no persistence, playlist progression, editorial crawling, catalog topology or report presentation in Core.

`bootstrap` parses authenticated browser GET cURL as data into a disposable `HodorRuntimeContext`, with origin/header/token validation and non-sensitive errors. Endpoint policy stays in each consumer. Its optional `read_curl()` uses the `interactive` extra (`prompt-toolkit`) with multiline Esc/Enter submission and no persistent history. Importing the parser does not require that extra.

`logging.get_logger()` and synchronous `timing.timeit()` use the application-selected structlog configuration. Timing is inclusive and preserves exceptions; logging failures do not break wrapped operations. Importing Core never configures global logging. Applications may explicitly call `console.configure_console()` or supply their own processor chain.

```sh
uv sync
uv run pytest
uv build
```

Tests are offline, with synthetic fixtures. Core has no application dependencies. Neither package nor fixtures include authenticated captures. Version 0.1.0 is prepared for local development only; nothing has been published. See LICENSE and NOTICE for extraction provenance.
