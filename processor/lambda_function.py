import json
import os
import time
import urllib.parse
import pg8000.native
import boto3
import redis

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]

redis_client = redis.from_url(REDIS_URL, decode_responses=True)

def get_db_connection():
    p = urllib.parse.urlparse(DATABASE_URL)
    return pg8000.native.Connection(
        host=p.hostname,
        port=p.port or 5432,
        database=p.path.lstrip("/"),
        user=urllib.parse.unquote(p.username),
        password=urllib.parse.unquote(p.password),
        ssl_context=True
    )

def update_redis(service_id, status, latency):
    key = f"status:{service_id}"
    value = json.dumps({
        "status": status,
        "response_time": latency,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    })
    redis_client.setex(key, 90, value)

def insert_history(conn, service_id, status, latency):
    conn.run(
        "INSERT INTO service_status (id, service_id, status, response_time) VALUES (gen_random_uuid(), :service_id, :status, :latency)",
        service_id=service_id,
        status=status,
        latency=latency
    )

def lambda_handler(event, context):
    conn = get_db_connection()
    try:
        for record in event["Records"]:
            body = json.loads(record["body"])
            service_id = body["service_id"]
            status = body["status"]
            latency = body["latency_ms"]

            update_redis(service_id, status, latency)
            insert_history(conn, service_id, status, latency)
            print(f"Processed {service_id} -> {status} ({latency}ms)")
    finally:
        conn.close()

    return {"statusCode": 200}
