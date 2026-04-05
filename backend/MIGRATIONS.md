# Database Migrations

This project uses [Alembic](https://alembic.sqlalchemy.org/) for database schema migrations with SQLAlchemy.

## Commands

All commands are run via Docker from the project root (`spotify-analytics/`).

### Check current migration version

```bash
docker compose exec backend alembic current
```

### Create a new migration

1. Make your changes to the models in `app/models.py`
2. Auto-generate a migration file:

```bash
docker compose exec backend alembic revision --autogenerate -m "describe the change"
```

3. **Review the generated file** in `migrations/versions/` -- autogenerate is not perfect, always check the `upgrade()` and `downgrade()` functions before applying.

### Apply migrations

```bash
docker compose exec backend alembic upgrade head
```

### Roll back one migration

```bash
docker compose exec backend alembic downgrade -1
```

### View migration history

```bash
docker compose exec backend alembic history
```

## Notes

- The database URL is loaded from your environment variables via `app/config.py` -- no credentials in `alembic.ini`.
- `DATABASE_URL` must target your external Postgres instance and include `sslmode=require`.
- Migration files in `migrations/versions/` should be committed to git.
- If the backend container isn't running, start it first with `docker compose up -d backend`.
