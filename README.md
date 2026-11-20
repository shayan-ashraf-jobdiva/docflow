# DocFlow

Invoice and document processing, built to match the DocFlow architecture design.
Users upload a file, a background worker extracts the data, and the result appears
on a status screen and in an email.

It runs on one machine with no cloud account. Each AWS service in the design is
replaced by a small local component with the same behaviour, so the code shows the
architecture and can be moved to AWS piece by piece.

## Run it

Needs Python 3.7 or newer (3.10 or newer is recommended).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000, upload a file from `samples/`, and watch the status change.
The 1 second delay in the worker simulates OCR time (`DOCFLOW_PROCESSING_DELAY`).

## Try the three outcomes

| Sample file | What it shows |
| --- | --- |
| `samples/invoice_ok.txt` | Normal path: status goes QUEUED, PROCESSING, DONE, then an email appears |
| `samples/invoice_bad_total.txt` | Validation failure: line items do not match the total, so the job fails at once and is not retried |
| `samples/invoice_crash.txt` | Worker crash: the message is retried 3 times, then moves to the dead-letter queue and the job is marked FAILED |

Replay dead-lettered messages as admin:

```bash
curl -X POST localhost:8000/admin/dlq/replay -H 'Authorization: Bearer demo:admin'
curl localhost:8000/admin/queue -H 'Authorization: Bearer demo:admin'
```

## How the pieces map to the architecture

| Design component | Local stand-in | File | Swap for |
| --- | --- | --- | --- |
| React app on S3 + CloudFront | `static/index.html` | `app/main.py` (`/`) | Static hosting on S3 + CloudFront |
| Cognito JWT | `Authorization: Bearer demo:<user>` | `app/main.py` (`current_user`) | Cognito authorizer on API Gateway |
| API Gateway + API service | FastAPI app | `app/main.py` | API Gateway + Lambda (e.g. Mangum) |
| Pre-signed S3 upload URL | `PUT /uploads/<token>`, single use, 15 min | `app/main.py` | `generate_presigned_url` |
| S3 bucket + ObjectCreated event | `data/uploads/` + `queue.send` | `app/main.py` | S3 event notification to SQS |
| SQS + dead-letter queue | SQLite queue with visibility timeout and redrive | `app/queue.py` | boto3 SQS client |
| Processor Lambda | background thread | `app/worker.py` | Lambda with SQS trigger |
| Textract + validation | text/PDF parser | `app/extract.py` | Textract `AnalyzeExpense` |
| PostgreSQL (RDS) | SQLite | `app/db.py` | RDS PostgreSQL |
| Redis (ElastiCache) | in-process TTL cache | `app/cache.py` | Redis client, same get/set/delete |
| SNS fan-out | event bus with subscribers | `app/events.py` | SNS topic with subscriptions |
| Notifier Lambda + SES | stores emails in an outbox table | `app/events.py` | Lambda calling SES |

## API

All routes except `/`, `/health` and `/uploads/<token>` need `Authorization: Bearer demo:<username>`.

| Method and path | Purpose |
| --- | --- |
| `POST /jobs` `{"filename": "x.txt"}` | Create a job, returns `job_id` and a one-time `upload_url` |
| `PUT /uploads/<token>` | Upload the raw file bytes (max 20 MB) |
| `GET /jobs` | Your jobs, newest first |
| `GET /jobs/<id>` | One job; the `X-Cache` header shows HIT or MISS |
| `GET /notifications` | Emails sent to you |
| `GET /admin/queue`, `POST /admin/dlq/replay` | Queue depth and dead-letter replay (user `admin`) |

## How the design properties show up in code

- **Loose coupling:** the API only writes a message to the queue; it never calls the worker. The worker only publishes an event; it never calls the notifier. Add a new subscriber in `Services.__init__` without touching either (the audit log in `data/events.log` is a second subscriber).
- **No lost work:** a message is deleted only after the result is saved. A crash leaves it in the queue, and it reappears after the visibility timeout.
- **Idempotent worker:** a duplicate delivery of a finished job is dropped (`Processor.handle`).
- **Caching:** `GET /jobs/<id>` reads the cache first; every job update deletes its cache entry (`Jobs._update`), so readers never see stale status for longer than one write.
- **Bad documents vs broken workers:** a `ValidationError` fails the job at once; any other exception is retried and eventually dead-lettered.

## Deploy to AWS with CI/CD

`deploy/AWS_SETUP.md` has the step-by-step guide. A push to `main` runs the unit tests and
security scans (Bandit, pip-audit, Gitleaks), builds `build-v<VERSION>-<run>.zip`, uploads it
to S3, and deploys the latest build from S3 to an EC2 server. A release that fails its health
check is rolled back automatically. Change `VERSION` to see a new version appear on the live page.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Covers the happy path, validation failure, crash then dead-letter then replay, user isolation, auth, caching, bad file type, oversize upload and single-use upload links.

## Limits of this version

- The queue, cache and event bus live in one process, so run a single server worker. Moving to real SQS, Redis and SNS removes that limit.
- Extraction handles simple text invoices and PDFs with a text layer. Scanned images need Textract.
- The demo login trusts the username. Use Cognito before exposing it anywhere.
