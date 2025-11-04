# Markdown File Analyzer (Docker Edition)

This application scans a directory on your server for markdown files and analyzes them.

## File Structure

To build this, you must have all 5 of these files in the same directory:

```
.
├── Dockerfile
├── index.html
├── requirements.txt
├── server.py
└── README.md
```

## How to Run

### 1. Build the Docker Image

Open a terminal in the directory containing all the files and run:

```bash
docker build -t markdown-analyzer .
```

### 2. Run the Docker Container

You must mount the host directory you want to scan (e.g., \`/root/mariadb-docs\`) to the container's \`/scan_data\` directory.

```bash
# *** REPLACE /path/on/your/host with the ABSOLUTE path to your markdown files ***
docker run -d -p 5000:5000 -v /path/on/your/host:/scan_data --name markdown-analyzer-app markdown-analyzer
```

**Example:**
If your markdown files are in \`/root/mariadb-docs\`, the command would be:
```bash
docker run -d -p 5000:5000 -v /root/mariadb-docs:/scan_data --name markdown-analyzer-app markdown-analyzer
```

### 3. Access the Application

Open your web browser and go to: \`http://<your-remote-machine-ip>:5000\`

### 4. Using the App

When the app loads, enter the path *inside the container*.
* To scan the \`mariadb-cloud\` folder, enter: \`/scan_data/mariadb-cloud\`
* To scan the entire repository, enter: \`/scan_data\`

### How to Stop the Container

```bash
docker stop markdown-analyzer-app
docker rm markdown-analyzer-app
```
