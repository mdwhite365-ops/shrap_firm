### httpx logged the Discord webhook, token and all (#274)

Found by the 2026-09-23 audit while checking whether degraded alerts reached
Discord. They did: the Health Monitor's log showed the POSTs returning 204. It
also showed the **full webhook URL**, and a Discord webhook's token is a path
segment. httpx logs every request URL at INFO, and nothing in `src/` had ever
configured its logger. So every alert wrote a credential that can post to the
channel into `docker logs`.

The shared `configure_logging` now installs a filter on the `httpx` and
`httpcore` loggers. It masks webhook tokens, credential-named query
parameters and URL userinfo, and leaves everything else alone. **Redacted, not
silenced:** the same request lines diagnosed the arXiv 406s (#272) that day,
and turning them off would have cost more than it saved.

The existing token still needs rotating. It is in the old container logs, and
this change stops new leaks without recalling old ones.
