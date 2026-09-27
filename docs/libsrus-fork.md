# libsrus fork maintenance

This integration build is based on upstream `v1.5.2` at
`56671331ce84d171d8651b9beeafdff74bf445a1`.

## Patch sets

The public patches are kept as two independently reviewable branches:

- `libsrus/client-http-v1.5.2`: isolated session injection, lifecycle,
  timeouts, sanitized HTTP errors, and the expected unauthenticated login
  challenge;
- `libsrus/messages-v1.5.2`: strict parsing for legacy and current message
  lists, normalized message IDs, and robust pagination.

The integration branch additionally contains the guarded discovery tooling and
sanitized current-message fixture. Remarks support is intentionally deferred to
post-MVP and is not advertised by this build.

Integration commits, in order:

- `d7497eb` — isolated client sessions;
- `392777d` — timeouts and sanitized HTTP errors;
- `4c51c96` — guarded in-memory structure discovery;
- `7383c5e`, `1dd4fdb` — sanitized discovery diagnostics;
- `2183205` — expected login maintenance challenge;
- `3d43a89`, `f697421` — discarded remarks-route exploration followed by the
  approved messages-only scope;
- `e8789c0` — sanitized current messages fixture;
- `ceda043` — year-first date sanitization;
- `9b56f8c` — strict current messages parser.

The complete suite passes against
`ghcr.io/rustysnek/librus-apix-mock:latest` on `127.0.0.1:8000`.

## Rebasing onto a newer upstream release

1. Fetch the new upstream release and create a fresh integration branch at its
   exact commit.
2. Cherry-pick the client branch commits and run `tests/test_client.py`.
3. Cherry-pick the messages branch commits and run `tests/test_messages.py`.
4. Re-run the complete suite against the official mock service.
5. Run a separately authorized, no-retry live validation before updating the
   full commit SHA pinned by `libsrus`.

Do not copy discovery output, credentials, cookies, tokens, raw HTML, or local
validation reports into Git.
