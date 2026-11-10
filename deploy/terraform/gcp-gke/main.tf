# Google Kubernetes Engine (GKE Standard Zonal Cluster) + Artifact Registry Terraform Configuration
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
  description = "GCP region"
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "GCP zone for the GKE cluster"
}

variable "cluster_name" {
  type        = string
  default     = "fuel-route-optimizer-cluster"
  description = "Name of the GKE cluster"
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

resource "google_project_service" "artifactregistry_api" {
  project            = var.project_id
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_container_cluster" "primary" {
  name                = var.cluster_name
  location            = var.zone
  initial_node_count  = 1
  deletion_protection = false

  node_config {
    machine_type = "e2-standard-2"
    disk_type    = "pd-standard"
    disk_size_gb = 30
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  depends_on = [google_project_service.container_api]
}

output "cluster_name" {
  value       = google_container_cluster.primary.name
  description = "Provisioned GKE cluster name"
}

output "zone" {
  value       = google_container_cluster.primary.location
  description = "Provisioned GKE cluster zone"
}

output "get_credentials_command" {
  value       = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --zone ${google_container_cluster.primary.location} --project ${var.project_id}"
  description = "Command to configure kubectl for the GKE cluster"
}
