FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml setup.cfg README.md LICENSE LICENSING.md MANIFEST.in ./
COPY safeagent_exec_guard ./safeagent_exec_guard
COPY settlement ./settlement
COPY examples ./examples

RUN pip install --upgrade pip
RUN pip install psycopg[binary]>=3.1
RUN pip install -e .

CMD ["bash"]