FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt constraints.txt pyproject.toml README.md ./
COPY python ./python

RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt \
    && pip install --no-cache-dir -e .

EXPOSE 9000

CMD ["watchfiles", "--filter", "python", "--ignore-paths", ".venv", "--", "python -m mc_bot_agent_entrypoint"]
