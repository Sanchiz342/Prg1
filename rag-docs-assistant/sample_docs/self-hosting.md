# Self-hosting Zirka Server

> Demo document. Zirka Tasks is a fictional product invented for this project.

Zirka Server is the self-hosted edition for teams that must keep data on their own infrastructure.
It is available on the Business plan.

## Requirements

- Docker Engine 24 or newer with Docker Compose v2
- PostgreSQL 14 or newer (PostgreSQL 16 is recommended)
- Redis 7 for background jobs and caching
- At least 2 CPU cores and 8 GB of RAM for up to 200 users

## Installation

Download the compose file and the example environment file, then start the stack:

```
curl -O https://get.zirka.example/compose.yaml
cp .env.example .env
docker compose up -d
```

The web interface listens on port 8080 by default. Change it with the `ZIRKA_HTTP_PORT` variable.

## Configuration

All settings are environment variables in `.env`.
`DATABASE_URL` is the PostgreSQL connection string. `REDIS_URL` points to the Redis instance.
`ZIRKA_SECRET_KEY` signs sessions and must be at least 32 random characters; changing it logs out every user.
Set `ZIRKA_SMTP_HOST`, `ZIRKA_SMTP_USER` and `ZIRKA_SMTP_PASSWORD` to send invitation and notification emails.

## Backups

Run `docker compose exec server zirka-admin backup --output /backups` to create a full backup.
A backup contains the database dump and uploaded attachments. Store backups outside the server and test restoring
them at least once a quarter. Restore with `zirka-admin restore <file>` on a stopped server.

## Upgrading

Read the release notes before upgrading. Always back up first.
Upgrade with `docker compose pull` followed by `docker compose up -d`; database migrations run automatically on start.
Skipping major versions is not supported: upgrade from 3.x to 4.x before moving to 5.x.

## Troubleshooting

If the server does not start, check the logs with `docker compose logs server`.
The error "database is not ready" usually means PostgreSQL is still starting; the server retries for 60 seconds.
If attachments fail to upload, check that the `attachments` volume is writable and that `ZIRKA_MAX_UPLOAD_MB`
(default 25) is large enough.
