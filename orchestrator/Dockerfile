FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir docker requests

COPY app/ .

CMD ["python", "main.py"]




