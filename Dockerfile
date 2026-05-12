FROM python:3.11-slim
WORKDIR /app
COPY fleet_maintenance/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fleet_maintenance/ .
CMD ["python", "app.py"]
