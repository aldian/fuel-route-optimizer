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
  description = "ECR or public container image URI"
}

provider "aws" {
  region = var.aws_region
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
      }
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/api/v1/health/"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 3
  }
}

output "service_url" {
  value       = aws_apprunner_service.app.service_url
  description = "Public HTTPS URL of the AWS App Runner service"
}
