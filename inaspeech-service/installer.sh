# 1. Create VM
gcloud compute instances create inaspeech-vm \
  --project=spaza-media-monitor \
  --zone=us-central1-a \
  --machine-type=n1-standard-4 \
  --image-family=debian-11 \
  --image-project=debian-cloud \
  --boot-disk-size=50GB \
  --tags=http-server

# 2. Create firewall rule
gcloud compute firewall-rules create allow-inaspeech \
  --allow tcp:8000 \
  --source-ranges 0.0.0.0/0 \
  --target-tags http-server

# 3. SSH into VM
gcloud compute ssh inaspeech-vm --zone=us-central1-a

# 4. Install Docker
sudo apt-get update
sudo apt-get install -y docker.io git
sudo systemctl start docker
sudo systemctl enable docker

# 5. Clone your repo or copy files
# (Copy inaspeech_service.py, Dockerfile, requirements.txt)

# 6. Build and run
sudo docker build -t inaspeech-service .
sudo docker run -d -p 8000:8000 \
  -e GCP_PROJECT_ID="spaza-media-monitor" \
  --restart unless-stopped \
  --name inaspeech \
  inaspeech-service

# 7. Check it's running
curl http://localhost:8000/health

# 8. Get VM external IP
gcloud compute instances describe inaspeech-vm \
  --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'