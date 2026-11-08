Subject: Serverless /run returns 409 ENDPOINT_PAUSED after max workers was raised from 0 to 1 (get-endpoint and health show it active)

Endpoint: rcybvy10rvnu2q (queue endpoint, EU-RO-1, network volume attached)
Date: 2026-11-07, all times UTC

Summary
I set the endpoint's max workers to 0 and then back to 1 through the REST API (PATCH, via the Runpod MCP server). Afterwards, the endpoint configuration and health check both reported it active, but POST /v2/rcybvy10rvnu2q/run kept rejecting jobs with HTTP 409 ENDPOINT_PAUSED for at least 25 seconds. Re-sending the same workers setting, with no actual change, cleared it immediately.

Timeline
- 04:54:51 PATCH workers.max = 0, to drain idle workers before a model change. Read-back: max 0.
- 04:54:58 PATCH workers.max = 1. Read-back at 04:55:02: workers {max: 1, min: 0, idleTimeout: 5}.
- 04:55:07 POST /run → HTTP 409.
- 04:55:10 GET /health → workers idle 1, ready 1, running 0; jobs inQueue 0.
- 04:55:19 POST /run → HTTP 409.
- 04:55:24 POST /run → HTTP 409, body:
  {"status":409,"title":"Conflict","detail":"Endpoint is paused (max_workers=0). Set max_workers > 0 to accept work.","code":"ENDPOINT_PAUSED"}
- 04:55:27 GET endpoint → workers {max: 1, min: 0, idleTimeout: 5}.
- 04:55:30 PATCH workers {max: 1, min: 0, idleTimeout: 5}, the same values already stored.
- Within seconds, POST /run accepted jobs, and they ran normally from then on.

Expected
Once the endpoint shows max workers 1, and health shows a ready worker, /run should accept jobs. If the change is still propagating, the API should say so.

Actual
/run reported max_workers=0, contradicting both the stored configuration and the health check, until the identical setting was saved again. The error tells the user to "Set max_workers > 0", which was already true, so it points them in the wrong direction.

Questions
1. Is there a known propagation delay between the endpoint configuration and the /run admission check after max workers changes from 0? If so, how long is it, and can the API report it?
2. Is re-saving the workers setting the recommended workaround, or is there a better one?

Our client now retries ENDPOINT_PAUSED a limited number of times and then reports it with the fix, so this isn't blocking us. We'd still like to know whether it's a bug.
