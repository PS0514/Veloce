# Stop all running Veloce containers
docker-compose -f deploy/docker-compose.yaml down

# Rebuild and start the services in the background
docker-compose -f deploy/docker-compose.yaml up -d --build

Write-Host "Veloce services have been recreated and started." -ForegroundColor Green
docker-compose -f deploy/docker-compose.yaml ps
