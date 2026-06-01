# Use official lightweight Python base image
FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install essential system dependencies needed for OpenCV, compiling, and runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# Set standard working directory
WORKDIR /app

# Copy dependency requirements
COPY requirements.txt .

# Upgrade pip and install package packages
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application codebase
COPY . .

# Expose backend API and frontend Streamlit default ports
EXPOSE 8000
EXPOSE 8501

# Command is specified inside docker-compose for granular service control
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
