resource "google_service_account" "cicd" {
  account_id   = "tunestack-cicd"
  display_name = "TuneStack CI/CD service account"
  description  = "Used by GitHub Actions to push images and deploy (wired up in phase 6)"
}

resource "google_project_iam_member" "cicd_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.cicd.email}"
}

resource "google_project_iam_member" "cicd_gke_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.cicd.email}"
}
