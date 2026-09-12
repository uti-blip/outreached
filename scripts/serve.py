"""Container entrypoint: persistent storage, unprivileged API, one worker."""

import os
from pathlib import Path


def prepare_volume() -> None:
    volume = Path("/data")
    database = Path(os.environ.get("WORKSPACE_DB_PATH", "/data/workspace.db"))
    if database != volume / "workspace.db":
        raise RuntimeError("Container requires WORKSPACE_DB_PATH=/data/workspace.db")
    if not volume.is_mount():
        raise RuntimeError("Mount a persistent volume at /data before starting the container")
    if os.geteuid() == 0:
        for path in (volume, database, Path(f"{database}-wal"), Path(f"{database}-shm")):
            if path.is_symlink():
                raise RuntimeError("Data volume paths must not be symlinks")
            if path.exists():
                os.chown(path, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    os.umask(0o077)


if __name__ == "__main__":
    prepare_volume()
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8001")),
        workers=1,
        proxy_headers=False,
        timeout_keep_alive=5,
        limit_concurrency=100,
    )
