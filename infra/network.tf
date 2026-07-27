resource "google_compute_network" "vpc" {
  name                    = "tunestack-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "tunestack-subnet"
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.10.0.0/20"
  region        = var.region

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}
