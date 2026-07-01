import asyncio
import aiohttp
import boto3
import json
import os
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

logger = logging.getLogger()
logger.setLevel(logging.INFO)

import boto3, json as _json
_sm = boto3.client("secretsmanager", region_name="us-east-1")
DATABASE_URL = _sm.get_secret_value(SecretId=os.environ["DATABASE_URL_SECRET_ARN"])["SecretString"]
SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]
TIMEOUT_SECONDS = 10

sqs = boto3.client("sqs", region_name="us-east-1")


def get_active_services():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, user_id, url, name
            FROM services
            WHERE is_active = true
        """))
        return [dict(row._mapping) for row in result]


async def ping_service(session, service):
    url = service["url"]
    start = asyncio.get_event_loop().time()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS), allow_redirects=True) as resp:
            latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            status = "UP" if resp.status < 400 else "DOWN"
            return {
                "service_id": str(service["id"]),
                "user_id": str(service["user_id"]),
                "name": service["name"],
                "url": url,
                "status": status,
                "latency_ms": latency_ms,
                "http_status": resp.status,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    except Exception as e:
        latency_ms = int((asyncio.get_event_loop().time() - start) * 1000)
        logger.warning(f"Failed to ping {url}: {e}")
        return {
            "service_id": str(service["id"]),
            "user_id": str(service["user_id"]),
            "name": service["name"],
            "url": url,
            "status": "DOWN",
            "latency_ms": latency_ms,
            "http_status": None,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


async def ping_all(services):
    async with aiohttp.ClientSession() as session:
        tasks = [ping_service(session, s) for s in services]
        return await asyncio.gather(*tasks)


def publish_to_sqs(results):
    entries = []
    for i, result in enumerate(results):
        entries.append({
            "Id": str(i),
            "MessageBody": json.dumps(result)
        })
        if len(entries) == 10:
            sqs.send_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries)
            entries = []
    if entries:
        sqs.send_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries)


def handler(event, context):
    logger.info("Monitor worker triggered")
    services = get_active_services()
    logger.info(f"Found {len(services)} active services to ping")

    if not services:
        return {"statusCode": 200, "body": "No active services"}

    results = asyncio.run(ping_all(services))

    up = sum(1 for r in results if r["status"] == "UP")
    down = sum(1 for r in results if r["status"] == "DOWN")
    logger.info(f"Results: {up} UP, {down} DOWN")

    publish_to_sqs(results)
    logger.info(f"Published {len(results)} results to SQS")

    return {"statusCode": 200, "body": f"Pinged {len(results)} services: {up} UP, {down} DOWN"}
