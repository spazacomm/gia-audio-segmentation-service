/**
 * Cloud Run – Audio Processing Orchestrator
 * ----------------------------------------
 * - Scans GCS for audio files
 * - Checks Supabase to avoid duplicates
 * - Sends new files to inapp-service in batches
 */

const express = require('express');
const { Storage } = require('@google-cloud/storage');
const fetch = require('node-fetch');
const { createClient } = require('@supabase/supabase-js');

const app = express();
app.use(express.json());

// ----------------------------------------------------
// Environment Variables (REQUIRED)
// ----------------------------------------------------
const PORT = process.env.PORT || 8080;

const GCS_BUCKET_NAME = process.env.GCS_BUCKET_NAME;
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY;
const INAPP_SERVICE_ENDPOINT = process.env.INAPP_SERVICE_ENDPOINT;
const INAPP_SERVICE_TOKEN = process.env.INAPP_SERVICE_TOKEN;

const BATCH_LIMIT = Number(process.env.BATCH_LIMIT || 5);

// ----------------------------------------------------
// Clients
// ----------------------------------------------------
const storage = new Storage();
const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// ----------------------------------------------------
// Helpers
// ----------------------------------------------------
function formatDate(date) {
  const d = new Date(date);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

// ----------------------------------------------------
// Health check (IMPORTANT for Cloud Run)
// ----------------------------------------------------
app.get('/', (_, res) => {
  res.status(200).send('OK');
});

// ----------------------------------------------------
// Main endpoint
// ----------------------------------------------------
app.post('/process', async (req, res) => {
  try {
    const { country, platform, source, date } = req.body;

    if (!country || !platform || !source) {
      return res
        .status(400)
        .json({ error: 'Missing required parameters: country, platform, source' });
    }

    const targetDate = date ? formatDate(date) : formatDate(new Date());
    const GCS_PATH_PREFIX = `${country}/${platform}/${source}/${targetDate}/`;

    console.log(`🔍 Searching gs://${GCS_BUCKET_NAME}/${GCS_PATH_PREFIX}`);

    // ----------------------------------------------------
    // 1. List audio files from GCS
    // ----------------------------------------------------
    const [files] = await storage.bucket(GCS_BUCKET_NAME).getFiles({
      prefix: GCS_PATH_PREFIX,
      matchGlob: '**/*.mp3',
    });

    if (!files.length) {
      console.log('ℹ️ No audio files found');
      return res.status(200).json({ message: 'No audio files found' });
    }

    const gcsFilePaths = files.map(
      (file) => `gs://${GCS_BUCKET_NAME}/${file.name}`
    );

    console.log(`📁 Found ${gcsFilePaths.length} files`);

    // ----------------------------------------------------
    // 2. Filter already processed files via Supabase
    // ----------------------------------------------------
    const { data: existing, error } = await supabase
      .from('broadcast_timeline')
      .select('recording_url')
      .in('recording_url', gcsFilePaths);

    if (error) {
      console.error('❌ Supabase error:', error);
      return res.status(500).json({ error: 'Database query failed' });
    }

    const processed = new Set(existing.map((r) => r.recording_url));
    const filesToProcess = gcsFilePaths.filter(
      (path) => !processed.has(path)
    );

    console.log(`🆕 ${filesToProcess.length} new files to process`);

    if (!filesToProcess.length) {
      return res.status(200).json({
        message: 'All files already processed',
      });
    }

    // ----------------------------------------------------
    // 3. Send files to inapp-service (batched)
    // ----------------------------------------------------
    let sentCount = 0;

    for (const filePath of filesToProcess) {
      if (sentCount >= BATCH_LIMIT) break;

      try {
        console.log(`➡️ Sending ${filePath}`);

        const response = await fetch(INAPP_SERVICE_ENDPOINT, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-api-key': INAPP_SERVICE_TOKEN,
          },
          body: JSON.stringify({
            processing_url: filePath,
          }),
        });

        if (!response.ok) {
          const text = await response.text();
          console.error(`⚠️ Failed ${filePath}: ${response.status} ${text}`);
          continue;
        }

        sentCount++;
        console.log(`✅ Sent ${filePath}`);
      } catch (err) {
        console.error(`🔥 Error sending ${filePath}`, err);
      }
    }

    return res.status(200).json({
      message: 'Processing started',
      sent: sentCount,
      skipped: filesToProcess.length - sentCount,
    });
  } catch (err) {
    console.error('💥 Fatal error', err);
    return res.status(500).json({ error: 'Internal server error' });
  }
});

// ----------------------------------------------------
// Start server (THIS FIXES YOUR ERROR)
// ----------------------------------------------------
app.listen(PORT, () => {
  console.log(`🚀 Cloud Run service listening on port ${PORT}`);
});
