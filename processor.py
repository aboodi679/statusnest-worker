import json
import os
import logging
import asyncio
from datetime import datetime, timezone
import uuid

import asyncpg
import redis.asyncio as aioredis
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

sns_client = boto3.client("sns", region_name="us-east-1")


async def get_database_url() -> str:
    """Fetch DATABASE_URL from Secrets Manager at runtime."""
    import aioboto3
    session = aioboto3.Session()
    async with session.client("secretsmanager", region_name="us-east-1") as sm:
        secret = await sm.get_secret_value(
            SecretId=os.environ["DATABASE_URL_SECRET_ARN"]
        )
        return secret["SecretString"]


async def process_record(record: dict, db_url: str):
    body = json.loads(record["body"])
    service_id = body["service_id"]
    new_status = body["status"]
    response_time = body.get("response_time")
    checked_at = body.get("checked_at", datetime.now(timezone.utc).isoformat())

    logger.info(f"Processing service={service_id} status={new_status}")

    # ── RDS ──────────────────────────────────────────
    conn = await asyncpg.connect(db_url)
    try:
        prev = await conn.fetchrow(
            """
            SELECT status FROM service_status
            WHERE service_id = $1
            ORDER BY checked_at DESC
            LIMIT 1
            """,
            uuid.UUID(service_id)
        )
        prev_status = prev["status"] if prev else None

        await conn.execute(
            """
            INSERT INTO service_status (id, service_id, status, response_time, checked_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            uuid.uuid4(),
            uuid.UUID(service_id),
            new_status,
            float(response_time) if response_time else None,
            datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        )
        logger.info(f"Inserted: {new_status} for {service_id}")
    finally:
        await conn.close()

    # ── Redis ─────────────────────────────────────────
    r = await aioredis.from_url(
        f"redis://{REDIS_HOST}:{REDIS_PORT}",
        decode_responses=True
    )
    try:
        await r.set(
            f"status:{service_id}",
            json.dumps({
                "status": new_status,
                "response_time": response_time,
                "checked_at": checked_at
            }),
            ex=300
        )
        logger.info(f"Redis updated: status:{service_id} = {new_status}")
    finally:
        await r.aclose()

    # ── SNS (Day 11 hook) ─────────────────────────────
    if prev_status and prev_status != new_status and SNS_TOPIC_ARN:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=json.dumps({
                "service_id": service_id,
                "previous_status": prev_status,
                "new_status": new_status,
                "checked_at": checked_at,
            }),
            Subject=f"StatusNest Alert: {service_id} is {new_status}"
        )
        logger.info(f"SNS published: {prev_status} -> {new_status}")


async def main(event):
    records = event.get("Records", [])
    logger.info(f"Received {len(records)} SQS messages")
    db_url = await get_database_url()
    for record in records:
        await process_record(record, db_url)
    return {"statusCode": 200, "processed": len(records)}


def lambda_handler(event, context):
    return asyncio.run(main(event))