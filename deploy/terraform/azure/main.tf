terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
  }
}

provider "azurerm" {
  features {}
}

variable "resource_group_name" {
  type        = string
  default     = "fuel-route-optimizer-rg"
  description = "Azure Resource Group name"
}

variable "location" {
  type        = string
  default     = "eastus"
  description = "Azure region"
}

variable "image" {
  type        = string
  default     = "ghcr.io/aldian/fuel-route-optimizer:latest"
  description = "Container image URI"
}

variable "django_secret_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Cryptographic DJANGO_SECRET_KEY"
}

variable "allowed_hosts" {
  type        = string
  default     = ".azurecontainerapps.io,localhost,127.0.0.1"
  description = "Comma-separated ALLOWED_HOSTS"
}

variable "provision_postgres" {
  type        = bool
  default     = false
  description = "When true, provisions an Azure Database for PostgreSQL Flexible Server from scratch"
}

variable "db_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Administrator password for Azure PostgreSQL Flexible Server"
}

variable "database_url" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Explicit DATABASE_URL when connecting to an external database"
}

resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
}

# Optional: Provision Managed Azure Database for PostgreSQL Flexible Server from scratch
resource "azurerm_postgresql_flexible_server" "postgres" {
  count                  = var.provision_postgres ? 1 : 0
  name                   = "fuel-route-optimizer-pg"
  resource_group_name    = azurerm_resource_group.rg.name
  location               = azurerm_resource_group.rg.location
  version                = "16"
  administrator_login    = "spotter"
  administrator_password = var.db_password
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
}

resource "azurerm_postgresql_flexible_server_database" "app_db" {
  count     = var.provision_postgres ? 1 : 0
  name      = "spotter_fuel"
  server_id = azurerm_postgresql_flexible_server.postgres[0].id
  collation = "en_US.utf8"
  charset   = "utf8"
}

locals {
  effective_database_url = var.provision_postgres ? (
    "postgres://spotter:${var.db_password}@${azurerm_postgresql_flexible_server.postgres[0].fqdn}:5432/spotter_fuel?sslmode=require"
  ) : var.database_url
}

resource "azurerm_container_app_environment" "env" {
  name                = "fuel-optimizer-env"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
}

resource "azurerm_container_app" "app" {
  name                         = "fuel-route-optimizer"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = azurerm_resource_group.rg.name
  revision_mode                = "Single"

  template {
    min_replicas = 0
    max_replicas = 10

    container {
      name   = "fuel-route-optimizer"
      image  = var.image
      cpu    = 1.0
      memory = "2Gi"

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
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

output "fqdn" {
  value       = azurerm_container_app.app.ingress[0].fqdn
  description = "Public FQDN of the Azure Container App"
}
