# PostgreSQL 16: setup and test run (no Docker)

SQLite is the default database and what most tests use. Production instances with several
workers run on PostgreSQL, so the API tests also run against PostgreSQL 16: in CI on every push
(the `postgres` legs of the `python` job), and locally with the steps below. PostGIS is not
needed.

Only `tests/api/` touches the database. With `TEST_DATABASE_URL` set, every API test uses that
database and empties its tables first, so **point it at a database you can throw away**.

## 1. Get a PostgreSQL 16 server

Pick one. None of them needs Docker.

**Ubuntu / Debian**

```bash
sudo apt install postgresql-16          # or postgresql, if the release ships 16
sudo -u postgres createuser --superuser "$USER"
```

**macOS (Homebrew)**

```bash
brew install postgresql@16
brew services start postgresql@16
```

**Windows, without admin rights (portable, nothing installed system-wide)**

1. Download the "zip archive" of the latest 16.x binaries for Windows x86-64 from
   https://www.enterprisedb.com/download-postgresql-binaries (for 16.15:
   `postgresql-16.15-1-windows-x64-binaries.zip`, about 330 MB), the official EDB build.
2. Unzip it into a folder of your own outside the repository, for example
   `%USERPROFILE%\pg16`. You only need `pgsql\bin`, `pgsql\lib` and `pgsql\share`; `pgAdmin 4`,
   `doc`, `symbols` and `StackBuilder` can be left out.
3. In Git Bash, create a cluster that only you can reach, on a port nothing else uses:

   ```bash
   PG="$USERPROFILE/pg16/pgsql/bin"
   "$PG/initdb.exe" -D "$USERPROFILE/pg16/data" -U airbrief --auth=trust -E UTF8 --locale=C
   "$PG/pg_ctl.exe" -D "$USERPROFILE/pg16/data" -l "$USERPROFILE/pg16/pg.log" \
     -o "-p 54329 -c listen_addresses=127.0.0.1" start
   ```

   `--auth=trust` is fine here because the server listens on 127.0.0.1 only and holds test data.
   In Git Bash `pg_ctl start` may not return to the prompt; the server is up once `pg.log` says
   "ready to accept connections". Open another terminal and carry on.

With the EDB installer instead (needs admin), the server runs as a Windows service on port 5432
and asks for a `postgres` password during setup.

## 2. Create the test database and install the driver

```bash
psql -h 127.0.0.1 -p 54329 -U airbrief -d postgres -c "CREATE DATABASE airbrief_test;"
pip install -e ".[api,dev,postgres]"     # psycopg 3 (binary wheels)
```

(Linux and macOS with the default port: `createdb airbrief_test`, and leave `-p 54329` out of the
URLs below or use `5432`.)

## 3. Migrations, then the tests

```bash
export TEST_DATABASE_URL=postgresql+psycopg://airbrief@127.0.0.1:54329/airbrief_test
DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head
pytest -m "not native and not network" tests/api
```

PowerShell: `$env:TEST_DATABASE_URL = "postgresql+psycopg://airbrief@127.0.0.1:54329/airbrief_test"`.

`alembic upgrade head` should print the four migrations, `0001_initial` to
`0004_analysis_datapack_sha256`. The tests should pass with one skip: the backup-restore test
copies a SQLite file and says so. On 2026-10-30, with the portable PostgreSQL 16.15 on Windows:
414 passed, 1 skipped, in a little over 4 minutes; 418 passed on 2026-10-31
after the final review added tests, in 6 and a half. The core tests (`tests/core/`) do not use a
database, so running them again here adds nothing.

## 4. Optional: the production roles

`DEPLOYMENT.md` §3.6 runs the API with a role that cannot change tables, and migrates with a
second one. To check that on your server:

```sql
CREATE ROLE airbrief_owner LOGIN PASSWORD 'owner-test';
CREATE ROLE airbrief_app   LOGIN PASSWORD 'app-test';
CREATE DATABASE airbrief_roles OWNER airbrief_owner;
\c airbrief_roles
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO airbrief_app;
ALTER DEFAULT PRIVILEGES FOR ROLE airbrief_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO airbrief_app;
ALTER DEFAULT PRIVILEGES FOR ROLE airbrief_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO airbrief_app;
```

Then migrate as the owner and start the API as the app role:

```bash
DATABASE_URL=postgresql+psycopg://airbrief_owner:owner-test@127.0.0.1:54329/airbrief_roles alembic upgrade head
AUTO_MIGRATE=false DATABASE_URL=postgresql+psycopg://airbrief_app:app-test@127.0.0.1:54329/airbrief_roles \
  uvicorn api.main:app --port 8000
curl -s -X POST localhost:8000/api/samples/bengaluru_survey/ | head -c 200    # works
psql -h 127.0.0.1 -p 54329 -U airbrief_app -d airbrief_roles -c "DROP TABLE datasets;"
# ERROR:  must be owner of table datasets
```

With `--auth=trust` the passwords are not checked; on a real server they are. Checked on
2026-10-30: the owner role migrates, and the API running as the app role serves `/health`,
processes a sample and deletes it, while `DROP TABLE` as that role fails.

## 5. Clean up

```bash
"$PG/pg_ctl.exe" -D "$USERPROFILE/pg16/data" stop     # Linux/macOS: stop the service
rm -rf "$USERPROFILE/pg16/data"                        # the whole test cluster
```

## Notes

- With `APP_ENV=production`, a `DATABASE_URL` whose host is a public name or address must ask for
  TLS (`?sslmode=require` or `verify-full`), or the API refuses to start. Local servers,
  private addresses and names without a dot (`db`) are exempt.
- PostgreSQL keeps deleted rows until `VACUUM`; `DEPLOYMENT.md` §3.6 shows how to clear them.
- CI starts `postgres:16` as a GitHub Actions service container on GitHub's runner and sets
  `TEST_DATABASE_URL=postgresql+psycopg://airbrief:airbrief@localhost:5432/airbrief`; nothing of
  that runs on your machine.
