# StatusNest Worker

> AWS Lambda workers for endpoint monitoring and status processing

Two serverless functions that form the core monitoring engine of StatusNest — pinging endpoints every 60 seconds and updating Redis + PostgreSQL with results.

---

## How It Works

```
EventBridge Scheduler (every 60s)
        ↓
Lambda Monitor Worker
  - Reads all active service URLs from PostgreSQL
  - HTTP GET each endpoint (10s timeout)
  - Sends result to SQS: { service_id, status, latency_ms }
        ↓
SQS Queue (statusnest-dev-monitor-queue)
        ↓
Lambda Processor
  - Reads SQS messages in batches of 10
  - Updates Redis: SET status:{service_id} with 90s TTL
  - Inserts into service_status table (permanent history)
        ↓
SQS Dead Letter Queue (statusnest-dev-monitor-dlq)
  - Failed messages after 3 retries land here
```

---

## Functions

### Monitor Worker (`monitor/lambda_function.py`)

- Triggered by EventBridge every 60 seconds
- Reads active services from PostgreSQL via `pg8000`
- HTTP pings each endpoint with `urllib.request` (no dependencies)
- Status: `UP` = 2xx response, `DOWN` = timeout / non-2xx / connection error
- Sends results to SQS

### Processor (`processor/lambda_function.py`)

- Triggered by SQS (batch size 10)
- Updates Redis key `status:{service_id}` with 90s TTL
- Inserts history row into `service_status` table
- Logs UP/DOWN status with latency

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.11 |
| DB Driver | pg8000 (pure Python — no compilation needed) |
| Cache | redis-py |
| Trigger | AWS EventBridge Scheduler |
| Queue | AWS SQS + DLQ |
| Networking | VPC — same private subnets as ECS |

---

## Project Structure

```
statusnest-worker/
├── monitor/
│   └── lambda_function.py    # Endpoint pinger
├── processor/
│   └── lambda_function.py    # SQS consumer
├── package-lean/             # Monitor dependencies
├── package-processor/        # Processor dependencies
├── monitor-lean.zip          # Monitor deployment package
└── processor.zip             # Processor deployment package
```

---

## Environment Variables

### Monitor Worker
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `SQS_QUEUE_URL` | SQS queue URL |

### Processor
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | ElastiCache Redis URL |

---

## Deployment

```bash
# Monitor Worker
cd package-lean
zip -r ../monitor-lean.zip .
aws lambda update-function-code \
  --function-name statusnest-dev-monitor-worker \
  --zip-file fileb://../monitor-lean.zip

# Processor
cd package-processor
zip -r ../processor.zip .
aws lambda update-function-code \
  --function-name statusnest-dev-processor \
  --zip-file fileb://../processor.zip
```

---

## Related Repos

| Repo | Description |
|------|-------------|
| [statusnest-api](https://github.com/aboodi679/statusnest-api) | FastAPI backend — 3 microservices |
| [statusnest-infra](https://github.com/aboodi679/statusnest-infra) | Terraform IaC |
| [statusnest-frontend](https://github.com/aboodi679/statusnest-frontend) | React dashboard |

---

*Built by [Muhammad Abdullah](https://github.com/aboodi679) · Powered by AWS Lambda*
