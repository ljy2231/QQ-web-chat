# Changelog

## 0.2.0 — audit remediation

- Extracted authentication, incremental SQLite storage, protocol helpers, templates and browser scripts from the monolithic server.
- Added bounded, expiring Cookie sessions, rate limiting, Origin validation, WS/bridge revocation and redacted access logs; browser no longer persists bearer tokens or places them in URLs.
- Migrated legacy snapshots transactionally to schema3 with typed conversations, stable internal IDs, platform-ID mappings, sender direction and revision boundaries. Ambiguous orphan history remains read-only, not guessed.
- Added durable inbox, asynchronous media enrichment and idempotent outbound tracking with partial/unknown states and atomic history publication recovery. Unknown operations are never blindly replayed.
- Fixed >64-contact initialization, reply/recall platform mapping, online heartbeat state, settings revision conflicts, cross-conversation uploads, loading-text display clearing, IME handling and stale snapshots.
- Retained SSRF/path/attachment protections and added multicast rejection, quotas, request/read deadlines, private responses, local CSS and file expiry checks.
- Added Python/Node tests, CI, reproducible Linux dependency hashes, nonroot Docker/systemd examples and backup/restore tools.

### Behavior changes

`= ALL ON/OFF` is administrator-only in private chat and target-scoped. Reply segments no longer bypass the normal private-message prefix. Client IP disclosure defaults on to keep the original 正文/IP/time wire format; set DISCLOSE_CLIENT_IP=false to omit it. Old browser bearer sessions expire on upgrade; refresh and log in again. Attachments selected before restart must be uploaded again.

### Validation and limits

93 Python tests and 28 Node VM/DOM synthetic cases passed on Python3.12/aiohttp3.14.5. An offline production snapshot migration preserved every retained record and settings; a nonroot/read-only container smoke check and graceful shutdown passed. These are not a browser/device certification or full real-QQ delivery test. NR requires target-device confirmation. Exactly-once remote delivery, copies already downloaded by recipients, and upstream events never delivered to this service cannot be guaranteed. Original code and documentation are now available under the MIT License; third-party audio and other assets retain their respective rights and terms.
