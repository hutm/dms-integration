# Credential and task data handling

Keep downloaded OAuth client JSON, access/refresh tokens, synchronization state, task snapshots, and live verification logs outside this public repository. `.gitignore` is a guardrail, not a secret scanner.

The default client file is `~/.config/dank-todo-google-tasks/client.json`. Its containing directory should be mode 0700 and the file mode 0600. The sync tool creates private state files atomically and enforces mode 0700 on its state directory. The NixOS service sets a private umask. File permissions do not encrypt files; use an encrypted disk or encrypted backup storage where appropriate. Never put secrets into Nix expressions or the Nix store.

Only the Google Tasks scope is requested. The integration uses Google's installed-app OAuth flow with PKCE and an ephemeral loopback callback. Its service logs do not intentionally include task contents, HTTP response bodies, or tokens.

To revoke access, stop the timer and remove the connection from your Google account. Existing tasks, snapshots, and backup copies must be deleted separately if you want them removed.

Do not open public issues containing tokens, client credentials, task titles, account dumps, or private logs. For a code issue, provide a minimal synthetic reproduction with invented tasks. If a credential is exposed, revoke or rotate it before discussing the incident publicly.
