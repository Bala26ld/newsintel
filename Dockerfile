FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY newsintel ./newsintel
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir -e ".[dev]"
EXPOSE 8000
CMD ["newsintel", "serve", "--host", "0.0.0.0"]
