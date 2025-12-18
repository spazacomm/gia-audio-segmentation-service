# 6. Build and run
#sudo docker stop inaspeech
#sudo docker rm inaspeech
sudo docker build -t inaspeech-service .
sudo docker run -d -p 8000:8000 \
  -e GCP_PROJECT_ID="spaza-media-monitor" \
  -e WEBHOOK_URL="https://nclmnuzdgmcuobduhteo.supabase.co/functions/v1/create-broadcast-timeline"\
  -e WEBHOOK_TOKEN="sb_publishable_SywChzoJM0g0rBfblQV1tg_weB1E9dX"\
  -e API_KEY="9f2b4c8d5a1e6f3b7c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b" \
  --restart unless-stopped \
  --name inaspeech \
  inaspeech-service

# 7. Check it's running
curl http://localhost:8000/health