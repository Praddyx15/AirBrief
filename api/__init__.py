"""AirBrief HTTP API (FastAPI).

Run it with ``uvicorn api.main:app`` and the job worker with ``python -m api.worker``.
The application is built by :func:`api.main.create_app`; this package deliberately imports
nothing at load time so that ``api.worker`` and Alembic can import submodules cheaply.
"""
