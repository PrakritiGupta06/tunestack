resource "google_artifact_registry_repository" "tunestack" {
  location      = var.region
  repository_id = "tunestack"
  format        = "DOCKER"
  description   = "Container images for the TuneStack recommender API"

  depends_on = [google_project_service.required]
}
