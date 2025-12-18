docker build -t audio-fingerprinter .


docker run -d \
  -p 8080:8080 \
  --name fingerprinter \
  -v $(pwd)/your-google-creds.json:/app/credentials.json \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json \
  -e SOURCE_BUCKET="spaza-recordings" \
  -e LIBRARY_BUCKET="audio_library" \
  -e WEBHOOK_URL="https://webhook.site/5f3323cb-a7d2-4c57-ae80-a10419e0b03d" \
  audio-fingerprinter


  curl -X POST http://localhost:8080/fingerprint \
  -H "Content-Type: application/json" \
  -d '{
    "recording_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    "timeline_label_id": "label_12345",
    "start_offset_seconds": 10.0,
    "end_offset_seconds": 25.0,
    "webhook_url": "https://webhook.site/5f3323cb-a7d2-4c57-ae80-a10419e0b03d"
  }'