# Use a lightweight Python image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your scripts and session files into the container
COPY . .

# Command to run the listener daemon 24/7
CMD ["python", "-u", "listener.py"]