# Google Kubernetes Engine (GKE Autopilot) + Artifact Registry Terraform Configuration
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
  description = "GCP region for GKE Autopilot cluster"
}

variable "cluster_name" {
  type        = string
  default     = "fuel-route-optimizer-cluster"
  description = "Name of the GKE Autopilot cluster"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "container_api" {
  project            = var.project_id
  service            = "container.googleapis.com"
  disable_on_destroy = false
}

resource "google_container_cluster" "primary" {
  name                = var.cluster_name
  location            = var.region
  enable_autopilot    = true
  deletion_protection = false

  depends_on = [google_project_service.container_api]
}

output "cluster_name" {
  value       = google_container_cluster.primary.name
  description = "Provisioned GKE Autopilot cluster name"
}

output "get_credentials_command" {
  value       = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --region ${var.region} --project ${var.project_id}"
  description = "Command to configure kubectl for the GKE cluster"
}
