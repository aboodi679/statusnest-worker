# StatusNest Worker

Serverless monitoring engine for StatusNest. Two AWS Lambda functions that together handle service health checking, result caching, and database persistence.

---

## Architecture

```
EventBridge (every 60s)
        │
        ▼
 monitor Lambda          ← Reads active services from RDS
        │                   Pings each URL (HTTP)
        │                   Sends result to SQS
        ▼
      SQS Queue
        │
        ▼
 processor Lambda        ← Reads SQS messages
                            Writes status + response_time + checked_at to Redis (TTL 90s)
                            Inserts row into service_status table (RDS)
```

---

## Functions

### `monitor/lambda_function.py`
- Triggered by EventBridge rule every 60 seconds
- Fetches all `is_active = true` services from RDS
- Pings each URL with a 10s timeout
- Sends `{service_id, user_id, url, status, latency_ms}` to SQS

### `processor/lambda_function.py`
- Triggered by SQS queue
- Writes to Redis: `status:{service_id}` → `{status, response_time, checked_at}` (TTL 90s)
- Inserts historical row into `service_status` table in RDS

<img width="1568" height="232" alt="image" src="https://github.com/user-attachments/assets/ac8e9dc4-097f-4049-bbb9-bdc7417a6653" />

<img width="1555" height="340" alt="image" src="https://github.com/user-attachments/assets/59d250cb-1a0d-40dd-afb9-2176c3f6a972" />


---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.11 |
| HTTP | `urllib.request` (stdlib, no extra deps) |
| Database | `pg8000` (pure-Python PostgreSQL driver) |
| Cache | `redis-py` |
| Queue | AWS SQS |
| Scheduler | AWS EventBridge |
| Packaging | Docker (Linux-compatible deps) + zip |

---

## Environment Variables

```
DATABASE_URL      postgresql://user:pass@host:5432/statusnest
REDIS_URL         redis://host:6379
SQS_QUEUE_URL     https://sqs.us-east-1.amazonaws.com/026243800492/statusnest-dev-queue
```

---

## Deployment

Dependencies are built inside a Docker container to ensure Linux-compatible binaries, then zipped and uploaded to Lambda:

```bash
# Build processor package
docker run --rm -v "$PWD":/out python:3.11-slim bash -c \
  "pip install redis pg8000 -t /out/package-processor"

cd package-processor
zip -r ../processor.zip .
cd ../processor && zip -g ../processor.zip lambda_function.py

aws lambda update-function-code \
  --function-name statusnest-dev-processor \
  --zip-file fileb://processor.zip
```

---

## Project Structure

```
statusnest-worker/
├── monitor/
│   └── lambda_function.py    # Health check + SQS sender
├── processor/
│   └── lambda_function.py    # SQS consumer + Redis/RDS writer
├── terraform/                # Lambda + EventBridge + SQS Terraform (reference)
└── requirements.txt
```

---

## Related Repos

| Repo | Description |
|---|---|
| [statusnest-api](https://github.com/aboodi679/statusnest-api) | FastAPI microservices (auth, monitor, status) |
| [statusnest-frontend](https://github.com/aboodi679/statusnest-frontend) | React SPA |
| [statusnest-infra](https://github.com/aboodi679/statusnest-infra) | Terraform IaC for all AWS infrastructure |
