# Use official slim Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire app into the container
COPY . .

# Expose the Flask port
EXPOSE 5000

# Run the app
CMD ["python", "app.py"]