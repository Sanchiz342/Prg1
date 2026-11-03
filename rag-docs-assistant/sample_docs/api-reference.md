# Zirka Tasks REST API

> Demo document. Zirka Tasks is a fictional product invented for this project.

The REST API is available at `https://api.zirka.example/v2`. All requests and responses use JSON.

## Authentication

Create a personal access token in Settings › API tokens. Send it in the `Authorization: Bearer <token>` header.
Access tokens expire after 24 hours. A refresh token is valid for 30 days and can be exchanged for a new access token
at `POST /v2/auth/refresh`. Revoking a token in the settings takes effect immediately.

OAuth 2.0 with PKCE is supported for third-party integrations. The client secret must never be shipped in a mobile
or browser application.

## Rate limits

Each token can make 600 requests per minute. Bulk endpoints count as 10 requests each.
When the limit is exceeded the API returns HTTP 429 with a `Retry-After` header that says how many seconds to wait.
Clients should retry with exponential backoff and must not retry more than 5 times.

## Pagination

List endpoints use cursor pagination. Pass `limit` (default 50, maximum 200) and the `next_cursor` value from the
previous response. When `next_cursor` is null there are no more pages.

## Webhooks

Webhooks notify your server when tasks are created, updated or deleted. Register a webhook URL in
Settings › Webhooks; the URL must use HTTPS.

Each delivery is signed with HMAC-SHA256 using your webhook secret. The signature is sent in the
`X-Zirka-Signature` header. Always verify the signature and reject requests older than 5 minutes to prevent replays.

If your endpoint does not answer with a 2xx status within 10 seconds, the delivery is retried up to 8 times with
exponential backoff over roughly 24 hours. After 8 failed attempts the webhook is disabled and the workspace owner
receives an email.

## Error codes

| Code | Meaning |
|------|---------|
| ZK-401 | The token is missing, invalid or expired. |
| ZK-403 | The token is valid but lacks permission for this workspace. |
| ZK-409 | The task was modified by someone else; reload it and retry with the new `version`. |
| ZK-422 | Validation failed; the `errors` field lists the invalid fields. |
| ZK-429 | Rate limit exceeded; see the `Retry-After` header. |

Error ZK-409 is part of optimistic concurrency control: every task has a `version` number, and updates must send the
version they are based on.
