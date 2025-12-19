docker build -t audio-worker .
docker run -p 8080:8080 \
  -e SUPABASE_URL=... \
  -e SUPABASE_SERVICE_KEY=... \
  audio-worker
