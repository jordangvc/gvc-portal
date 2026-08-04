# Auto-deploy setup (one-time)

After this is merged, every push/merge to `master` deploys the portal automatically via GitHub Actions.

## What you need

- Your **Windows PC** (already has `gcloud.cmd` + the repo)
- GitHub CLI (`gh`) — install once if missing: https://cli.github.com/

## One PowerShell paste (creates deploy identity + wires GitHub secret)

```powershell
cd C:\Users\jorda\gvc-portal
git checkout master
git pull

$PROJECT = "gvc-invoice-system"
$ACCOUNT = "hello@greenvalleycontractors.com"
$SA = "gvc-github-deploy"
$EMAIL = "$SA@$PROJECT.iam.gserviceaccount.com"
$KEY = "$env:TEMP\gvc-github-deploy.json"

gcloud.cmd config set account $ACCOUNT
gcloud.cmd config set project $PROJECT

# Create service account (ignore error if it already exists)
gcloud.cmd iam service-accounts create $SA --display-name="GitHub Actions Cloud Run deploy" 2>$null

# Permissions needed for: gcloud run deploy --source .
$roles = @(
  "roles/run.admin",
  "roles/iam.serviceAccountUser",
  "roles/cloudbuild.builds.editor",
  "roles/artifactregistry.admin",
  "roles/storage.admin",
  "roles/serviceusage.serviceUsageConsumer"
)
foreach ($r in $roles) {
  gcloud.cmd projects add-iam-policy-binding $PROJECT --member="serviceAccount:$EMAIL" --role=$r --quiet | Out-Null
}

# Fresh key + store as GitHub Actions secret GCP_SA_KEY
if (Test-Path $KEY) { Remove-Item $KEY -Force }
gcloud.cmd iam service-accounts keys create $KEY --iam-account=$EMAIL

gh auth status 2>$null
if ($LASTEXITCODE -ne 0) { gh auth login }
gh secret set GCP_SA_KEY --repo jordangvc/gvc-portal < $KEY
Remove-Item $KEY -Force

Write-Host "OK: GCP_SA_KEY set. Triggering first deploy..."
gh workflow run deploy-cloud-run.yml --repo jordangvc/gvc-portal --ref master
Write-Host "Watch: https://github.com/jordangvc/gvc-portal/actions"
```

## Immediate deploy WITHOUT waiting for Actions (same as always)

```powershell
cd C:\Users\jorda\gvc-portal
git checkout master
git pull
gcloud.cmd run deploy gvc-invoice --source . --region us-central1 --project gvc-invoice-system --account=hello@greenvalleycontractors.com
```

## Notes

- Do **not** use Mac Terminal until gcloud is installed there; Windows path above is the verified one.
- `gcloud run deploy --source .` updates the image/source only; existing Cloud Run env vars + Secret Manager bindings stay.
- After `GCP_SA_KEY` is set, you never need the deploy command again — merge PRs and wait for the green check on Actions.
