FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spaceship_ddns.py .

USER 65532:65532

ENTRYPOINT ["python3", "spaceship_ddns.py"]
