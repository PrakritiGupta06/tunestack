resource "google_secret_manager_secret" "grafana_admin_password" {
  secret_id = "grafana-admin-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "grafana_admin_password" {
  secret      = google_secret_manager_secret.grafana_admin_password.id
  secret_data = var.grafana_admin_password
}

resource "google_service_account" "grafana_secrets" {
  account_id   = "grafana-secrets-sa"
  display_name = "Grafana Secret Manager access"
  description  = "Read-only access to exactly one secret: Grafana's admin password"
}

# Scoped to this ONE secret specifically, not project-wide secretAccessor
resource "google_secret_manager_secret_iam_member" "grafana_secret_access" {
  secret_id = google_secret_manager_secret.grafana_admin_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.grafana_secrets.email}"
}

# GKE Workload Identity binding: lets a Kubernetes ServiceAccount named
# grafana-secrets-ksa, in the monitoring namespace, impersonate this GCP
# service account -- no downloaded key file, same principle as phase 6,
# different trust boundary (this cluster's own identity pool).
resource "google_service_account_iam_member" "grafana_ksa_binding" {
  service_account_id = google_service_account.grafana_secrets.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[monitoring/grafana-secrets-ksa]"
}
