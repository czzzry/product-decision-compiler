FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN python -m pip install --upgrade pip \
    && python -m pip install .

CMD ["python", "-m", "ai_native_studio.product_agent_live.server"]
