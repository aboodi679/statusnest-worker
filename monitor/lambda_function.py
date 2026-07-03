import json
import os
import time
import urllib.request
import urllib.parse
import pg8000.native
import boto3

DATABASE_URL = os.environ["DATABASE_URL"]
SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]

sqs = boto3.client("sqs", region_name="us-east-1")

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

def get_active_services():
    conn = get_db_connection()
    try:
        rows = conn.run("SELECT id, user_id, url FROM services WHERE is_active = true")
        return [{"service_id": str(r[0]), "user_id": str(r[1]), "url": r[2]} for r in rows]
    finally:
        conn.close()

def ping_service(url):
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StatusNest-Monitor/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            latency = round((time.time() - start) * 1000, 2)
            if 200 <= resp.status < 300:
                return "UP", latency
            return "DOWN", latency
    except Exception:
        latency = round((time.time() - start) * 1000, 2)
        return "DOWN", latency

def lambda_handler(event, context):
    services = get_active_services()
    print(f"Pinging {len(services)} services")
    for svc in services:
        status, latency = ping_service(svc["url"])
        message = {
            "service_id": svc["service_id"],
            "user_id": svc["user_id"],
            "url": svc["url"],
            "status": status,
            "latency_ms": latency,
        }
        sqs.send_message(QueueUrl=SQS_QUEUE_URL, MessageBody=json.dumps(message))
        print(f"{svc['url']} -> {status} ({latency}ms)")
    return {"statusCode": 200, "body": f"Pinged {len(services)} services"}
