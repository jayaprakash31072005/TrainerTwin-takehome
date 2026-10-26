FROM python:3.12-slim

# uv for installing from your lockfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# install dependencies first (cached unless they change)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# copy the whole server folder (bot.py, interview_flow.py, ...)
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["sh", "-c", "python bot.py -t websocket --host 0.0.0.0 --port ${PORT:-10000}"]
