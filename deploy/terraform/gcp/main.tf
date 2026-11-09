terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

variable "project_id" {
  type        = string
  description = "GCP Project ID"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "GCP Region"
}

variable "image" {
  type        = string
  default     = "ghcr.io/aldian/fuel-route-optimizer:latest"
  description = "Container image URL"
}

variable "min_instances" {
  type        = number
  default     = 0
  description = "Minimum Cloud Run instances (default 0 for scale-to-zero cost savings during demos)"
}

variable "django_secret_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Cryptographic DJANGO_SECRET_KEY injected into Cloud Run"
}

variable "allowed_hosts" {
  type        = string
  default     = ".run.app,.a.run.app,localhost,127.0.0.1"
  description = "Comma-separated ALLOWED_HOSTS (defaults to Cloud Run *.run.app wildcard suffixes)"
}

variable "provision_postgres" {
  type        = bool
  default     = false
  description = "When true, provisions a managed Cloud SQL for PostgreSQL 16 instance from scratch"
}

variable "db_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Database password for the provisioned Cloud SQL user"
}

variable "database_url" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Explicit DATABASE_URL when connecting to an external database"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Optional: Provision Managed Cloud SQL for PostgreSQL 16 from scratch
resource "google_sql_database_instance" "postgres" {
  count               = var.provision_postgres ? 1 : 0
  name                = "fuel-route-optimizer-pg"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false # Disabled so ./bin/spotter-destroy can tear down cleanly after demo

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_type         = "PD_SSD"
  }
}

resource "google_sql_database" "app_db" {
  count    = var.provision_postgres ? 1 : 0
  name     = "spotter_fuel"
  instance = google_sql_database_instance.postgres[0].name
}

resource "google_sql_user" "app_user" {
  count    = var.provision_postgres ? 1 : 0
  name     = "spotter"
  instance = google_sql_database_instance.postgres[0].name
  password = var.db_password
}

locals {
  effective_database_url = var.provision_postgres ? (
    "postgres://spotter:${var.db_password}@/spotter_fuel?host=/cloudsql/${google_sql_database_instance.postgres[0].connection_name}"
  ) : var.database_url
}

# Cloud Run v2 Service (Scale-to-Zero enabled by default for demo cost savings)
resource "google_cloud_run_v2_service" "app" {
  name                = "fuel-route-optimizer"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    dynamic "volumes" {
      for_each = var.provision_postgres ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.postgres[0].connection_name]
        }
      }
    }

    containers {
      image = var.image
      resources {
        limits = {
          cpu    = "1000m"
          memory = "1024Mi"
        }
      }
      ports {
        container_port = 8000
      }
      env {
        name  = "DJANGO_SETTINGS_MODULE"
        value = "config.settings.production"
      }
      env {
        name  = "DJANGO_SECRET_KEY"
        value = var.django_secret_key
      }
      env {
        name  = "ALLOWED_HOSTS"
        value = var.allowed_hosts
      }
      env {
        name  = "DATABASE_URL"
        value = local.effective_database_url
      }
      dynamic "volume_mounts" {
        for_each = var.provision_postgres ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
      startup_probe {
        http_get {
          path = "/api/v1/health/"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
      }
      liveness_probe {
        http_get {
          path = "/api/v1/health/"
          port = 8000
        }
        period_seconds    = 15
        failure_threshold = 3
      }
    }
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = 10
    }
  }
}

resource "google_cloud_run_service_iam_member" "public_access" {
  location = google_cloud_run_v2_service.app.location
  service  = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value       = google_cloud_run_v2_service.app.uri
  description = "Public URL of the deployed Cloud Run service (e.g. https://fuel-route-optimizer-xyz.a.run.app)"
}

output "cloud_sql_connection_name" {
  value       = var.provision_postgres ? google_sql_database_instance.postgres[0].connection_name : "none (SQLite WAL or external DB)"
  description = "Cloud SQL connection name if provisioned from scratch"
}
