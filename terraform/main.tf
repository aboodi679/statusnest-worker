terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "statusnest-terraform-state"
    key            = "worker/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "statusnest-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = "us-east-1"
}

variable "environment" {
  default = "dev"
}

variable "database_url_secret_arn" {
  default = "arn:aws:secretsmanager:us-east-1:026243800492:secret:statusnest-dev-database-url-75QRll"
}

variable "vpc_id" {
  default = "vpc-059b43ace86716342"
}

variable "private_subnet_ids" {
  default = ["subnet-072b96143bbd37ed7", "subnet-059242e11ee727d9e"]
}

variable "redis_host" {
  default = "statusnest-dev-redis.b8x2ra.0001.use1.cache.amazonaws.com"
}

# ── Security Group ────────────────────────────────────────────────
resource "aws_security_group" "monitor_lambda" {
  name        = "statusnest-${var.environment}-monitor-lambda-sg"
  description = "Security group for monitor Lambda"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Environment = var.environment }
}

# ── SQS ──────────────────────────────────────────────────────────
resource "aws_sqs_queue" "dlq" {
  name                      = "statusnest-${var.environment}-monitor-dlq"
  message_retention_seconds = 1209600
  tags = { Environment = var.environment }
}

resource "aws_sqs_queue" "monitor" {
  name                       = "statusnest-${var.environment}-monitor-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 3600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
  tags = { Environment = var.environment }
}

# ── IAM ──────────────────────────────────────────────────────────
resource "aws_iam_role" "monitor_lambda" {
  name = "statusnest-${var.environment}-monitor-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = { Environment = var.environment }
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.monitor_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.monitor_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "monitor_lambda_policy" {
  name = "statusnest-${var.environment}-monitor-lambda-policy"
  role = aws_iam_role.monitor_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:SendMessageBatch"]
        Resource = aws_sqs_queue.monitor.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.monitor.arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.database_url_secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = "*"
      }
    ]
  })
}

# ── Lambda Layer (shared deps) ────────────────────────────────────
locals {
  layer_arn = "arn:aws:lambda:us-east-1:026243800492:layer:statusnest-dev-worker-deps:1"
}

# ── Monitor Lambda ────────────────────────────────────────────────
resource "aws_lambda_function" "monitor" {
  filename         = "${path.module}/../monitor-handler.zip"
  source_code_hash = filebase64sha256("${path.module}/../monitor-handler.zip")
  function_name    = "statusnest-${var.environment}-monitor"
  role             = aws_iam_role.monitor_lambda.arn
  handler          = "monitor.handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 256
 layers = [local.layer_arn]

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.monitor_lambda.id]
  }

  environment {
    variables = {
      SQS_QUEUE_URL           = aws_sqs_queue.monitor.url
      DATABASE_URL_SECRET_ARN = var.database_url_secret_arn
    }
  }

  tags = { Environment = var.environment }
}

resource "aws_lambda_function_event_invoke_config" "monitor" {
  function_name          = aws_lambda_function.monitor.function_name
  maximum_retry_attempts = 0
}

# ── EventBridge ───────────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "every_minute" {
  name                = "statusnest-${var.environment}-monitor-every-minute"
  schedule_expression = "rate(1 minute)"
  tags = { Environment = var.environment }
}

resource "aws_cloudwatch_event_target" "monitor_lambda" {
  rule      = aws_cloudwatch_event_rule.every_minute.name
  target_id = "MonitorLambda"
  arn       = aws_lambda_function.monitor.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.every_minute.arn
}

# ── Processor Lambda ──────────────────────────────────────────────
resource "aws_lambda_function" "processor" {
  filename         = "${path.module}/../processor-handler.zip"
  source_code_hash = filebase64sha256("${path.module}/../processor-handler.zip")
  function_name    = "statusnest-${var.environment}-processor"
  role             = aws_iam_role.monitor_lambda.arn
  handler          = "processor.lambda_handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 256
 layers = [local.layer_arn]

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.monitor_lambda.id]
  }

  environment {
    variables = {
      DATABASE_URL_SECRET_ARN = var.database_url_secret_arn
      REDIS_HOST              = var.redis_host
      REDIS_PORT              = "6379"
      SNS_TOPIC_ARN           = ""
    }
  }

  tags = { Environment = var.environment }
}

resource "aws_lambda_event_source_mapping" "processor_sqs" {
  event_source_arn                   = aws_sqs_queue.monitor.arn
  function_name                      = aws_lambda_function.processor.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 10
  enabled                            = true
}

# ── Outputs ───────────────────────────────────────────────────────
output "sqs_queue_url" {
  value = aws_sqs_queue.monitor.url
}

output "sqs_queue_arn" {
  value = aws_sqs_queue.monitor.arn
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.monitor.function_name
}

output "processor_function_name" {
  value = aws_lambda_function.processor.function_name
}

output "layer_arn" {
  value = local.layer_arn
}