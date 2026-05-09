FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -e ".[payment]"
CMD uvicorn payment_server_minimal:app --host 0.0.0.0 --port $PORT
