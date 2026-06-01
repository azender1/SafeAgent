FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[payment]"
CMD uvicorn safeagent.main:app --host 0.0.0.0 --port $PORT
