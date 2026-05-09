FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[payment]"
CMD uvicorn safeagent_exec_guard.payment_server:app --host 0.0.0.0 --port $PORT
