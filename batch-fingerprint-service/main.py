from fastapi import FastAPI, BackgroundTasks
from worker import AudioFingerprintWorker
from logging_conf import setup_logging

setup_logging()

app = FastAPI(title="Audio Fingerprint Worker")
worker = AudioFingerprintWorker()


@app.post("/fingerprint/batch")
def run_batch(background_tasks: BackgroundTasks):
    background_tasks.add_task(worker.run_batch)
    return {"status": "batch started"}


@app.get("/health")
def health():
    return {"status": "ok"}
