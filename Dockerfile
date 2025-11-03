# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
# This includes server.py and index.html
COPY . .

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Create a mount point for the data. This is the /scan_data directory.
VOLUME /scan_data

# Run server.py when the container launches
CMD ["python", "server.py"]
