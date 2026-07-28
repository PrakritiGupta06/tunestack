resource "google_container_cluster" "autopilot" {
  name     = "tunestack-cluster"
  location = var.region
  
  secret_manager_config {
    enabled = true
  }

  enable_autopilot = true

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  release_channel {
    channel = "REGULAR"
  }

  # Default is `true` in this provider version -- set false so a future
  # `terraform destroy` (e.g. tearing this down between sessions to save
  # cost) doesn't just fail on a safety check.
  deletion_protection = false

  depends_on = [
    google_project_service.required,
    google_compute_subnetwork.subnet,
  ]
}
