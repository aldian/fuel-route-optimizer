terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region for deployment"
}

variable "image" {
  type        = string
  default     = "public.ecr.aws/spotter/fuel-route-optimizer:latest"
  description = "ECR or public container image URI"
}

variable "django_secret_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Cryptographic DJANGO_SECRET_KEY"
}

variable "allowed_hosts" {
  type        = string
  default     = ".awsapprunner.com,.elb.amazonaws.com,localhost,127.0.0.1"
  description = "Comma-separated ALLOWED_HOSTS"
}

variable "provision_postgres" {
  type        = bool
  default     = false
  description = "When true, provisions a managed Amazon RDS PostgreSQL 16 instance from scratch"
}

variable "db_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Master password for Amazon RDS PostgreSQL"
}

variable "database_url" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Explicit DATABASE_URL when connecting to an external database"
}

variable "provision_redis" {
  type        = bool
  default     = false
  description = "When true, provisions a managed Amazon ElastiCache for Redis 7 cluster from scratch"
}

variable "redis_url" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Explicit REDIS_URL when connecting to an external Redis cache"
}

provider "aws" {
  region = var.aws_region
}

# Optional: Provision Managed Amazon RDS for PostgreSQL 16 from scratch
resource "aws_db_instance" "postgres" {
  count               = var.provision_postgres ? 1 : 0
  identifier          = "fuel-route-optimizer-pg"
  engine              = "postgres"
  engine_version      = "16"
  instance_class      = "db.t4g.micro"
  allocated_storage   = 20
  db_name             = "spotter_fuel"
  username            = "spotter"
  password            = var.db_password
  publicly_accessible = false
  skip_final_snapshot = true # Enabled so ./bin/spotter-destroy tears down cleanly after demo
  deletion_protection = false
}

# Optional: Provision Managed Amazon ElastiCache for Redis 7 from scratch
resource "aws_elasticache_cluster" "cache" {
  count                = var.provision_redis ? 1 : 0
  cluster_id           = "fuel-optimizer-redis"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = "cache.t4g.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
}

locals {
  effective_database_url = var.provision_postgres ? (
    "postgres://spotter:${var.db_password}@${aws_db_instance.postgres[0].endpoint}/spotter_fuel"
  ) : var.database_url

  effective_redis_url = var.provision_redis ? (
    "redis://${aws_elasticache_cluster.cache[0].cache_nodes[0].address}:6379/0"
  ) : var.redis_url
}

resource "aws_apprunner_service" "app" {
  service_name = "fuel-route-optimizer"

  source_configuration {
    auto_deployments_enabled = false
    image_repository {
      image_identifier      = var.image
      image_repository_type = "ECR_PUBLIC"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          DJANGO_SETTINGS_MODULE = "config.settings.production"
          DJANGO_SECRET_KEY      = var.django_secret_key
          ALLOWED_HOSTS          = var.allowed_hosts
          DATABASE_URL           = local.effective_database_url
          REDIS_URL              = local.effective_redis_url
        }
      }
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/api/v1/health/"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 10
  }
}

output "service_url" {
  value       = aws_apprunner_service.app.service_url
  description = "Public HTTPS URL of the AWS App Runner service"
}
