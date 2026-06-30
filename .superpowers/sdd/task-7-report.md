# Task 7: Containerized Deployment -- Final Review Fixes

## Issues Fixed

### Critical
1. **entrypoint.sh -- missing `migrate`**: Added `python manage.py migrate --noinput` before `exec "$@"`. Idempotent, ensures Django built-in app tables exist.

2. **docker-compose.yml -- db.sqlite3 not persisted**: Added named volume `db_data:/app/db.sqlite3` to the `app` service (prod profile) and `db_data:` to the top-level `volumes:` section.

### Important
3. **Dockerfile -- missing `--chown` on COPY**: Changed `COPY --from=builder /root/.local /home/appuser/.local` to `COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local`.

4. **docker-compose.yml -- inaccurate comment**: Changed comment from "项目无 Django models，无需持久化" to "Django 内置 models 存在但项目不依赖它们 -- db.sqlite3 丢失后可重新生成".

## Verification
- `bash -n entrypoint.sh`: PASS
- `python3 -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('OK')"`: OK
- Dockerfile `--chown` syntax: visually verified correct
