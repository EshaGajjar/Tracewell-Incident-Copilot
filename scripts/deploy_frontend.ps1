# Build the dashboard and deploy hosting + rules. Run from the repo root in PowerShell.
# One-time: npm install -g firebase-tools ; firebase login ; set the project id in .firebaserc
Set-Location frontend
npm install
npm run build
Set-Location ..
firebase deploy --only "hosting,firestore:rules,storage"
