FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md ./
COPY src ./src
COPY adapters ./adapters
COPY policies ./policies
RUN uv pip install --system .
CMD ["python", "-m", "adapters.slack_app"]
