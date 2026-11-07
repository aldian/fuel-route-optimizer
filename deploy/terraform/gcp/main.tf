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
  description = "Container image URL"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Cloud Run v2 Service
resource "google_cloud_run_v2_service" "app" {
  name     = "fuel-route-optimizer"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
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
      min_instance_count = 1
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
  description = "Public URL of the deployed Cloud Run service"
}
